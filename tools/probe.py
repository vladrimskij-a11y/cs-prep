import json, re, time, urllib.request, urllib.parse, gzip
def get(url, h=None):
    hh = {"User-Agent": "cs-prep/1.0 (https://github.com/vladrimskij-a11y/cs-prep; cs-prep-bot@users.noreply.github.com)", "Accept-Encoding": "gzip"}; hh.update(h or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=hh), timeout=30) as r:
            b = r.read()
            if r.headers.get("Content-Encoding") == "gzip": b = gzip.decompress(b)
            return b.decode("utf-8", "replace")
    except Exception as e:
        return "ERR " + repr(e)
B = "https://api.bo3.gg/api/v1"
T = json.loads(get(B + "/tournaments/intel-extreme-masters-beijing-2026"))
m = None
for st in T.get("stages") or []:
    for rd in st.get("rounds") or []:
        for x in rd.get("matches") or []:
            if x.get("status") == "finished": m = x; break
print("MATCH", m["id"], m["slug"])
def keys(o, pre=""):
    out = []
    if isinstance(o, dict):
        for k, v in o.items():
            out.append(pre + k)
            if isinstance(v, (dict, list)): out += keys(v, pre + k + ".")
    elif isinstance(o, list) and o: out += keys(o[0], pre + "[].")
    return out
for path in [f"/matches/{m['slug']}", f"/matches/{m['id']}", f"/matches/{m['slug']}?with=games,picks_bans,match_maps,vetoes",
             f"/matches/{m['id']}/picks_bans", f"/matches/{m['id']}/vetoes", f"/matches/{m['id']}/map_picks", f"/picks_bans?filter[picks_bans.match_id][eq]={m['id']}",
             f"/match_maps?filter[match_maps.match_id][eq]={m['id']}", f"/games?filter[games.match_id][eq]={m['id']}"]:
    r = get(B + path); time.sleep(0.5)
    print("=====", path, len(r), r[:120].replace("\n", " "))
    if not r.startswith("ERR"):
        try:
            j = json.loads(r); ks = keys(j); print("  KEYS:", [k for k in ks if re.search(r"pick|ban|veto|decid|map", k, re.I)][:60])
            for k in ("picks_bans", "vetoes", "map_picks", "match_maps", "veto"):
                if isinstance(j, dict) and k in j: print("  ", k, json.dumps(j[k], ensure_ascii=False)[:1500])
        except Exception as e: print("  notjson", e)

for x in [mm for st in T.get("stages") or [] for rd in st.get("rounds") or [] for mm in rd.get("matches") or [] if mm.get("status") == "finished"][:8]:
    j = json.loads(get(B + "/matches/" + x["slug"])); time.sleep(0.4)
    print("MM", x["slug"], json.dumps(j.get("match_maps"), ensure_ascii=False)[:600])
LP = "https://liquipedia.net/counterstrike/api.php"
r = get(LP + "?" + urllib.parse.urlencode({"action": "parse", "page": "Intel_Extreme_Masters/2026/Beijing", "prop": "wikitext", "format": "json"}))
wt = json.loads(r)["parse"]["wikitext"]["*"]
i = wt.find("{{Match")
print("LP MATCH BLOCK:\n", wt[i:i+2500])
print("veto words:", sorted(set(re.findall(r"\|\s*(\w*veto\w*|\w*ban\w*|\w*pick\w*)\s*=", wt)))[:30])
