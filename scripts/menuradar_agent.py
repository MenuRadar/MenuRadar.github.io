import os,re,json,html
from urllib.parse import urlparse,parse_qs
from pathlib import Path
from io import BytesIO
import requests
from PIL import Image
from html.parser import HTMLParser

BASE="https://menuradar.github.io"

def esc(s): return html.escape(str(s or ""),quote=True)
def slugify(s): return re.sub(r"[^a-z0-9]+","-",str(s or "").lower()).strip("-")[:90]

class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.title=""; self.in_title=False; self.ignored=0; self.heading_tag=None
    def handle_starttag(self,tag,attrs):
        if tag in ("script","style","noscript","template"): self.ignored+=1; return
        if tag=="title": self.in_title=True
        if tag in ("h2","h3","h4"):
            self.heading_tag=tag
            self.parts.append("\n__MR_SECTION_HEADING__")
        if tag in ("p","div","li","h1","h2","h3","h4","tr","td","th","dt","dd","section","article","br","header","main","footer","nav","aside"): self.parts.append("\n")
    def handle_endtag(self,tag):
        if tag in ("script","style","noscript","template"):
            if self.ignored: self.ignored-=1
            return
        if tag=="title": self.in_title=False
        if tag in ("h2","h3","h4"): self.heading_tag=None
        if tag in ("p","div","li","h1","h2","h3","h4","tr","section","article","header","main","footer","nav","aside"): self.parts.append("\n")
    def handle_data(self,data):
        if self.ignored: return
        t=re.sub(r"\s+"," ",data).strip()
        if not t: return
        self.parts.append(t)
        if self.in_title: self.title+=t

def write_agent_preview(status, data, ims=None, done=False):
    payload=dict(data or {})
    payload["status"]=status
    payload["done"]=done
    payload["updated_at"]=__import__("datetime").datetime.utcnow().isoformat()+"Z"
    payload["html"]=render(payload, ims or [])
    Path("admin/agent-preview.json").write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")
    # Preview is committed once by the workflow after the agent finishes.
    # Do not push from inside the Python process; concurrent pushes can race with
    # the workflow's final commit and leave the admin UI showing stale state.

def source():
    s=os.environ.get("AGENT_SOURCE","").strip()
    u=os.environ.get("AGENT_SOURCE_URL","").strip()
    if u:
        r=requests.get(u,headers={"User-Agent":"MenuRadar-Free-Agent/1.0"},timeout=45); r.raise_for_status()
        p=TextParser(); p.feed(r.text)
        s+="\n"+(p.title or "")+"\n"+"\n".join(p.parts)
    if not s: raise RuntimeError("Source text or URL required")
    # Preserve source line boundaries; collapse spaces/tabs only inside each line.
    return "\n".join(re.sub(r"[ \t]+"," ",line).strip() for line in s.splitlines() if line.strip())

def clean_lines(src):
    out=[]
    skip_exact={"menu","home","login","search","order now","skip to content","privacy policy","terms",
                "brands","compare","near me","price changes","blog","alerts","sign in","locations",
                "all","save","updated weekly","avg. item price","today","us","menuprice"}
    skip_contains=["skip to content","privacy policy","terms of use","sign in","log in",
                   "order now","price changes","near me","updated weekly","avg. item price","prices are sourced","available at around","browse the latest","find the cheapest pick"]
    for raw in src.splitlines():
        x=re.sub(r"[ \t]+"," ",raw).strip()
        if not x: continue
        if x.startswith("__MR_SECTION_HEADING__") and not x[len("__MR_SECTION_HEADING__"):].strip(): continue
        low=x.lower().strip()
        if low in skip_exact or any(p in low for p in skip_contains): continue
        if x.startswith(("self.__next_f.push","window.__","(()=>","(function(")): continue
        if re.fullmatch(r"[\d\s/()·•|,-]+",x): continue
        out.append(x)
    dedup=[]
    for x in out:
        if not dedup or x != dedup[-1]: dedup.append(x)
    return dedup

def price(text):
    m=re.search(r"(?:(?:[$£€]|R[$])\s*\d+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?\s*(?:USD|GBP|EUR|BRL|AUD))",text,re.I)
    return m.group(0) if m else ""

