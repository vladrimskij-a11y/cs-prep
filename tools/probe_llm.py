import os, sys, json, urllib.request
sys.path.insert(0, "collector")
os.environ.setdefault("NEWS_ONLY", "1")
import collect as C
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
for mdl in ["gemma-3-27b-it", "gemma-3-12b-it"]:
    try:
        body = json.dumps({"contents": [{"role": "user", "parts": [{"text": "Ответь одним словом по-русски: столица Франции?"}]}]}).encode()
        req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent", data=body, method="POST", headers={"Content-Type": "application/json", "x-goog-api-key": key})
        j = json.loads(urllib.request.urlopen(req, timeout=60).read())
        print(mdl, "OK:", j["candidates"][0]["content"]["parts"][0]["text"][:80])
    except Exception as e:
        print(mdl, "ERR", repr(e)[:200], getattr(e, "read", lambda: b"")()[:300])
