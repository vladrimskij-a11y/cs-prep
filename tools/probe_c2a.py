import urllib.request, gzip, re, json
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept-Encoding": "gzip", "Accept": "text/html,application/json"}
def get(u):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30); b = r.read()
        return r.status, r.headers.get("Content-Type"), (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "replace")
    except Exception as e:
        return getattr(e, "code", None), None, str(e)
for u in ["https://cs2analytics.com/", "https://cs2analytics.com/matches", "https://cs2analytics.com/robots.txt", "https://cs2analytics.com/sitemap.xml"]:
    st, ct, h = get(u)
    print("==", u, st, ct, len(h))
    print("   title:", (re.search(r"<title>(.*?)</title>", h, re.S) or [None, ""])[1][:120])
    links = sorted(set(re.findall(r'href="(/[^"#?]{2,80})"', h)))[:60]
    print("   links:", links)
    apis = sorted(set(re.findall(r'["\'](/api/[^"\']{1,80}|https://[a-z0-9.-]*api[a-z0-9.-]*/[^"\']{0,60})', h)))[:30]
    print("   api:", apis)
    js = sorted(set(re.findall(r'src="([^"]+\.js)"', h)))[:10]
    print("   js:", js)
    if "robots" in u or "sitemap" in u: print(h[:1500])
