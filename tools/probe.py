import re, urllib.request, gzip, time
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
def get(u):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Encoding": "gzip"}), timeout=30) as r:
            b = r.read(); return (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "replace")
    except Exception as e: return "ERR " + repr(e)
for u in ["https://t.me/cs3news/19505", "https://t.me/newcsgo/106965", "https://t.me/raiznews/62349"]:
    for suf in ("?embed=1&mode=tme", "?single", "?embed=1"):
        h = get(u + suf)
        v = re.findall(r'<video[^>]+src="([^"]+)"', h)
        big = "too big" in h.lower() or "media is too big" in h.lower()
        print(u + suf, len(h), "video:", v[:1], "too_big:", big, re.findall(r'class="(tgme_widget_message_video[^"]*)"', h)[:3])
        time.sleep(1)
    h = get(u.replace("t.me/", "t.me/s/").rsplit("/", 1)[0] + "?before=" + str(int(u.rsplit("/", 1)[1]) + 1))
    blk = h.split('data-post="' + u.split("t.me/")[1] + '"')
    print("  /s block video:", re.findall(r'<video[^>]+src="([^"]+)"', blk[1][:6000])[:1] if len(blk) > 1 else "no block", "big" if len(blk) > 1 and "too big" in blk[1][:6000].lower() else "")