def infer_country(src,url=""):
    u=(url or "").lower()
    for marker,country in [("/france/","france"),("/uk/","uk"),("/brazil/","brazil"),("/australia/","australia"),("/us/","usa"),("/usa/","usa")]:
        if marker in u: return country
    s=src.lower()
    if re.search(r"\b(brazil|brasil|brl|r[$])\b",s): return "brazil"
    if re.search(r"\b(france|french|eur|€)\b",s): return "france"
    if re.search(r"\b(australia|australian|aud)\b",s): return "australia"
    if re.search(r"\b(uk|united kingdom|britain|british|england|gbp|£)\b",s): return "uk"
    return "usa"

def gemini(src):
    # AI is used only for metadata/SEO fields. Menu wording is never rewritten by AI.
    key=os.environ.get("GEMINI_API_KEY","").strip()
    if not key: return None
    prompt="""Return ONLY valid JSON for restaurant metadata. Do NOT extract or rewrite menu items.
Use the source only to identify brand, location/address, country and SEO metadata.
Do not invent facts or prices.
Return exactly:
{"brand":"","location":"","address":"","country":"usa|uk|france|brazil|australia","slug":"","keyword":"","title":"","metaDescription":"","intro":"","relatedQueries":[],"imageQueries":[]}
Source:
"""+src
    models=[]
    preferred=os.environ.get("GEMINI_MODEL","").strip()
    if preferred: models.append(preferred)
    models += ["gemini-2.5-flash","gemini-2.5-flash-lite","gemini-2.0-flash","gemini-2.0-flash-lite"]
    seen=set()
    for model in models:
        if not model or model in seen: continue
        seen.add(model)
        try:
            url="https://generativelanguage.googleapis.com/v1beta/models/"+model+":generateContent?key="+key
            payload={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"responseMimeType":"application/json","temperature":0.1,"maxOutputTokens":4096}}
            r=requests.post(url,json=payload,timeout=120)
            if not r.ok:
                print("Gemini model",model,"returned",r.status_code,r.text[:500]); continue
            j=r.json(); t=j["candidates"][0]["content"]["parts"][0]["text"]; data=json.loads(t)
            if isinstance(data,dict) and data.get("brand"):
                return data
        except Exception as e:
            print("Gemini model",model,"failed:",e)
    return None

def is_section_heading(x):
    marker="__MR_SECTION_HEADING__"
    if x.startswith(marker):
        return bool(x[len(marker):].strip())
    if price(x) or len(x)>85: return False
    low=x.strip().lower()
    known={"breakfast","chicken","burgers","burger","meals","sandwiches","sides","snacks","desserts",
           "drinks","beverages","coffee","bowls","combos","family","kids","appetizers","salads","wraps",
           "pizza","pasta","popular items","menu","entrees","shareables","sauces","condiments","cookies",
           "cakes","ice cream","featured","limited time","value","deals","boxes","tenders","wings"}
    return low in known

