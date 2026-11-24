import sys
sys.path.insert(0, "collector")
import collect as c
r = c.bo3("/tournaments", {"page[limit]": 40, "sort": "-start_date", "filter[tournaments.tier][in]": ",".join(sorted(c.TIERS)), "filter[tournaments.status][in]": "upcoming,current,finished"}).get("results") or []
for t in r:
    if "ingapore" in (t.get("name") or "") or "ajor" in (t.get("name") or ""):
        print("T", t.get("id"), t.get("slug"), t.get("name"), t.get("status"), t.get("start_date"), t.get("parent_id"), c.tour_same(t["name"], c.tour_norm("PGL Major Singapore 2026")))
        T = c.bo3(f"/tournaments/{t['slug']}")
        for st in T.get("stages") or []:
            print("   ST", st.get("title"), st.get("format_type"), st.get("status"), len(st.get("rounds") or []), sum(len(r.get("matches") or []) for r in st.get("rounds") or []))
