// Puente PDMX → OpenF1 (Cloudflare Worker, plan gratis, prendido 24/7)
// Es la alternativa a tu servidor de Oracle (puente/servidor.py): hacen lo mismo.
//
// MODO NUBE (recomendado): tu cuenta de OpenF1 vive aquí como secretos cifrados de Cloudflare
//   OPENF1_USER, OPENF1_PASS y CLAVE. Tus celulares y la compu entran con tu link (lleva tu
//   CLAVE); tu contraseña de OpenF1 nunca sale de Cloudflare. El puente le da a cada dispositivo
//   un token de OpenF1 que dura máximo 1 hora.
// PASO DIRECTO: sin esos secretos solo deja pasar el token que manda la página.
//
// Durante las sesiones en vivo OpenF1 no le contesta a páginas web (rechaza la consulta previa del
// navegador); el puente hace la consulta desde Cloudflare. Solo acepta tu página (ORIGENES) y
// solo rutas de OpenF1 (/v1/... y /token).

const ORIGENES = ["https://julioga120-tech.github.io"];
const OPENF1 = "https://api.openf1.org";

let tok = "", tokExp = 0;     // token de tu cuenta (en memoria; se renueva solo)
const fallos = new Map();     // intentos con clave equivocada por IP (freno anti adivinanzas)

function cors(origin) {
  return {
    "Access-Control-Allow-Origin": ORIGENES.includes(origin) ? origin : ORIGENES[0],
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}
const json = (obj, status, h) => new Response(JSON.stringify(obj), { status, headers: { ...h, "Content-Type": "application/json", "Cache-Control": "no-store" } });

function iguales(a, b) {
  a = String(a || ""); b = String(b || "");
  let d = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) d |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return d === 0 && a.length > 0;
}

async function tokenNube(env) {
  if (tok && Date.now() < tokExp - 5 * 60000) return 200;
  const body = "username=" + encodeURIComponent(env.OPENF1_USER) + "&password=" + encodeURIComponent(env.OPENF1_PASS);
  const r = await fetch(OPENF1 + "/token", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
  if (!r.ok) return r.status;
  const j = await r.json().catch(() => ({}));
  if (!j.access_token) return 502;
  tok = j.access_token; tokExp = Date.now() + (+j.expires_in || 3600) * 1000;
  return 200;
}

async function handle(req, env) {
  const origin = req.headers.get("Origin") || "";
  const h = cors(origin);
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: h });

  const url = new URL(req.url);
  const ruta = url.pathname;
  const nube = !!(env && env.OPENF1_USER && env.OPENF1_PASS && env.CLAVE);
  const esOpenF1 = ruta.startsWith("/v1/") || ruta === "/token";
  if (!esOpenF1) {
    return new Response("Puente PDMX funcionando" + (nube ? " · modo nube listo" : " · sin modo nube (faltan los secretos OPENF1_USER, OPENF1_PASS y CLAVE)"), { status: 200, headers: { ...h, "Content-Type": "text/plain; charset=utf-8" } });
  }
  if (!ORIGENES.includes(origin)) return new Response("Origen no permitido", { status: 403, headers: h });
  if (req.method !== "GET" && !(req.method === "POST" && ruta === "/token")) {
    return new Response("Método no permitido", { status: 405, headers: h });
  }

  let body = null;
  if (req.method === "POST") {
    body = await req.text();
    const f = new URLSearchParams(body);
    if (f.has("clave")) {
      // MODO NUBE: el dispositivo manda tu CLAVE y recibe un token de tu cuenta guardada
      if (!nube) return json({ error: "sin-nube" }, 501, h);
      const ip = req.headers.get("CF-Connecting-IP") || "?", now = Date.now(), x = fallos.get(ip);
      if (x && x.n >= 8 && now - x.t < 15 * 60000) return json({ error: "espera" }, 429, h);
      if (!iguales(f.get("clave"), env.CLAVE)) {
        if (fallos.size > 2000) fallos.clear();
        fallos.set(ip, x && now - x.t < 15 * 60000 ? { n: x.n + 1, t: x.t } : { n: 1, t: now });
        return json({ error: "clave" }, 401, h);
      }
      fallos.delete(ip);
      let st;
      try { st = await tokenNube(env); } catch { return json({ error: "openf1" }, 504, h); }
      if (st !== 200) return json({ error: [400, 401, 403, 422].includes(st) ? "cuenta" : "openf1" }, 502, h);
      return json({ access_token: tok, token_type: "bearer", expires_in: Math.max(60, Math.floor((tokExp - Date.now()) / 1000)), username: env.OPENF1_USER }, 200, h);
    }
  }

  // PASO DIRECTO: consultas /v1/... con el token que manda la página, o /token con usuario y contraseña
  const init = { method: req.method, headers: {} };
  const auth = req.headers.get("Authorization");
  if (auth) init.headers["Authorization"] = auth;
  if (req.method === "POST") {
    init.headers["Content-Type"] = "application/x-www-form-urlencoded";
    init.body = body;
  }
  let r;
  try {
    r = await fetch(OPENF1 + ruta + url.search, init);
  } catch (e) {
    return json({ error: "OpenF1 no respondió" }, 502, h);
  }
  const out = new Headers(h);
  out.set("Content-Type", r.headers.get("Content-Type") || "application/json");
  out.set("Cache-Control", "no-store");
  return new Response(r.body, { status: r.status, headers: out });
}

export default { fetch: handle };
