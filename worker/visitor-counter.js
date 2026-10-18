// ThePhantasmAl's own visitor counter (Cloudflare Worker, free plan).
//
//   /hit.gif  -> an invisible 1x1 image for the AniList bio. Each load counts one visit,
//                but the same person (same IP) is only counted once per day.
//   /stats    -> {"total": 2184, "today": 37}, read by tools/likers.py every 15 min.
//
// Setup (Cloudflare dashboard):
//   1. Workers & Pages > Create > Worker > name it (e.g. "phantasm-visitors") > Deploy,
//      then Edit code, replace everything with this file, Deploy.
//   2. Storage & Databases > KV > Create namespace "visitors".
//   3. Your worker > Settings > Bindings > Add > KV namespace: variable name COUNTER,
//      namespace "visitors".
//   4. Optional, Settings > Variables: OFFSET = a number to start from (e.g. your old
//      flag counter total), TZ = your time zone for "today" (default America/Toronto).
//
// Free plan limit: 1,000 KV writes a day. Each NEW visitor of the day uses 3 writes,
// so about 330 new visitors a day are counted (repeat visits cost nothing).

const GIF = Uint8Array.from(atob("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"), (c) => c.charCodeAt(0));
const BOTS = /bot|crawl|spider|slurp|preview|embed|facebookexternalhit|discord|telegram|curl|wget|python/i;

function today(env) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: env.TZ || "America/Toronto" }).format(new Date());
}

async function sha(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].slice(0, 12).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function num(env, key) {
  return parseInt((await env.COUNTER.get(key)) || "0", 10) || 0;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = { "Access-Control-Allow-Origin": "*" };

    if (url.pathname === "/hit.gif" || url.pathname === "/hit") {
      const ua = request.headers.get("User-Agent") || "";
      if (!BOTS.test(ua)) {
        const day = today(env);
        const ip = request.headers.get("CF-Connecting-IP") || "unknown";
        // only a hash is stored, never the IP itself; it expires after 2 days
        const seen = "seen:" + (await sha(ip + "|" + day + "|" + (env.SALT || "phantasm")));
        if (!(await env.COUNTER.get(seen))) {
          await env.COUNTER.put(seen, "1", { expirationTtl: 2 * 86400 });
          await env.COUNTER.put("total", String((await num(env, "total")) + 1));
          await env.COUNTER.put("day:" + day, String((await num(env, "day:" + day)) + 1),
                                { expirationTtl: 3 * 86400 });
        }
      }
      return new Response(GIF, {
        headers: { "Content-Type": "image/gif", "Cache-Control": "no-store, max-age=0", ...cors },
      });
    }

    if (url.pathname === "/stats") {
      const offset = parseInt(env.OFFSET || "0", 10) || 0;
      const body = JSON.stringify({
        total: (await num(env, "total")) + offset,
        today: await num(env, "day:" + today(env)),
      });
      return new Response(body, {
        headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...cors },
      });
    }

    return new Response("ThePhantasmAl visitor counter", { headers: cors });
  },
};
