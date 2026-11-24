import sys
sys.path.insert(0, "collector")
import collect as c
for a, b in (("Liquid", "Wildcard"), ("FaZe", "BC.Game"), ("Eternal Fire", "DENDELE")):
    print("O", a, b, c.bo3_odds([c.team_by_name(a), c.team_by_name(b)]))
print("U", [(x["opponent_name"], x["date"]) for x in c.bo3_upcoming(c.team_by_name("Liquid"))][:3])
