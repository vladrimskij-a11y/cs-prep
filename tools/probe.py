import re, urllib.request, gzip
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
with urllib.request.urlopen(urllib.request.Request("https://www.hltv.org/player/7998/s1mple", headers={"User-Agent": UA, "Accept-Encoding": "gzip"}), timeout=40) as r:
    b = r.read(); h = (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "replace")
b = re.sub(r"\s+", " ", h)
i = b.find('id="MVPs"'); print("MVPS:", re.sub(r"<img[^>]*>", "", b[i:i+2500]))