def exact_sections(lines):
    sections=[]; current=None; pending=[]
    def new_section(name):
        nonlocal current
        name=re.sub(r"^__MR_SECTION_HEADING__","",name or "").strip()
        current={"name":name or "Menu","icon":"🍽️",
                 "description":"Menu information reproduced from the supplied source.","items":[]}
        sections.append(current)
    def flush():
        nonlocal pending
        if current is not None and pending and not current["items"]:
            useful=[x for x in pending if len(x)>=3]
            if useful: current.setdefault("source_lines",[]).extend(useful[-4:])
        pending=[]
    for x in lines:
        if x.startswith("__MR_SECTION_HEADING__"):
            flush()
            heading=x[len("__MR_SECTION_HEADING__"):].strip()
            if heading: new_section(heading)
            continue
        if is_section_heading(x):
            flush(); new_section(x); continue
        p=price(x)
        if p:
            if current is None: new_section("Menu")
            recent=[z for z in pending if len(z)>=2][-4:]
            pending=[]
            non_price=[z for z in recent if not price(z)]
            name=non_price[-1] if non_price else x
            # Never turn a price-only line or scraped UI icon/label into a menu item.
            if name.strip()==x.strip() and (re.fullmatch(r"[£€$R]?\s*\d+(?:[.,]\d{1,2})?\s*(?:USD|GBP|EUR|BRL|AUD)?", name, re.I) or re.fullmatch(r"[£€$R]?\s*\d+(?:[.,]\d{1,2})?\s*[–—-]\s*[£€$R]?\s*\d+(?:[.,]\d{1,2})?", name, re.I) or name.strip().startswith(("🏷","🔔","⭐"))):
                continue
            if name.lower() in {"save","avg. item price","updated weekly","alert","alerts"}: continue
            item={"name":name,"price":p,"note":"","source_lines":recent or [x]}
            if x not in item["source_lines"]: item["source_lines"].append(x)
            current["items"].append(item)
            continue
        x=x.replace("__MR_SECTION_HEADING__","").strip()
        if x: pending.append(x)
    flush()
    cleaned=[]; seen_sections=set()
    for s in sections:
        key=re.sub(r"\s+"," ",s.get("name","").lower()).strip()
        if key in seen_sections and not s.get("items"): continue
        seen_sections.add(key)
        seen_items=set(); items=[]
        for i in s.get("items",[]):
            ik=(i.get("name","").strip().lower(),i.get("price","").strip())
            if ik in seen_items: continue
            seen_items.add(ik); items.append(i)
        s["items"]=items
        if items or s.get("source_lines"): cleaned.append(s)
    return cleaned

def source_slug_hint(source_url=""):
    if not source_url: return ""
    try:
        q=parse_qs(urlparse(source_url).query)
        branch=(q.get("branch") or [""])[0].strip().lower()
        if branch:
            branch=re.sub(r"-[a-f0-9]{8}$","",branch)
            return slugify(branch)
        path=urlparse(source_url).path.rstrip("/")
        tail=path.split("/")[-1] if path else ""
        if tail and tail not in {"menu","menus","restaurant"}:
            return slugify(tail)
    except Exception:
        pass
    return ""

def build_data(src,source_url=""):
    lines=clean_lines(src); blob=" ".join(lines); country=infer_country(blob,source_url); brand=""
    for x in lines[:160]:
        m=re.search(r"\b(kfc|mcdonald'?s|burger king|starbucks|subway|wendy'?s|taco bell|pizza hut|domino'?s|chipotle|popeyes)\b",x,re.I)
        if m: brand=m.group(0).replace("’","'"); break
    if not brand and lines: brand=re.sub(r"\s+(menu|prices?|& prices?).*$","",lines[0],flags=re.I).strip()
    brand=brand or "Restaurant"
    loc=""
    for x in lines[:200]:
        if len(x)<140 and re.search(r",|\b(?:road|rd|street|st|avenue|ave|drive|dr|lane|ln|boulevard|blvd)\b",x,re.I) and x.lower()!=brand.lower():
            loc=x; break
    keyword=(brand+" menu").strip()
    location_hint=source_slug_hint(source_url)
    if location_hint and brand.lower() not in location_hint:
        location_hint=slugify(brand+"-"+location_hint)
    slug=location_hint or (slugify(brand+"-"+slugify(loc)) if loc else slugify(keyword))
    sections=exact_sections(lines)
    if not sections:
        sections=[{"name":"Source Menu","icon":"🍽️","description":"Menu information reproduced from the supplied source.","items":[{"name":x,"price":"","note":"","source_lines":[x]} for x in lines]}]
    queries=[brand+" menu",brand+" menu prices",brand+" restaurant menu"]
    return {"brand":brand,"location":loc,"address":"","country":country,"slug":slug,"keyword":keyword,
            "title":brand+" Menu & Prices | MenuRadar",
            "metaDescription":f"{keyword}, menu items and source-based pricing. Prices and availability may vary by location and ordering channel.",
            "intro":f"Explore the {keyword} using the supplied menu source. Menu wording and listed information are preserved while the page is organized into readable sections.",
            "sections":sections,"relatedQueries":queries,"imageQueries":queries}

