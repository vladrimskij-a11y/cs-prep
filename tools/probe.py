import re, json, urllib.request
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "cs-prep"}), timeout=30) as r: return r.read().decode()
base = "https://raw.githubusercontent.com/ValveSoftware/counter-strike_regional_standings/main/live/2026/"
g = get(base + "standings_global_2026_11_02.md")
for line in g.splitlines():
    if re.search(r"PARIVISION|Natus Vincere|\| 1 ", line):
        print(line)
        m = re.search(r"\((details/[^)]+)\)", line)
        if m:
            print(get(base + m.group(1))[:9000]); print("#" * 40)
