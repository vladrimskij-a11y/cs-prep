import re, urllib.request, gzip, time
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
def get(u):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Encoding": "gzip", "Accept-Language": "en-US,en;q=0.9"}), timeout=40) as r:
            b = r.read(); return (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "replace")
    except Exception as e: return "ERR " + repr(e)
h = get("https://www.hltv.org/player/7998/s1mple")
print("PLAYER", len(h), h[:40])
b = re.sub(r"\s+", " ", h)
i = b.find("MVP winner at"); print("TROPHY:", b[i-300:i+2500])
j = b.find("EVP", i); print("EVP:", b[j-200:j+400] if j > 0 else "-")
