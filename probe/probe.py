"""Поиск Telegram-каналов игроков и команд IEM Beijing: какие t.me/<имя> существуют и что о себе пишут."""
import gzip, html, re, time, urllib.request, urllib.error
nicks = """m0NESY NiKo TeSeS kyousuke karrigan zonic b1t Aleksib iM w0nderful makazze B1ad3 Boombl4 zorte s1ren d1Ledez Magnojez
Jame xiELO zweih FL1T HObbit dastan npl alex666 esenthial kensizor s1zzi YEKINDAR molodoy FalleN KSCERATO yuurih
XANTARES woxic Wicadia Jimpphat kyxsan torzsi xertioN Spinx xelex jcobbb frozen Twistzz JBOEN Neityu""".split()
teams = "natus_vincere navi navi_cs navi_official betboomteam betboom_cs betboomcs parivision parivisiongg b8esports b8_esports teamfalcons falconsesports auroragg aurora_gg furia mouz faze heroic".split()
cands = []
for n in nicks:
    b = re.sub(r"[^A-Za-z0-9_]", "", n)
    for v in (b, b + "cs", b + "_cs", b + "cs2", b + "tg", "its" + b, b + "_official", b + "official"):
        if len(v) >= 5 and v not in cands: cands.append(v)
cands += teams
out = []
def get(u):
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            b = r.read(); b = gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b
            return r.status, b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return -1, ""
for c in cands:
    st, h = get(f"https://t.me/s/{c}")
    posts = h.count("tgme_widget_message_text")
    if st == 200 and posts:
        t = re.search(r'<meta property="og:title" content="([^"]*)"', h)
        d = re.search(r'<meta property="og:description" content="([^"]*)"', h)
        subs = re.search(r'<span class="counter_value">([^<]+)</span>\s*<span class="counter_type">subscribers', h)
        last = re.findall(r'<time datetime="([^"]+)"', h)
        out.append(f"{c} | {html.unescape(t.group(1)) if t else ''} | subs={subs.group(1) if subs else '?'} | last={last[-1][:10] if last else '?'} | {html.unescape(d.group(1))[:150] if d else ''}")
    time.sleep(0.7)
open("probe/out.txt", "w").write("\n".join(out) or "ничего не найдено")
