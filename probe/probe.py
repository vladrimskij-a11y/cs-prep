"""Поиск официальных Telegram-каналов CS-организаций."""
import gzip, html, re, time, urllib.request, urllib.error
orgs = {
 "NAVI": ["navi", "navi_gg", "navigg", "natusvincere", "natus_vincere", "navi_official", "naviofficial", "navi_cs", "navics2", "navi_esports", "naviesports"],
 "Falcons": ["teamfalcons", "falcons", "falconsgg", "falcons_gg", "falconsesports", "teamfalconsgg", "falcons_esports"],
 "FURIA": ["furia", "furiagg", "furia_gg", "furiaesports"],
 "FaZe": ["faze", "fazeclan", "faze_clan"],
 "MOUZ": ["mouz", "mouzesports", "mouz_gg", "mousesports"],
 "Liquid": ["teamliquid", "liquid"],
 "G2": ["g2esports", "g2_esports", "g2"],
 "HEROIC": ["heroic", "heroicgg", "heroic_gg"],
 "B8": ["b8", "b8esports", "b8_esports", "b8gg", "b8_gg", "b8team", "b8_team", "teamb8", "b8cs", "b8_cs"],
 "BetBoom": ["betboomteam", "betboom_team", "betboomcs", "betboom_cs", "betboomcs2", "bbteam", "bbteamcs", "betboomesports"],
 "Aurora": ["aurora", "auroragg", "aurora_gg", "auroragaming", "aurora_gaming", "teamaurora", "auroracs"],
 "PARIVISION": ["parivision", "parivisioncs"],
 "The MongolZ": ["themongolz", "mongolz", "the_mongolz"],
 "TYLOO": ["tyloo"], "Legacy": ["legacyesports", "legacygg"], "9z": ["9zteam", "9z_team"],
 "Vitality": ["teamvitality", "vitality", "vitalitygg"],
 "Spirit": ["teamspirit", "team_spirit", "spirit", "teamspiritgg", "spirit_gg"],
 "Astralis": ["astralis", "astralisgg"], "Virtus.pro": ["virtuspro", "virtus_pro", "vpesports", "vp_esports"],
 "Nemiga": ["nemiga", "nemigagaming", "nemiga_gaming", "nemigagg"], "Monte": ["monte", "montegg", "monte_esports", "monteesports"],
 "Eternal Fire": ["eternalfire", "eternal_fire"], "1win": ["1winteam", "1win_team", "1wincs"],
 "AMKAL": ["amkal", "amkalesports", "amkal_esports"], "Passion UA": ["passionua", "passion_ua"],
 "Fnatic": ["fnatic"], "Ninjas in Pyjamas": ["nip", "nipgaming", "ninjasinpyjamas"], "Complexity": ["complexity", "complexitygaming"],
 "3DMAX": ["3dmax", "3dmaxgg"], "BIG": ["bigclan", "big_clan"], "GamerLegion": ["gamerlegion"], "Astralis": ["astralis"],
}
out = []
def get(u):
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return r.status, b.decode("utf-8", "replace")
    except Exception:
        return -1, ""
for org, cands in orgs.items():
    for c in cands:
        st, h = get(f"https://t.me/s/{c}")
        if st != 200 or "tgme_widget_message_text" not in h:
            time.sleep(0.5); continue
        t = re.search(r'<meta property="og:title" content="([^"]*)"', h)
        d = re.search(r'<meta property="og:description" content="([^"]*)"', h)
        subs = re.search(r'<span class="counter_value">([^<]+)</span>\s*<span class="counter_type">subscribers', h)
        last = re.findall(r'<time datetime="([^"]+)"', h)
        cs = len(re.findall(r"\bCS ?2?\b|Counter-Strike|КС|IEM|BLAST|PGL|мейджор|major", h, re.I))
        dota = len(re.findall(r"Dota|Дот[аеу]", h, re.I))
        out.append(f"{org} | @{c} | {html.unescape(t.group(1)) if t else ''} | subs={subs.group(1) if subs else '?'} | last={last[-1][:10] if last else '?'} | cs={cs} dota={dota} | {html.unescape(d.group(1)).replace(chr(10),' ')[:160] if d else ''}")
        time.sleep(0.6)
open("probe/out.txt", "w").write("\n".join(out) or "ничего")
