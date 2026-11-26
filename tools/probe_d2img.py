import urllib.request, re, time
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
def get(p):
    time.sleep(1.2)
    return urllib.request.urlopen(urllib.request.Request("https://www.dust2.us"+p,headers={"User-Agent":UA,"Accept":"text/html"}),timeout=40).read().decode("utf-8","replace")
r=get("/results")
links=list(dict.fromkeys(re.findall(r'href="(/matches/\d+/[^"]+)"',r)))[:3]
print("матчи:",links)
for l in links:
    h=get(l)
    imgs=re.findall(r"<img[^>]+>",h)
    print("\n==",l,"картинок",len(imgs))
    for t in imgs:
        if re.search(r"player|bodyshot|playerbody|headshot|/player",t,re.I): print("  P",t[:220])
    srcs=set(re.findall(r'src="(https?://[^"/]+)/',h)); print("  хосты:",srcs)
    print("  пример:", [t[:160] for t in imgs[:8]])
for p in ["/player/7998/s1mple","/players"]:
    try: h=get(p); print(p,"OK",len(h), re.findall(r"bodyshot[^\"']*",h)[:2])
    except Exception as e: print(p,"ERR",e)
