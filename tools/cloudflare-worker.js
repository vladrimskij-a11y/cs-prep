// RIM·CS live — посредник между приложением и bo3.gg (Cloudflare Workers, бесплатный тариф).
// Отдаёт только матчи и карты, только для нашего сайта, и запоминает ответ на 15 секунд,
// поэтому bo3.gg получает не больше одного запроса в 15 секунд на один адрес, сколько бы людей ни смотрело.

const ALLOW = ["https://vladrimskij-a11y.github.io"];
const UPSTREAM = "https://api.bo3.gg/api/v1";

export default {
  async fetch(req, env, ctx) {
    const origin = req.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": ALLOW.includes(origin) ? origin : ALLOW[0],
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Vary": "Origin",
    };
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (req.method !== "GET") return new Response("method not allowed", { status: 405, headers: cors });

    const url = new URL(req.url);
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
