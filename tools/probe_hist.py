"""Проба (только чтение): где у Liquipedia и HLTV история команд игрока и прежние ники."""
import sys, re
sys.path.insert(0, "collector")
import collect as C

pages = C.lp_pages(["Techno4K", "S1mple", "Magixx", "Zont1x", "Torzsi", "Jame"])
for t, txt in pages.items():
    print("=== LP", t, len(txt))
    for ln in txt.split("\n"):
        if re.search(r"^\|\s*(id|ids|romanized_name|history)\s*=|\{\{TH\||TeamHistory|\{\{TeamHistoryAuto", ln):
            print("   ", ln[:200])
    i = txt.find("history")
    print("   ctx:", txt[i - 50:i + 700].replace("\n", " ⏎ ") if i >= 0 else "-")
h = C.hltv_get("/player/7998/s1mple")
print("=== HLTV len", len(h or ""))
if h:
    for kw in ("team-breakdown", "time-period", "Team history", "playerTeams", "teamsBox", "Teams</", "player-alt-nick", "Also known", "nickname"):
        for m in list(re.finditer(re.escape(kw), h))[:2]:
            print("--", kw, ":", re.sub(r"\s+", " ", h[max(0, m.start() - 300): m.start() + 900]))