def get_images(queries,slug,n):
    out=[]
    for q in queries[:n*2]:
        try: r=requests.get("https://api.openverse.org/v1/images/",params={"q":q,"page_size":5,"license":"cc0,by,by-sa,pdm"},headers={"User-Agent":"MenuRadar-Free-Agent/1.0"},timeout=45)
        except: continue
        if not r.ok: continue
        for p in r.json().get("results",[]):
            src=p.get("thumbnail") or p.get("url")
            if not src: continue
            try:
                raw=requests.get(src,timeout=45).content; im=Image.open(BytesIO(raw)).convert("RGB"); w,h=im.size; target=16/9; ratio=w/h
                if ratio>target: nw=int(h*target); x=(w-nw)//2; im=im.crop((x,0,x+nw,h))
                elif ratio<target: nh=int(w/target); y=(h-nh)//2; im=im.crop((0,y,w,y+nh))
                im.thumbnail((1400,788),Image.Resampling.LANCZOS)
                path=Path("assets/agent")/slug/f"{len(out)+1}.webp"; path.parent.mkdir(parents=True,exist_ok=True); im.save(path,"WEBP",quality=84)
                out.append({"path":"/"+str(path).replace("\\","/"),"alt":q+" menu photo","caption":"Photo by "+str(p.get("creator") or "Openverse contributor")+" via Openverse — "+str(p.get("license") or "open license")}); break
            except: continue
        if len(out)>=n: break
    return out

def menu_fingerprint(d):
    """Fingerprint the actual extracted menu, independent of source URL or slug."""
    rows=[]
    for s in d.get("sections",[]):
        for i in s.get("items",[]):
            name=re.sub(r"\s+"," ",str(i.get("name") or "").strip().lower())
            p=re.sub(r"\s+"," ",str(i.get("price") or "").strip().lower())
            if name: rows.append(name+"|"+p)
    rows=sorted(set(rows))
    if len(rows)<8: return ""
    import hashlib
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()

def _menu_rows_from_html(markup):
    rows=[]
    for block in re.findall(r'<article\\s+class=["'][^"']*menu-card[^"']*["'][^>]*>(.*?)</article>',markup,re.I|re.S):
        nm=re.search(r'<h3[^>]*>(.*?)</h3>',block,re.I|re.S)
        pr=re.search(r'<span[^>]*class=["'][^"']*menu-price[^"']*["'][^>]*>(.*?)</span>',block,re.I|re.S)
        if nm:
            name=re.sub(r"<[^>]+>"," ",nm.group(1))
            price_text=re.sub(r"<[^>]+>"," ",pr.group(1)) if pr else ""
            name=re.sub(r"\\s+"," ",html.unescape(name).strip().lower())
            price_text=re.sub(r"\\s+"," ",html.unescape(price_text).strip().lower())
            if name: rows.append((name,price_text))
    return sorted(set(rows))

def menu_name_fingerprint(d):
    rows=[]
    for s in d.get("sections",[]):
        for i in s.get("items",[]):
            name=re.sub(r"\\s+"," ",str(i.get("name") or "").strip().lower())
            if name: rows.append(name)
    rows=sorted(set(rows))
    if len(rows)<8: return ""
    import hashlib
    return hashlib.sha256("\\n".join(rows).encode("utf-8")).hexdigest()

def html_menu_name_fingerprint(markup):
    rows=sorted(set(name for name,_ in _menu_rows_from_html(markup)))
    if len(rows)<8: return ""
    import hashlib
    return hashlib.sha256("\\n".join(rows).encode("utf-8")).hexdigest()

def html_menu_fingerprint(markup):
    """Read fingerprints from newer articles, or reconstruct older menu-card HTML."""
    m=re.search(r"menuradar-menu-fingerprint:\s*([0-9a-f]{64})",markup,re.I)
    if m: return m.group(1).lower()
    rows=_menu_rows_from_html(markup)
    if len(rows)<8: return ""
    import hashlib
    return hashlib.sha256("\\n".join(n+"|"+p for n,p in rows).encode("utf-8")).hexdigest()

