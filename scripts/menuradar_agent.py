import os,re,json,html
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
        super().__init__(); self.parts=[]; self.title=""; self.in_title=False
    def handle_starttag(self,tag,attrs):
        if tag=="title": self.in_title=True
        if tag in ("p","div","li","h1","h2","h3","h4","tr","br"): self.parts.append("\n")
    def handle_endtag(self,tag):
        if tag=="title": self.in_title=False
        if tag in ("p","div","li","h1","h2","h3","h4","tr"): self.parts.append("\n")
    def handle_data(self,data):
        t=data.strip()
        if t:
            self.parts.append(t)
            if self.in_title: self.title+=t

def write_agent_preview(status, data, ims=None, done=False):
    payload=dict(data or {})
    payload["status"]=status
    payload["done"]=done
    payload["updated_at"]=__import__("datetime").datetime.utcnow().isoformat()+"Z"
    payload["html"]=render(payload, ims or [])
    Path("admin/agent-preview.json").write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")
    os.system("git add admin/agent-preview.json && git config user.name 'MenuRadar Agent' && git config user.email '41898282+github-actions[bot]@users.noreply.github.com' && git commit -m '🤖 MenuRadar Agent live preview' >/dev/null 2>&1 && git push origin HEAD:main >/dev/null 2>&1 || true")

def source():
    s=os.environ.get("AGENT_SOURCE","").strip()
    u=os.environ.get("AGENT_SOURCE_URL","").strip()
    if u:
        r=requests.get(u,headers={"User-Agent":"MenuRadar-Free-Agent/1.0"},timeout=45); r.raise_for_status()
        p=TextParser(); p.feed(r.text)
        s+="\n"+(p.title or "")+"\n"+"\n".join(p.parts)
    if not s: raise RuntimeError("Source text or URL required")
    return re.sub(r"[ \t]+"," ",s)

def clean_lines(src):
    # Keep the source wording intact. Only normalize repeated whitespace and remove
    # obvious site chrome that is not part of the menu itself.
    out=[]
    skip={"menu","home","login","search","order now","skip to content","privacy policy","terms"}
    for raw in src.splitlines():
        x=re.sub(r"[ \\t]+"," ",raw).strip()
        if not x: continue
        if x.lower() in skip: continue
        out.append(x)
    return out

def price(text):
    m=re.search(r"(?:(?:\\$|£|€|R\\$)\\s*\\d+(?:\\.\\d{1,2})?|\\d+(?:\\.\\d{1,2})?\\s*(?:USD|GBP|EUR|BRL|AUD))",text,re.I)
    return m.group(0) if m else ""

def infer_country(src):
    s=src.lower()
    if re.search(r"\\b(brazil|brasil|brl|r\\$)\\b",s): return "brazil"
    if re.search(r"\\b(france|french|eur|€)\\b",s): return "france"
    if re.search(r"\\b(australia|australian|aud)\\b",s): return "australia"
    if re.search(r"\\b(uk|united kingdom|britain|british|england|gbp|£)\\b",s): return "uk"
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
    models += ["gemini-3.8-flash","gemini-3.7-flash","gemini-3.6-flash","gemini-3.5-flash-lite","gemini-2.5-flash-lite","gemini-2.5-flash"]
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
    if price(x) or len(x)>85: return False
    known=["breakfast","chicken","burgers","burger","meals","sandwiches","sides","snacks","desserts","drinks","beverages","coffee","bowls","combos","family","kids","appetizers","salads","wraps","pizza","pasta","popular items","menu"]
    if any(re.search(r"\\b"+re.escape(k)+r"\\b",x,re.I) for k in known): return True
    # Source headings are usually short, title-like lines.
    words=x.split()
    return 1 <= len(words) <= 6 and x[:1].isupper() and not re.search(r"[.!?]$",x)

def exact_sections(lines):
    sections=[]; current=None; pending=[]; used=set()
    def new_section(name):
        nonlocal current
        current={"name":name or "Menu","icon":"🍽️","description":"Menu information reproduced from the supplied source.","items":[]}
        sections.append(current)
    for idx,x in enumerate(lines):
        if is_section_heading(x):
            if pending:
                # Preserve non-price lines rather than discarding them.
                if current is None: new_section("Menu")
                current.setdefault("source_lines",[]).extend(pending); pending=[]
            new_section(x); used.add(idx); continue
        if price(x):
            if current is None: new_section("Menu")
            item_lines=pending[:] if pending else [x]
            if pending:
                pending=[]
            item={"name":item_lines[0], "price":price(x), "note":"", "source_lines":item_lines}
            if x not in item_lines: item["source_lines"].append(x)
            elif len(item_lines)==1 and item_lines[0]!=x: item["source_lines"].append(x)
            # If the price is on a separate line, keep it exactly as a source line too.
            if x not in item["source_lines"]: item["source_lines"].append(x)
            current["items"].append(item); used.add(idx); continue
        pending.append(x)
    if pending:
        if current is None: new_section("Source content")
        current.setdefault("source_lines",[]).extend(pending)
    # Do not drop sections; every meaningful source line remains represented.
    return [s for s in sections if s.get("items") or s.get("source_lines")]

