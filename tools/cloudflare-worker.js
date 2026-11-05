// RIM·CS live — посредник между приложением и bo3.gg (Cloudflare Workers, бесплатный тариф).
// Отдаёт только матчи и карты, только для нашего сайта, и запоминает ответ на 15 секунд,
// поэтому bo3.gg получает не больше одного запроса в 15 секунд на один адрес, сколько бы людей ни смотрело.
//
// Будильник для сборщика: GitHub запускает расписание с опозданием (новости — примерно раз в час),
// поэтому по крону Cloudflare (Settings → Triggers → Cron: */5 * * * *) воркер сам запускает
// сбор новостей каждые 5 минут, а статистику — каждые 30 минут. Нужен секрет GH_TOKEN
// (Settings → Variables and Secrets): токен GitHub с правом Actions: write только на репозиторий cs-prep.
//
// Чат «Спросить» (POST /ask): вопрос → Gemini с поиском Google → ответ и ссылки на источники.
// Нужен секрет GEMINI_KEY (тот же ключ, что в GitHub Secrets). Ключ живёт только здесь, в приложение не попадает.

const ALLOW = ["https://vladrimskij-a11y.github.io"];
const UPSTREAM = "https://api.bo3.gg/api/v1";

const REPO = "vladrimskij-a11y/cs-prep";

async function dispatch(env, wf) {
  return fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${wf}/dispatches`, {
    method: "POST",
    headers: { "Authorization": `Bearer ${env.GH_TOKEN}`, "Accept": "application/vnd.github+json", "User-Agent": "rimcs-cron", "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main" }),
  });
}

const ASK_SYSTEM = `Ты — эксперт по профессиональному Counter-Strike 2 и помощник русскоязычного комментатора.
Отвечай по-русски, коротко и по делу (обычно 2–6 предложений или короткий список), без воды.
Для любых фактов о текущем положении дел (составы, результаты, рейтинги, трансферы, расписание, статистика) ОБЯЗАТЕЛЬНО ищи свежую информацию в Google и опирайся на неё.
Если источники расходятся или данных нет — так и скажи, не выдумывай. Ники игроков и названия команд пиши как в оригинале.
Сегодня: ${"{DATE}"}.`;

async function gemini(env, model, contents, withSearch) {
  const body = {
    systemInstruction: { parts: [{ text: ASK_SYSTEM.replace("{DATE}", new Date().toISOString().slice(0, 10)) }] },
    contents,
    generationConfig: { temperature: 0.3, maxOutputTokens: 1400 },
  };
  if (withSearch) body.tools = [{ google_search: {} }];
  return fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": env.GEMINI_KEY },
    body: JSON.stringify(body),
  });
}

async function ask(req, env, cors) {
  const json = (o, st = 200) => new Response(JSON.stringify(o), { status: st, headers: { ...cors, "Content-Type": "application/json; charset=utf-8" } });
  if (!env.GEMINI_KEY) return json({ error: "В воркере не задан секрет GEMINI_KEY" }, 500);
  let q;
  try { q = await req.json(); } catch (e) { return json({ error: "плохой запрос" }, 400); }
  const msgs = (Array.isArray(q.messages) ? q.messages : []).slice(-12)
    .filter((m) => m && typeof m.text === "string" && m.text.trim())
    .map((m) => ({ role: m.role === "model" ? "model" : "user", parts: [{ text: m.text.slice(0, 2000) }] }));
  if (!msgs.length || msgs[msgs.length - 1].role !== "user") return json({ error: "нет вопроса" }, 400);
  // основная модель с поиском; перегружена — облегчённая с поиском; совсем не вышло — без поиска
  const tries = [["gemini-flash-latest", true], ["gemini-flash-lite-latest", true], ["gemini-flash-lite-latest", false]];
  let last = "";
  for (const [model, search] of tries) {
    const r = await gemini(env, model, msgs, search);
    if (!r.ok) { last = `${model}: ${r.status}`; if (r.status === 400 || r.status === 429 || r.status >= 500) continue; break; }
    const j = await r.json();
    const c = (j.candidates || [])[0] || {};
    const text = ((c.content || {}).parts || []).map((p) => p.text || "").join("").trim();
    if (!text) { last = `${model}: пустой ответ`; continue; }
    const seen = new Set(), sources = [];
    for (const ch of (c.groundingMetadata || {}).groundingChunks || []) {
      const w = ch.web || {};
      if (w.uri && !seen.has(w.uri)) { seen.add(w.uri); sources.push({ url: w.uri, title: w.title || "" }); }
    }
    return json({ text, sources: sources.slice(0, 6), model, search });
  }
  return json({ error: "Нейросеть сейчас перегружена, попробуй ещё раз через минуту", detail: last }, 503);
}

export default {
  async scheduled(event, env, ctx) {
    if (!env.GH_TOKEN) return;
    const m = new Date(event.scheduledTime).getUTCMinutes();
    ctx.waitUntil(dispatch(env, "news.yml"));
    if (m % 30 === 0) ctx.waitUntil(dispatch(env, "stats.yml"));
  },

  async fetch(req, env, ctx) {
    const origin = req.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": ALLOW.includes(origin) ? origin : ALLOW[0],
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Vary": "Origin",
    };
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    const url = new URL(req.url);
    if (url.pathname === "/ask" && req.method === "POST") {
      if (!ALLOW.includes(origin)) return new Response("not allowed", { status: 403, headers: cors });  // чат — только с нашего сайта
      return ask(req, env, cors);
    }
    if (req.method !== "GET") return new Response("method not allowed", { status: 405, headers: cors });

    if (!/^\/(matches|games)(\/[\w-]+)?(\/players_stats)?$/.test(url.pathname)) {
      return new Response("not allowed", { status: 403, headers: cors });
    }

    const target = UPSTREAM + url.pathname + url.search;
    const cache = caches.default;
    const key = new Request(target);
    let res = await cache.match(key);
    if (!res) {
      const up = await fetch(target, { headers: { "Accept": "application/json", "User-Agent": "rimcs-live/1.0" } });
      res = new Response(up.body, up);
      res.headers.set("Cache-Control", "public, max-age=15");
      if (up.ok) ctx.waitUntil(cache.put(key, res.clone()));
    }
    const out = new Response(res.body, res);
    for (const [k, v] of Object.entries(cors)) out.headers.set(k, v);
    return out;
  },
};
