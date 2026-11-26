import os, sys, json, urllib.request
sys.path.insert(0, "collector")
os.environ.setdefault("NEWS_ONLY", "1")
import collect as C
C.CACHE.mkdir(parents=True, exist_ok=True)
# 1) GigaChat: остаток пакета
try:
    tok = C.giga_token()
    req = urllib.request.Request("https://gigachat.devices.sberbank.ru/api/v1/balance", headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=C.giga_ctx()) as r:
        print("GIGA balance:", r.read().decode()[:600])
except Exception as e:
    print("GIGA balance ERR", repr(e)[:300])
# 2) Gemini: какие модели есть (gemma)
key = os.environ.get("GEMINI_KEY")
try:
    j = json.loads(urllib.request.urlopen(urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers={"x-goog-api-key": key}), timeout=30).read())
    print("models:", [m["name"] for m in j.get("models", []) if "gemma" in m["name"] or "flash" in m["name"]])
except Exception as e:
    print("models ERR", repr(e)[:300])
import time
for mdl in ["gemma-4-26b-a4b-it", "gemma-4-31b-it", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]:
    t0=time.time()
    try:
        body = json.dumps({"contents": [{"role": "user", "parts": [{"text": "Пост: «donk покинул Team Spirit и перешёл в Falcons». Ответь JSON с полями cat (transfer, match, interview, ad или other) и title_ru (короткий заголовок)"}]}]}).encode()
        req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent", data=body, method="POST", headers={"Content-Type": "application/json", "x-goog-api-key": key})
        j = json.loads(urllib.request.urlopen(req, timeout=60).read())
        print(mdl, "OK", round(time.time()-t0,1), "с:", j["candidates"][0]["content"]["parts"][-1]["text"][:160].replace("\n"," "))
    except Exception as e:
        print(mdl, "ERR", repr(e)[:200], getattr(e, "read", lambda: b"")()[:300])
