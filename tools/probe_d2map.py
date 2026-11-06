import re, sys, time, urllib.request
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
def get(p):
    time.sleep(1.2)
    r = urllib.request.Request("https://www.dust2.us" + p, headers={"User-Agent": UA, "Accept": "text/html"})
    return re.sub(r"\s+", " ", urllib.request.urlopen(r, timeout=40).read().decode("utf-8", "replace"))
b = get("/matches/2400006/parivision-vs-natus-vincere")
i = b.find('tabs-tab'); print("TABS", b[i-1500:i+2500])
i = b.find('match-result-table'); print("TABLE", b[i-200:i+5000])
for p in ("/matches/2400007/furia-vs-betboom",):
    c = get(p); i = c.find('class="maps-container"'); j = c.find('match-result-table')
    print("LIVE maps", re.sub(r"<img[^>]*>", "", c[i:i+9000]))
    print("LIVE tabs", re.findall(r'tabs-tab[^>]*>([^<]*)<', c), c.count('match-result-table'))
