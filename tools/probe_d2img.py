import urllib.request, re, time, html
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
def get(u):
    time.sleep(1.5)
    r=urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":UA}),timeout=40); b=r.read(); return r.status, r.headers.get("Content-Type"), b
st,ct,b=get("https://www.dust2.us/matches/2400579/lynn-vision-vs-heroic")
h=b.decode("utf-8","replace")
tags=[t for t in re.findall(r"<img[^>]+>",h) if "playerbodyshot" in t]
print("тегов",len(tags))
for t in tags[:12]: print(" ",re.search(r'alt="([^"]+)"',t).group(1), re.search(r'class="([^"]*)"',t).group(1) if 'class="' in t else "")
u=html.unescape(re.search(r'src="([^"]+)"',tags[0]).group(1))
for v in [u, u.split("?")[0], re.sub(r"(w|h)=100", r"\g<1>=300", u)]:
    try:
        s,c,bb=get(v); print("OK",s,c,len(bb),v[:120])
    except Exception as e: print("ERR",e,v[:120])
# HLTV player page format for comparison
