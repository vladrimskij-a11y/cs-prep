import json, sys
sys.path.insert(0, "collector")
import collect as c
j = c.bo3("/teams", {"sort": "rank", "page[limit]": 12})
r = j["results"][0]
print("KEYS", list(r.keys()))
print("VALS", {k: v for k, v in r.items() if k not in ("image_data", "image_url")})
print("TOP", [(x["rank"], x["name"]) for x in j["results"]])
