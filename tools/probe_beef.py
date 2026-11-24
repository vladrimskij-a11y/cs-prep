import sys
sys.path.insert(0, "collector")
import collect as c
for ch in c.TG_NEWS_CHANNELS:
    r = c.tg_search(ch, "stavn")
    print("CH", ch, len(r), r[-1]["date"] if r else None)
    for x in r:
        if c.BEEF_RX.search(x["text"]):
            print("  BEEF", x["date"], x["url"], x["text"][:160].replace("\n", " "))