def render(d,ims):
    source_url=esc(d.get("source_url") or "")
    source_id=esc(source_identity(d.get("source_url") or ""))
    menu_id=esc(menu_fingerprint(d))
    secs=[]
    source_marker=f'<!-- menuradar-source-url: {source_url} -->\\n<!-- menuradar-source-identity: {source_id} -->\\n<!-- menuradar-menu-fingerprint: {menu_id} -->\\n'
    for s in d.get("sections",[]):
        cards=[]
        for i in s.get("items",[]):
            raw_lines=i.get("source_lines") or [i.get("name") or ""]
            raw_html=''.join('<div class="source-line">'+esc(line)+'</div>' for line in raw_lines if line)
            cards.append('<article class="menu-card"><div class="menu-card-top"><h3>'+esc(i.get("name"))+'</h3><span class="menu-price">'+esc(i.get("price") or "See local menu")+'</span></div><div class="source-copy">'+raw_html+'</div></article>')
        leftovers=''.join('<div class="source-line source-detail">'+esc(x)+'</div>' for x in s.get("source_lines",[]) if x)
        if not cards and not leftovers: continue
        secs.append('<section class="menu-section"><div class="menu-section-head"><div class="menu-section-title"><span class="category-icon">'+esc(s.get("icon") or "🍽️")+'</span><div><h2>'+esc(s.get("name") or "Menu")+'</h2><p>'+esc(s.get("description") or "Menu information reproduced from the supplied source.")+'</p></div></div><p>'+str(len(s.get("items",[])))+' listed</p></div><div class="menu-grid">'+''.join(cards)+leftovers+'</div></section>')
    pics=[]
    for j,x in enumerate(ims):
        cls="menu-cover" if j==0 else "article-image"; loading="eager" if j==0 else "lazy"
        pics.append('<figure class="'+cls+'"><img src="'+esc(x["path"])+'" alt="'+esc(x["alt"])+'" loading="'+loading+'" width="1200" height="800"><figcaption>'+esc(x.get("caption") or "")+'</figcaption></figure>')
    c=d.get("country") or "usa"; loc=d.get("location") or ""; brand=d.get("brand") or "Restaurant"; address=d.get("address") or ""; source_url=d.get("source_url") or ""
    rel=d.get("relatedQueries") or [brand+" menu",brand+" menu prices",brand+" popular menu items"]
    rel_html=''.join('<a href="/'+c+'/">'+esc(x)+'</a>' for x in rel[:6] if x)
    cover=pics[0] if pics else ""; inside=''.join(pics[1:])
    facts_html=''.join('<div class="fact"><b>'+esc(a)+'</b><span>'+esc(b)+'</span></div>' for a,b in [("Restaurant",brand),("Location",loc or {"usa":"United States","uk":"United Kingdom","france":"France","brazil":"Brazil","australia":"Australia"}.get(c,"Location varies")),("Address",address or "Location varies")])
    source_link=('<a class="source-link" href="'+esc(source_url)+'" target="_blank" rel="nofollow noopener">View original menu source ↗</a>' if source_url else "")
    notice='<div class="menu-notice"><span>ℹ️</span><div><b>Source-faithful menu</b><span>The menu wording and listed source lines are preserved. Prices and availability can still vary by location, date and ordering channel.</span></div>'+source_link+'</div>'
    faq='<section class="section faq"><h2>About '+esc(brand)+' menu prices</h2><details><summary>Do '+esc(brand)+' menu prices vary by location?</summary><p>Yes. Prices and availability can vary by restaurant location, ordering channel and date.</p></details><details><summary>How was this menu page created?</summary><p>The supplied source was read and its menu lines were preserved; MenuRadar changes the presentation, not the source wording.</p></details></section>'
    return source_marker + f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(d.get("title"))}</title><meta name="description" content="{esc(d.get("metaDescription"))}"><meta name="robots" content="index,follow"><link rel="canonical" href="{BASE}/{esc(c)}/{esc(d.get("slug"))}/"><link rel="stylesheet" href="/styles.css"><style>.source-link{{display:inline-flex;margin-top:10px;font-weight:700;text-decoration:none}}.source-copy{{margin-top:10px}}.source-line{{font-size:.94rem;line-height:1.55;margin:3px 0;color:#344054}}.source-detail{{grid-column:1/-1;padding:12px 14px;background:#f8fafc;border-radius:10px}}.menu-card{{min-height:0}}.menu-card h3{{margin:0}}.menu-grid{{align-items:start}}</style></head><body><header class="site-header"><a class="logo" href="/">Menu<span>Radar</span></a></header><main><section class="menu-hero"><div class="menu-hero-inner"><p class="eyebrow">{esc(c.upper())} RESTAURANT MENU</p><h1>{esc(d.get("keyword"))}{(" — "+esc(loc)) if loc else ""}</h1><p class="lead">{esc(d.get("intro"))}</p>{cover}</div></section><div class="menu-layout"><div class="menu-main">{notice}<div class="facts">{facts_html}</div>{''.join(secs)}{inside}<section class="section" style="padding:55px 0 10px"><h2>Related Menu Searches</h2><div class="related">{rel_html}</div><p class="disclaimer">MenuRadar presents source-based menu information. Confirm current prices and availability with the local restaurant.</p></section></div></div>{faq}</main><footer><div class="footer-inner"><b>MenuRadar</b><span>Restaurant Menus, Prices & More</span></div></footer></body></html>'''

def validate_for_publish(d, rendered_html, path):
    keyword=(d.get("keyword") or "").strip().lower()
    brand=(d.get("brand") or "").strip().lower()
    if not keyword or not brand:
        raise RuntimeError("SEO validation failed: brand or primary keyword is empty")
    visible=html.unescape(re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",rendered_html))).lower()
    if visible.count(keyword) < 3:
        raise RuntimeError(f"SEO validation failed: primary keyword must appear at least 3 times; found {visible.count(keyword)}")
    if visible.count(brand) < 3:
        raise RuntimeError(f"SEO validation failed: brand must appear at least 3 times; found {visible.count(brand)}")
    canonical=f'{BASE}/{d.get("country")}/{d.get("slug")}/'
    if canonical not in rendered_html:
        raise RuntimeError("SEO validation failed: canonical URL does not match output path")
    if not d.get("metaDescription"):
        raise RuntimeError("SEO validation failed: meta description is empty")
    return True

def source_identity(source_url):
    """Stable identity for the supplied source. Branch hash changes are ignored."""
    u=(source_url or "").strip().rstrip("/")
    if not u: return ""
    try:
        parsed=urlparse(u)
        q=parse_qs(parsed.query)
        branch=(q.get("branch") or [""])[0].strip().lower()
        if branch:
            branch=re.sub(r"-[a-f0-9]{8}$","",branch)
            return parsed.netloc.lower()+parsed.path.rstrip("/").lower()+"?branch="+branch
        return parsed.netloc.lower()+parsed.path.rstrip("/").lower()
    except Exception:
        return u.lower()

def find_existing_article(path, source_url):
    p=Path(path)
    if p.exists(): return str(p)
    target=(source_url or "").strip().rstrip("/")
    identity=source_identity(source_url)
    if not target and not identity: return ""
    for candidate in Path(".").glob("**/index.html"):
        try:
            html=candidate.read_text(encoding="utf-8",errors="ignore")
            if identity and ("menuradar-source-identity: "+identity) in html:
                return str(candidate)
            if target and target in html:
                return str(candidate)
            # Backward compatibility: inspect source links from older Agent articles
            # and compare their normalized source identity.
            if identity:
                for found in re.findall(r'https?://[^"\\s<>]+', html, re.I):
                    if source_identity(found) == identity:
                        return str(candidate)
        except Exception:
            pass
    return ""


def find_existing_menu_by_content(menu_id, menu_name_id=""):
    """Find same menu even when source URL, slug, or prices differ."""
    if not menu_id and not menu_name_id: return ""
    for candidate in Path(".").glob("**/index.html"):
        try:
            markup=candidate.read_text(encoding="utf-8",errors="ignore")
            if menu_id and html_menu_fingerprint(markup) == menu_id:
                return str(candidate)
            if menu_name_id and html_menu_name_fingerprint(markup) == menu_name_id:
                return str(candidate)
        except Exception:
            pass
    return ""

def main():
    heartbeat={"brand":"MenuRadar AI Autopilot","keyword":"Reading source…","title":"MenuRadar AI Autopilot — Processing","metaDescription":"Reading the supplied restaurant menu source line by line.","intro":"Source received. MenuRadar is preserving the supplied wording and organizing it into a readable menu design.","country":"usa","slug":"agent-processing","sections":[],"relatedQueries":[]}
    write_agent_preview("📖 Source read started — preserving menu wording line by line…",heartbeat,[],False)
    src=source()
    source_url=os.environ.get("AGENT_SOURCE_URL","").strip()
    local=build_data(src,source_url)
    # Do not block publishing on Gemini. Source extraction is deterministic and complete.
    # Gemini is optional metadata enrichment only and is intentionally skipped in the
    # publishing path so a slow/expired API request can never leave Autopilot hanging.
    d=local
    d["source_url"]=os.environ.get("AGENT_SOURCE_URL","").strip()
    d["country"]=d.get("country") if d.get("country") in {"usa","uk","france","brazil","australia"} else "usa"
    d["slug"]=slugify(d.get("slug") or (d.get("brand","restaurant")+"-menu"))
    d["keyword"]=d.get("keyword") or (d.get("brand","Restaurant")+" menu")
    d["imageQueries"]=d.get("imageQueries") or [d["keyword"],d.get("brand","restaurant")+" food"]
    item_count=sum(len(x.get("items") or []) for x in d.get("sections",[]))
    line_count=sum(len(x.get("source_lines") or [])+sum(len(i.get("source_lines") or []) for i in x.get("items",[])) for x in d.get("sections",[]))
    write_agent_preview(f"✅ Source read complete — {item_count} menu items / {line_count} source lines preserved. Designing article…",d,[],False)
    ims=get_images(d["imageQueries"],d["slug"],max(1,min(int(os.environ.get("MAX_IMAGES","4")),6)))
    write_agent_preview(f"Images processed: {len(ims)} — source-faithful menu design is being assembled…",d,ims,False)
    path=f'{d["country"]}/{d["slug"]}/index.html'
    edit_existing=os.environ.get("EDIT_EXISTING","false").strip().lower() in ("1","true","yes","on")
    existing=find_existing_article(path,d.get("source_url",""))
    menu_id=menu_fingerprint(d)
    menu_name_id=menu_name_fingerprint(d)
    same_menu=find_existing_menu_by_content(menu_id, menu_name_id)
    if same_menu and not existing:
        d["status"]="Already published — same menu blocked"
        write_agent_preview("⚠️ This menu is already published at "+same_menu+". Agent blocked the new article even though the source URL is different.",d,[],True)
        print(json.dumps({"status":"duplicate_menu","existing_path":same_menu,"action":"skipped"},ensure_ascii=False))
        return
    if same_menu and existing and same_menu != existing:
        existing=same_menu
    if existing and not edit_existing:
        d["status"]="Already published — duplicate blocked"
        write_agent_preview("⚠️ This article is already published at "+existing+". Agent did not publish it again. Enable “Edit existing article” when you want to update it.",d,[],True)
        print(json.dumps({"status":"duplicate","existing_path":existing,"action":"skipped"},ensure_ascii=False))
        return
    # Editing an existing source must update its current path, not create a second slug.
    if existing and edit_existing:
        path=existing.replace("\\","/")
        if path.endswith("/index.html"):
            d["slug"]=path[:-len("/index.html")].split("/",1)[-1]
    rendered=render(d,ims)
    validate_for_publish(d,rendered,path)
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(rendered,encoding="utf-8")
    h=Path("index.html");s=h.read_text(encoding="utf-8");href="/"+path.rsplit("/index.html",1)[0]+"/";card=f'<a href="{href}"><strong>🍽️ {esc(d["title"])}</strong><br><small>MenuRadar restaurant/menu guide</small></a>\n'
    if href not in s:s=s.replace('<div class="topic-grid">','<div class="topic-grid">\n'+card,1)
    h.write_text(s,encoding="utf-8")
    sm=Path("sitemap.xml");s=sm.read_text(encoding="utf-8");url=BASE+"/"+path.rsplit("/index.html",1)[0]+"/"
    if url not in s:sm.write_text(s.replace("</urlset>",f"  <url><loc>{url}</loc></url>\n</urlset>"),encoding="utf-8")
    write_agent_preview("Published — source-faithful article, homepage and sitemap updated.",d,ims,True)
    print(json.dumps({"path":path,"images":len(ims),"keyword":d["keyword"],"items":item_count,"source_lines":line_count,"mode":"source-faithful-local-extraction"},ensure_ascii=False))

if __name__=="__main__": main()
