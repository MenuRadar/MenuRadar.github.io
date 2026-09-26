import os,re,json,html
from pathlib import Path
from io import BytesIO
import requests
from PIL import Image
BASE="https://menuradar.github.io"
def esc(s): return html.escape(str(s or ""),quote=True)
def slugify(s): return re.sub(r"[^a-z0-9]+","-",str(s or "").lower()).strip("-")[:90]
def source():
 s=os.environ.get("AGENT_SOURCE","").strip();u=os.environ.get("AGENT_SOURCE_URL","").strip()
 if u:
  r=requests.get(u,headers={"User-Agent":"MenuRadar-Agent/1.0"},timeout=45);r.raise_for_status();s+="\nSOURCE URL: "+u+"\n"+r.text[:180000]
 if not s: raise RuntimeError("Source text or URL required")
 return re.sub(r"\s+"," ",s)[:180000]
def ai(src,model):
 prompt="""You are MenuRadar's autonomous restaurant-menu agent. Return ONLY JSON with keys brand,location,address,country,slug,keyword,title,metaDescription,intro,sections,relatedQueries,imageQueries. sections contain name,icon,description,items; items contain name,price,note. Preserve source facts. Never invent exact prices. Write original useful prose and natural SEO. Country must be usa, uk, france, brazil or australia and only if supported. Source:\n"""+src
 r=requests.post("https://api.openai.com/v1/responses",headers={"Authorization":"Bearer "+os.environ["OPENAI_API_KEY"],"Content-Type":"application/json"},json={"model":model,"input":prompt,"store":False,"text":{"format":{"type":"json_object"}}},timeout=180);r.raise_for_status();j=r.json();t=j.get("output_text","")
 if not t:t="".join(c.get("text","") for x in j.get("output",[]) for c in x.get("content",[]) if c.get("type")=="output_text")
 return json.loads(t)
def get_images(queries,slug,n):
 k=os.environ.get("PEXELS_API_KEY","").strip()
 out=[]
 # Pexels when configured; otherwise use Openverse's public image index.
 if not k:
  for q in queries[:n*2]:
   try:r=requests.get("https://api.openverse.org/v1/images/",params={"q":q,"page_size":5},headers={"User-Agent":"MenuRadar-Agent/1.0"},timeout=45)
   except:continue
   if not r.ok:continue
   for p in r.json().get("results",[]):
    src=p.get("thumbnail") or p.get("url")
    if not src:continue
    try:
     raw=requests.get(src,timeout=45).content;im=Image.open(BytesIO(raw)).convert("RGB");w,h=im.size;target=16/9;ratio=w/h
     if ratio>target:nw=int(h*target);x=(w-nw)//2;im=im.crop((x,0,x+nw,h))
     elif ratio<target:nh=int(w/target);y=(h-nh)//2;im=im.crop((0,y,w,y+nh))
     im.thumbnail((1400,788),Image.Resampling.LANCZOS);path=Path("assets/agent")/slug/f"{len(out)+1}.webp";path.parent.mkdir(parents=True,exist_ok=True);im.save(path,"WEBP",quality=84)
     out.append({"path":"/"+str(path).replace("\\","/"),"alt":q+" menu photo","caption":"Photo via Openverse"});break
    except:continue
   if len(out)>=n:break
  return out
 for q in queries[:n*2]:
  try:r=requests.get("https://api.pexels.com/v1/search",headers={"Authorization":k},params={"query":q,"per_page":5,"orientation":"landscape"},timeout=45)
  except:continue
  if not r.ok:continue
  for p in r.json().get("photos",[]):
   try:
    raw=requests.get(p["src"]["large"],timeout=45).content;im=Image.open(BytesIO(raw)).convert("RGB");w,h=im.size;target=16/9;ratio=w/h
    if ratio>target:nw=int(h*target);x=(w-nw)//2;im=im.crop((x,0,x+nw,h))
    elif ratio<target:nh=int(w/target);y=(h-nh)//2;im=im.crop((0,y,w,y+nh))
    im.thumbnail((1400,788),Image.Resampling.LANCZOS);path=Path("assets/agent")/slug/f"{len(out)+1}.webp";path.parent.mkdir(parents=True,exist_ok=True);im.save(path,"WEBP",quality=84)
    out.append({"path":"/"+str(path).replace("\\","/"),"alt":q+" menu photo","caption":"Photo via Pexels"});break
   except:continue
  if len(out)>=n:break
 return out