def build_data(src):
    lines=clean_lines(src); blob=" ".join(lines); country=infer_country(blob); brand=""
    for x in lines[:80]:
        m=re.search(r"\\b(kfc|mcdonald'?s|burger king|starbucks|subway|wendy'?s|taco bell|pizza hut|domino'?s|chipotle|popeyes)\\b",x,re.I)
        if m: brand=m.group(0).replace("’","'"); break
    if not brand and lines: brand=re.sub(r"\\s+(menu|prices?|& prices?).*$","",lines[0],flags=re.I).strip()
    brand=brand or "Restaurant"
    loc=""
    for x in lines[:100]:
        if len(x)<140 and re.search(r",|\\b(?:road|rd|street|st|avenue|ave|drive|dr|lane|ln|boulevard|blvd)\\b",x,re.I) and x.lower()!=brand.lower():
            loc=x; break
    keyword=(brand+" menu").strip(); slug=slugify(keyword)
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

def render(d,ims):
    secs=[]
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
    c=d.get("country") or "usa"; loc=d.get("location") or ""; brand=d.get("brand") or "Restaurant"; address=d.get("address") or ""
    rel=d.get("relatedQueries") or [brand+" menu",brand+" menu prices",brand+" popular menu items"]
    rel_html=''.join('<a href="/'+c+'/">'+esc(x)+'</a>' for x in rel[:6] if x)
    cover=pics[0] if pics else ""; inside=''.join(pics[1:])
    facts_html=''.join('<div class="fact"><b>'+esc(a)+'</b><span>'+esc(b)+'</span></div>' for a,b in [("Restaurant",brand),("Location",loc or "United States"),("Address",address or "Location varies")])
    notice='<div class="menu-notice"><span>ℹ️</span><div><b>Source-faithful menu</b><span>The menu wording and listed source lines are preserved. Prices and availability can still vary by location, date and ordering channel.</span></div></div>'
    faq='<section class="section faq"><h2>About '+esc(brand)+' menu prices</h2><details><summary>Do '+esc(brand)+' menu prices vary by location?</summary><p>Yes. Prices and availability can vary by restaurant location, ordering channel and date.</p></details><details><summary>How was this menu page created?</summary><p>The supplied source was read and its menu lines were preserved; MenuRadar changes the presentation, not the source wording.</p></details></section>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(d.get("title"))}</title><meta name="description" content="{esc(d.get("metaDescription"))}"><meta name="robots" content="index,follow"><link rel="canonical" href="{BASE}/{esc(c)}/{esc(d.get("slug"))}/"><link rel="stylesheet" href="/styles.css"><style>.source-copy{margin-top:10px}.source-line{font-size:.94rem;line-height:1.55;margin:3px 0;color:#344054}.source-detail{grid-column:1/-1;padding:12px 14px;background:#f8fafc;border-radius:10px}.menu-card{min-height:0}.menu-card h3{margin:0}.menu-grid{align-items:start}</style></head><body><header class="site-header"><a class="logo" href="/">Menu<span>Radar</span></a></header><main><section class="menu-hero"><div class="menu-hero-inner"><p class="eyebrow">{esc(c.upper())} RESTAURANT MENU</p><h1>{esc(d.get("keyword"))}{(" — "+esc(loc)) if loc else ""}</h1><p class="lead">{esc(d.get("intro"))}</p>{cover}</div></section><div class="menu-layout"><div class="menu-main">{notice}<div class="facts">{facts_html}</div>{''.join(secs)}{inside}<section class="section" style="padding:55px 0 10px"><h2>Related Menu Searches</h2><div class="related">{rel_html}</div><p class="disclaimer">MenuRadar presents source-based menu information. Confirm current prices and availability with the local restaurant.</p></section></div></div>{faq}</main><footer><div class="footer-inner"><b>MenuRadar</b><span>Restaurant Menus, Prices & More</span></div></footer></body></html>'''

def main():
    heartbeat={"brand":"MenuRadar AI Autopilot","keyword":"Reading source…","title":"MenuRadar AI Autopilot — Processing","metaDescription":"Reading the supplied restaurant menu source line by line.","intro":"Source received. MenuRadar is preserving the supplied wording and organizing it into a readable menu design.","country":"usa","slug":"agent-processing","sections":[],"relatedQueries":[]}
    write_agent_preview("📖 Source read started — preserving menu wording line by line…",heartbeat,[],False)
    src=source()
    local=build_data(src)
    ai=gemini(src)
    if ai:
        for k in ["brand","location","address","country","slug","keyword","title","metaDescription","intro","relatedQueries","imageQueries"]:
            if ai.get(k): local[k]=ai[k]
    d=local
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
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(render(d,ims),encoding="utf-8")
    h=Path("index.html");s=h.read_text(encoding="utf-8");href="/"+path.rsplit("/index.html",1)[0]+"/";card=f'<a href="{href}"><strong>🍽️ {esc(d["title"])}</strong><br><small>MenuRadar restaurant/menu guide</small></a>\n'
    if href not in s:s=s.replace('<div class="topic-grid">','<div class="topic-grid">\n'+card,1)
    h.write_text(s,encoding="utf-8")
    sm=Path("sitemap.xml");s=sm.read_text(encoding="utf-8");url=BASE+"/"+path.rsplit("/index.html",1)[0]+"/"
    if url not in s:sm.write_text(s.replace("</urlset>",f"  <url><loc>{url}</loc></url>\n</urlset>"),encoding="utf-8")
    write_agent_preview("Published — source-faithful article, homepage and sitemap updated.",d,ims,True)
    print(json.dumps({"path":path,"images":len(ims),"keyword":d["keyword"],"items":item_count,"source_lines":line_count,"mode":"source-faithful-local-extraction"},ensure_ascii=False))

if __name__=="__main__": main()
