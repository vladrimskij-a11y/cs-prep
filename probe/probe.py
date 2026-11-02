import gzip, re, urllib.request, urllib.error
out = []
def get(u):
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
                                             "Accept": "text/html", "Accept-Encoding": "gzip", "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
        return b.decode("utf-8", "replace")
h = get("https://www.dust2.us/matches/2367883/betboom-vs-b8")
t = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S))
for k in ["pick", "Pick", "ban", "remov", "decider", "left over", "Mirage", "Inferno"]:
    i = t.find(k)
    out.append(f"--- [{k}] {i}\n" + (t[max(0, i-400):i+600] if i >= 0 else ""))
open("probe/out.txt", "w").write("\n".join(out))