def render(d,ims):
 secs=[]
 for s in d.get("sections",[]):
  cards="".join(f'<div class="menu-card"><div><h3>{esc(i.get("name"))}</h3><p>{esc(i.get("note"))}</p></div><strong>{esc(i.get("price") or "See local menu")}</strong></div>' for i in s.get("items",[]))
  secs.append(f'<section class="menu-section"><div class="menu-section-head"><span class="menu-icon">{esc(s.get("icon") or "🍽️")}</span><div><h2>{esc(s.get("name") or "Menu")}</h2><p>{esc(s.get("description"))}</p></div></div><div class="menu-grid">{cards}</div></section>')
 pics=[]
 for j,x in enumerate(ims): pics.append(f'<figure class="{"menu-cover" if j==0 else "article-image"}"><img src="{esc(x["path"])}" alt="{esc(x["alt"])}" loading="{"eager" if j==0 else "lazy"}"><figcaption>{esc(x["caption"])}</figcaption></figure>')
 c=d.get("country") or "usa";loc=d.get("location") or "";rel="".join(f"<li>{esc(x)}</li>" for x in d.get("relatedQueries",[]) if x)
 cover=pics[0] if pics else "";inside="".join(pics[1:])
 return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(d.get("title") or d.get("keyword"))}</title><meta name="description" content="{esc(d.get("metaDescription"))}"><meta name="robots" content="index,follow"><link rel="canonical" href="{BASE}/{c}/{esc(d.get("slug"))}/"><link rel="stylesheet" href="/styles.css"><style>.menu-hero{{padding:42px 20px;background:linear-gradient(135deg,#111827,#1f2937);color:#fff}}.menu-hero-inner{{max-width:1100px;margin:auto}}.menu-hero h1{{font-size:clamp(32px,5vw,58px);margin:12px 0}}.menu-cover,.article-image{{margin:28px auto;max-width:980px}}.menu-cover img,.article-image img{{display:block;width:100%;max-height:520px;object-fit:cover;border-radius:20px}}.menu-cover figcaption,.article-image figcaption{{font-size:12px;opacity:.7;margin-top:7px}}.menu-section{{max-width:1100px;margin:34px auto;padding:26px;border:1px solid #e5e7eb;border-radius:20px;background:#fff}}.menu-section-head{{display:flex;gap:16px;align-items:flex-start;margin-bottom:18px}}.menu-icon{{font-size:30px}}.menu-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}.menu-card{{display:flex;justify-content:space-between;gap:15px;padding:18px;border-radius:14px;background:#f8fafc;border:1px solid #e5e7eb}}.menu-card h3{{margin:0 0 5px}}.menu-card p{{margin:0;color:#64748b;font-size:14px}}.menu-card strong{{white-space:nowrap}}</style></head><body><header class="site-header"><a class="logo" href="/">Menu<span>Radar</span></a></header><main><section class="menu-hero"><div class="menu-hero-inner"><p class="eyebrow">{esc(c.upper())} RESTAURANT MENU</p><h1>{esc(d.get("keyword"))}{(" — "+esc(loc)) if loc else ""}</h1><p class="lead">{esc(d.get("intro"))}</p>{cover}</div></section><div class="section"><p>Menu information and prices can vary by location, date and ordering channel. Check the current local menu for final availability and pricing.</p></div>{"".join(secs)}{inside}<section class="section"><h2>Related Menu Searches</h2><ul>{rel}</ul><p><a href="/{c}/">Explore more {c.upper()} restaurant menus →</a></p></section></main><footer><div class="footer-inner"><b>MenuRadar</b><span>Restaurant Menus, Prices & More</span></div></footer></body></html>'''
def main():
 d=ai(source(),os.environ.get("AI_MODEL","gpt-5.6-luna"));d["country"]=d.get("country") if d.get("country") in {"usa","uk","france","brazil","australia"} else "usa";d["slug"]=slugify(d.get("slug") or (d.get("brand","restaurant")+"-"+d.get("location","menu")));d["keyword"]=d.get("keyword") or (d.get("brand","Restaurant")+" menu")
 ims=get_images(d.get("imageQueries") or [d.get("brand","restaurant")+" menu",d.get("brand","restaurant")+" food",d.get("brand","restaurant")+" restaurant"],d["slug"],max(1,min(int(os.environ.get("MAX_IMAGES","4")),6)))
 path=f'{d["country"]}/{d["slug"]}/index.html';p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(render(d,ims),encoding="utf-8")
 h=Path("index.html");s=h.read_text(encoding="utf-8");href="/"+path.rsplit("/index.html",1)[0]+"/";card=f'<a href="{href}"><strong>🍽️ {esc(d.get("title") or d["keyword"])}</strong><br><small>MenuRadar restaurant/menu guide</small></a>\n'
 if href not in s:s=s.replace('<div class="topic-grid">','<div class="topic-grid">\n'+card,1)
 h.write_text(s,encoding="utf-8")
 sm=Path("sitemap.xml");s=sm.read_text(encoding="utf-8");url=BASE+"/"+path.rsplit("/index.html",1)[0]+"/"
 if url not in s:sm.write_text(s.replace("</urlset>",f"  <url><loc>{url}</loc></url>\n</urlset>"),encoding="utf-8")
 print(json.dumps({"path":path,"images":len(ims),"keyword":d["keyword"]},ensure_ascii=False))
if __name__=="__main__":main()
