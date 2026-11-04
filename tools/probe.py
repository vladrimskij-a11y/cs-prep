import sys, os
sys.argv = ["x"]; os.environ.setdefault("PANDASCORE_TOKEN", "x"); sys.path.insert(0, "collector")
import collect as C
ha, hb = C.bo3_history(8171), C.bo3_history(787)
snap = C.valve_links().get("date"); print("PR snap", snap, len(ha), len(hb))
for h, bid in ((ha, 8171), (hb, 787)):
    print("PR wins after", [(m.get("start_date","")[:10], C.opp_of(m, bid).get("name"), m.get("tier"), (m.get("tournament") or {}).get("name")) for m in h if (m.get("start_date") or "")[:10] > snap and m.get("winner_team_id") == bid])
a = {"name": "PARIVISION", "points": 1661}; b = {"name": "Natus Vincere", "points": 1731}
for lab, hs in (("with", [ha, hb]), ("without", [[], []])):
    r = C.vrs_outcomes_valve(a, b, [a, b], hists=hs, bids=[8171, 787], prize="1,250,000 United States Dollar", since="2026-11-02")
    print("PR", lab, "PRV win", r["win0"][0]["delta"], r["win0"][1]["delta"], "| NAVI win", r["win1"][0]["delta"], r["win1"][1]["delta"])
