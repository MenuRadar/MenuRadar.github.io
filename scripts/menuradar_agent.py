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
    prompt="""You are MenuRadar's menu extraction engine. Return ONLY valid JSON.
This is a COMPLETE restaurant menu source. Extract the restaurant metadata and the COMPLETE menu.
CRITICAL DATA RULES:
- Preserve EVERY identifiable menu category and EVERY menu item in source order.
- Do NOT summarize, sample, choose popular items, deduplicate, or omit items.
- For every item return ONLY its exact source name and the displayed/current menu price when present.
- Do not copy the long marketing descriptions from the source into item notes.
- If the same item appears in multiple categories, keep each source occurrence/category.
- Keep category names exactly as supported by the source where practical.
- Use empty string when a price is not available.
- Never invent prices.
- Compact output is required so the complete menu fits in the response.
Return this shape:
{"brand":"","location":"","address":"","country":"usa|uk|france|brazil|australia","slug":"","keyword":"","title":"","metaDescription":"","intro":"","sections":[{"name":"","icon":"","description":"","items":[{"name":"","price":"","note":""}]}],"relatedQueries":[],"imageQueries":[]}
The sections array must contain ALL source categories and the items arrays must contain ALL source items.
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
            payload={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"responseMimeType":"application/json","temperature":0.1,"maxOutputTokens":65536}}
            r=requests.post(url,json=payload,timeout=240)
            if not r.ok:
                print("Gemini model",model,"returned",r.status_code,r.text[:500]); continue
            j=r.json()
            t=j["candidates"][0]["content"]["parts"][0]["text"]
            data=json.loads(t)
            if not isinstance(data,dict) or not data.get("sections"):
                print("Gemini model",model,"returned incomplete menu JSON"); continue
            total=sum(len(x.get("items") or []) for x in data.get("sections",[]))
            if total < 1:
                print("Gemini model",model,"returned zero items"); continue
            print("Gemini model",model,"extracted",total,"items in",len(data.get("sections",[])),"sections")
            return data
        except Exception as e:
            print("Gemini model",model,"failed:",e)
    print("Gemini unavailable or incomplete; using free local parser fallback.")
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
        cards=[]
        for i in s.get("items",[]):
            note=esc(i.get("note") or "")
            cards.append('<article class="menu-card"><div class="menu-card-top"><h3>'+esc(i.get("name"))+'</h3><span class="menu-price">'+esc(i.get("price") or "See local menu")+'</span></div>'+('<p>'+note+'</p>' if note else '')+'</article>')
        if not cards: continue
        secs.append('<section class="menu-section"><div class="menu-section-head"><div class="menu-section-title"><span class="category-icon">'+esc(s.get("icon") or "🍽️")+'</span><div><h2>'+esc(s.get("name") or "Menu")+'</h2><p>'+esc(s.get("description") or "Menu options and reference pricing.")+'</p></div></div><p>'+str(len(s.get("items",[])))+' listed</p></div><div class="menu-grid">'+''.join(cards)+'</div></section>')
    pics=[]
    for j,x in enumerate(ims):
        cls="menu-cover" if j==0 else "article-image"
        loading="eager" if j==0 else "lazy"
        pics.append('<figure class="'+cls+'"><img src="'+esc(x["path"])+'" alt="'+esc(x["alt"])+'" loading="'+loading+'" width="1200" height="800"><figcaption>'+esc(x.get("caption") or "")+'</figcaption></figure>')
    c=d.get("country") or "usa"; loc=d.get("location") or ""; brand=d.get("brand") or "Restaurant"; address=d.get("address") or ""
    rel=d.get("relatedQueries") or [brand+" menu",brand+" menu prices",brand+" popular menu items"]
    rel_html=''.join('<a href="/'+c+'/">'+esc(x)+'</a>' for x in rel[:6] if x)
    cover=pics[0] if pics else ""; inside=''.join(pics[1:])
    facts_html=''.join('<div class="fact"><b>'+esc(a)+'</b><span>'+esc(b)+'</span></div>' for a,b in [("Restaurant",brand),("Location",loc or "United States"),("Address",address or "Location varies")])
    notice='<div class="menu-notice"><span>ℹ️</span><div><b>Prices and availability can vary</b><span>Menu prices may change by location, date and ordering channel. Use the displayed figures as reference pricing and check the local restaurant for the current total.</span></div></div>'
    faq='<section class="section faq"><h2>About '+esc(brand)+' menu prices</h2><details><summary>Do '+esc(brand)+' menu prices vary by location?</summary><p>Yes. Prices and availability can vary by restaurant location, ordering channel and date.</p></details><details><summary>Is this the complete menu?</summary><p>This page is generated from the supplied source and preserves the menu categories and identifiable items available in that source.</p></details></section>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(d.get("title"))}</title><meta name="description" content="{esc(d.get("metaDescription"))}"><meta name="robots" content="index,follow"><link rel="canonical" href="{BASE}/{esc(c)}/{esc(d.get("slug"))}/"><link rel="stylesheet" href="/styles.css"></head><body><header class="site-header"><a class="logo" href="/">Menu<span>Radar</span></a></header><main><section class="menu-hero"><div class="menu-hero-inner"><p class="eyebrow">{esc(c.upper())} RESTAURANT MENU</p><h1>{esc(d.get("keyword"))}{(" — "+esc(loc)) if loc else ""}</h1><p class="lead">{esc(d.get("intro"))}</p>{cover}</div></section><div class="menu-layout"><div class="menu-main">{notice}<div class="facts">{facts_html}</div>{''.join(secs)}{inside}<section class="section" style="padding:55px 0 10px"><h2>Related Menu Searches</h2><div class="related">{rel_html}</div><p class="disclaimer">MenuRadar uses source-based menu information. Exact prices and availability should be confirmed with the local restaurant.</p></section></div></div>{faq}</main><footer><div class="footer-inner"><b>MenuRadar</b><span>Restaurant Menus, Prices & More</span></div></footer></body></html>'''

def main():
    heartbeat={"brand":"MenuRadar AI Autopilot","keyword":"Analyzing source…","title":"MenuRadar AI Autopilot — Processing","metaDescription":"Gemini is analyzing the supplied restaurant menu source.","intro":"Source received. Gemini is analyzing the restaurant, menu categories, items, prices and SEO structure…","country":"usa","slug":"agent-processing","sections":[],"relatedQueries":[]}
    write_agent_preview("🧠 Gemini source analyze kar raha hai…",heartbeat,[],False)
    src=source()
    local=build_data(src)
    ai=gemini(src)
    d=ai or local
    ai_count=sum(len(x.get("items") or []) for x in d.get("sections",[]))
    local_count=sum(len(x.get("items") or []) for x in local.get("sections",[]))
    if local_count and ai_count < max(1,int(local_count*0.55)):
        print("AI extraction was incomplete:",ai_count,"vs local",local_count,"— keeping local complete extraction.")
        if ai:
            ai["sections"]=local.get("sections",[])
            d=ai
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
