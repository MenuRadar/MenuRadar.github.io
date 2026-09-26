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
    out=[]
    for x in src.splitlines():
        x=re.sub(r"\s+"," ",x).strip(" -•*\t")
        if x and x.lower() not in {"menu","home","login","search","order now","skip to content"}: out.append(x)
    return out

def price(text):
    m=re.search(r"(?:(?:\$|£|€|R\$)\s*\d+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?\s*(?:USD|GBP|EUR|BRL|AUD))",text,re.I)
    return m.group(0) if m else ""

def infer_country(src):
    s=src.lower()
    if re.search(r"\b(brazil|brasil|brl|r\$)\b",s): return "brazil"
    if re.search(r"\b(france|french|eur|€)\b",s): return "france"
    if re.search(r"\b(australia|australian|aud)\b",s): return "australia"
    if re.search(r"\b(uk|united kingdom|britain|british|england|gbp|£)\b",s): return "uk"
    return "usa"

def gemini(src):
    key=os.environ.get("GEMINI_API_KEY","").strip()
    if not key: return None
    prompt="""Return ONLY valid JSON for a COMPLETE restaurant menu article. Keys: brand,location,address,country,slug,keyword,title,metaDescription,intro,sections,relatedQueries,imageQueries. sections contain name,icon,description,items; items contain name,price,note. CRITICAL: preserve EVERY menu category/section and EVERY identifiable menu item, price, size/variant and note found in the source. Do not summarize, sample, truncate, deduplicate, or replace the menu with only popular items. If the source contains 212 items, the JSON must represent all 212 items. Keep source order where practical. Never invent exact prices; if a price is absent, use an empty price. Use natural SEO only in title/meta/intro and do not alter source facts. Country must be usa, uk, france, brazil or australia. Source:\n"""+src
    models=[]
    preferred=os.environ.get("GEMINI_MODEL","").strip()
    if preferred: models.append(preferred)
    models += ["gemini-3.5-flash-lite","gemini-3.5-flash","gemini-2.5-flash"]
    seen=set()
    for model in models:
        if not model or model in seen: continue
        seen.add(model)
        try:
            url="https://generativelanguage.googleapis.com/v1beta/models/"+model+":generateContent?key="+key
            r=requests.post(url,json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"responseMimeType":"application/json"}},timeout=180)
            if not r.ok:
                print("Gemini model",model,"returned",r.status_code,r.text[:300])
                continue
            j=r.json(); t=j["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(t)
        except Exception as e:
            print("Gemini model",model,"failed:",e)
    print("Gemini unavailable; using free local parser fallback.")
    return None
def build_data(src):
    lines=clean_lines(src); blob=" ".join(lines); country=infer_country(blob); brand=""
    for x in lines[:40]:
        m=re.search(r"\b(kfc|mcdonald'?s|burger king|starbucks|subway|wendy'?s|taco bell|pizza hut|domino'?s|chipotle|popeyes)\b",x,re.I)
        if m: brand=m.group(0).replace("’","'"); break
    if not brand and lines: brand=re.sub(r"\s+(menu|prices?|& prices?).*$","",lines[0],flags=re.I).strip()
    brand=brand or "Restaurant"; loc=""
    for x in lines[:80]:
        if len(x)<120 and re.search(r",|\b(?:road|rd|street|st|avenue|ave|drive|dr|lane|ln|boulevard|blvd)\b",x,re.I) and x.lower()!=brand.lower():
            loc=x; break
    keyword=(brand+" menu").strip(); slug=slugify(keyword)
    section_names=["Breakfast","Chicken","Burgers","Meals","Sandwiches","Sides","Snacks","Desserts","Drinks","Coffee","Popular Items","Menu"]
    sections=[]; current=None
    for x in lines:
        if len(x)>90: continue
        px=price(x); normalized=re.sub(r"\s+"," ",re.sub(r"[:|]+$","",x)).strip()
        is_heading=(not px and any(re.search(r"\b"+re.escape(n)+r"\b",normalized,re.I) for n in section_names) and len(normalized)<70)
        if is_heading:
            current={"name":normalized,"icon":"🍽️","description":"Menu options and reference pricing.","items":[]}; sections.append(current); continue
        if px:
            name=re.sub(r"\s*(?:[-–—|:])?\s*(?:\$|£|€|R\$)?\s*\d+(?:\.\d{1,2})?\s*(?:USD|GBP|EUR|BRL|AUD)?\s*$","",x,flags=re.I).strip(" -–—|:")
            if len(name)<2: continue
            if current is None:
                current={"name":"Menu","icon":"🍽️","description":"Menu items and reference pricing.","items":[]}; sections.append(current)
            current["items"].append({"name":name,"price":px,"note":""})
    sections=[s for s in sections if s["items"]][:20]
    if not sections:
        sections=[{"name":"Menu","icon":"🍽️","description":"Menu options and reference pricing.","items":[{"name":x,"price":price(x),"note":""} for x in lines[:30] if len(x)<90]}]
    queries=[brand+" menu",brand+" restaurant food",brand+" popular menu items"]
    return {"brand":brand,"location":loc,"address":"","country":country,"slug":slug,"keyword":keyword,
            "title":brand+" Menu & Prices | MenuRadar","metaDescription":f"{keyword}, menu items, prices and popular choices. Prices and availability may vary by location and ordering channel.",
            "intro":f"Explore the {keyword} with menu items, categories and reference pricing. Check your local restaurant for current availability and prices.",
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
        cards="".join(f'<div class="menu-card"><div><h3>{esc(i.get("name"))}</h3><p>{esc(i.get("note"))}</p></div><strong>{esc(i.get("price") or "See local menu")}</strong></div>' for i in s.get("items",[]))
        secs.append(f'<section class="menu-section"><div class="menu-section-head"><span class="menu-icon">{esc(s.get("icon") or "🍽️")}</span><div><h2>{esc(s.get("name") or "Menu")}</h2><p>{esc(s.get("description"))}</p></div></div><div class="menu-grid">{cards}</div></section>')
    pics=[]
    for j,x in enumerate(ims):
        cls="menu-cover" if j==0 else "article-image"; loading="eager" if j==0 else "lazy"
        pics.append(f'<figure class="{cls}"><img src="{esc(x["path"])}" alt="{esc(x["alt"])}" loading="{loading}"><figcaption>{esc(x["caption"])}</figcaption></figure>')
    c=d.get("country") or "usa"; loc=d.get("location") or ""; rel="".join(f"<li>{esc(x)}</li>" for x in d.get("relatedQueries",[]) if x); cover=pics[0] if pics else ""; inside="".join(pics[1:])
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(d.get("title"))}</title><meta name="description" content="{esc(d.get("metaDescription"))}"><meta name="robots" content="index,follow"><link rel="canonical" href="{BASE}/{c}/{esc(d.get("slug"))}/"><link rel="stylesheet" href="/styles.css"><style>.menu-hero{{padding:42px 20px;background:linear-gradient(135deg,#111827,#1f2937);color:#fff}}.menu-hero-inner{{max-width:1100px;margin:auto}}.menu-hero h1{{font-size:clamp(32px,5vw,58px);margin:12px 0}}.menu-cover,.article-image{{margin:28px auto;max-width:980px}}.menu-cover img,.article-image img{{display:block;width:100%;max-height:520px;object-fit:cover;border-radius:20px}}.menu-cover figcaption,.article-image figcaption{{font-size:12px;opacity:.7;margin-top:7px}}.menu-section{{max-width:1100px;margin:34px auto;padding:26px;border:1px solid #e5e7eb;border-radius:20px;background:#fff}}.menu-section-head{{display:flex;gap:16px;align-items:flex-start;margin-bottom:18px}}.menu-icon{{font-size:30px}}.menu-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}.menu-card{{display:flex;justify-content:space-between;gap:15px;padding:18px;border-radius:14px;background:#f8fafc;border:1px solid #e5e7eb}}.menu-card h3{{margin:0 0 5px}}.menu-card p{{margin:0;color:#64748b;font-size:14px}}.menu-card strong{{white-space:nowrap}}</style></head><body><header class="site-header"><a class="logo" href="/">Menu<span>Radar</span></a></header><main><section class="menu-hero"><div class="menu-hero-inner"><p class="eyebrow">{esc(c.upper())} RESTAURANT MENU</p><h1>{esc(d.get("keyword"))}{(" — "+esc(loc)) if loc else ""}</h1><p class="lead">{esc(d.get("intro"))}</p>{cover}</div></section><div class="section"><p>Menu information and prices can vary by location, date and ordering channel. Check the current local menu for final availability and pricing.</p></div>{"".join(secs)}{inside}<section class="section"><h2>Related Menu Searches</h2><ul>{rel}</ul><p><a href="/{c}/">Explore more {c.upper()} restaurant menus →</a></p></section></main><footer><div class="footer-inner"><b>MenuRadar</b><span>Restaurant Menus, Prices & More</span></div></footer></body></html>'''

def main():
    heartbeat={"brand":"MenuRadar AI Autopilot","keyword":"Analyzing source…","title":"MenuRadar AI Autopilot — Processing","metaDescription":"Gemini is analyzing the supplied restaurant menu source.","intro":"Source received. Gemini is analyzing the restaurant, menu categories, items, prices and SEO structure…","country":"usa","slug":"agent-processing","sections":[],"relatedQueries":[]}
    write_agent_preview("🧠 Gemini source analyze kar raha hai…",heartbeat,[],False)
    src=source()
    d=gemini(src) or build_data(src)
    d["country"]=d.get("country") if d.get("country") in {"usa","uk","france","brazil","australia"} else "usa"
    d["slug"]=slugify(d.get("slug") or (d.get("brand","restaurant")+"-menu"))
    d["keyword"]=d.get("keyword") or (d.get("brand","Restaurant")+" menu")
    d["imageQueries"]=d.get("imageQueries") or [d["keyword"],d.get("brand","restaurant")+" food"]
    write_agent_preview("Gemini analysis complete — article structure ready. Designing preview…",d,[],False)
    ims=get_images(d["imageQueries"],d["slug"],max(1,min(int(os.environ.get("MAX_IMAGES","4")),6)))
    write_agent_preview(f"Images processed: {len(ims)} — menu design is being assembled…",d,ims,False)
    path=f'{d["country"]}/{d["slug"]}/index.html'
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(render(d,ims),encoding="utf-8")
    h=Path("index.html");s=h.read_text(encoding="utf-8");href="/"+path.rsplit("/index.html",1)[0]+"/";card=f'<a href="{href}"><strong>🍽️ {esc(d["title"])}</strong><br><small>MenuRadar restaurant/menu guide</small></a>\n'
    if href not in s:s=s.replace('<div class="topic-grid">','<div class="topic-grid">\n'+card,1)
    h.write_text(s,encoding="utf-8")
    sm=Path("sitemap.xml");s=sm.read_text(encoding="utf-8");url=BASE+"/"+path.rsplit("/index.html",1)[0]+"/"
    if url not in s:sm.write_text(s.replace("</urlset>",f"  <url><loc>{url}</loc></url>\n</urlset>"),encoding="utf-8")
    write_agent_preview("Published — article, homepage and sitemap updated.",d,ims,True)
    print(json.dumps({"path":path,"images":len(ims),"keyword":d["keyword"],"mode":"gemini-or-free-local-parser"},ensure_ascii=False))

if __name__=="__main__": main()
