// Puente PDMX → OpenF1 (Cloudflare Worker, plan gratis)
//
// Para qué sirve: durante las sesiones en vivo OpenF1 no le contesta a páginas web
// (rechaza la consulta previa que hace el navegador). Este puente hace la consulta
// desde Cloudflare y le regresa la respuesta a tu página.
//
// No guarda tu usuario ni tu contraseña: solo deja pasar el token de OpenF1 que tu
// página manda en cada consulta. Solo acepta tu página (ORIGENES) y solo rutas de
// OpenF1 (/v1/... y /token).

const ORIGENES = ["https://julioga120-tech.github.io"];
const OPENF1 = "https://api.openf1.org";

function cors(origin) {
  return {
    "Access-Control-Allow-Origin": ORIGENES.includes(origin) ? origin : ORIGENES[0],
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}

async function handle(req) {
  const origin = req.headers.get("Origin") || "";
  const h = cors(origin);
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: h });

  const url = new URL(req.url);
  const ruta = url.pathname;
  const esOpenF1 = ruta.startsWith("/v1/") || ruta === "/token";
  if (!esOpenF1) {
    return new Response("Puente PDMX funcionando", { status: 200, headers: { ...h, "Content-Type": "text/plain; charset=utf-8" } });
  }
  if (!ORIGENES.includes(origin)) return new Response("Origen no permitido", { status: 403, headers: h });
  if (req.method !== "GET" && !(req.method === "POST" && ruta === "/token")) {
    return new Response("Método no permitido", { status: 405, headers: h });
  }

  const init = { method: req.method, headers: {} };
  const auth = req.headers.get("Authorization");
  if (auth) init.headers["Authorization"] = auth;
  if (req.method === "POST") {
    init.headers["Content-Type"] = "application/x-www-form-urlencoded";
    init.body = await req.text();
  }

  let r;
  try {
    r = await fetch(OPENF1 + ruta + url.search, init);
  } catch (e) {
    return new Response(JSON.stringify({ error: "OpenF1 no respondió" }), { status: 502, headers: { ...h, "Content-Type": "application/json" } });
  }
  const out = new Headers(h);
  out.set("Content-Type", r.headers.get("Content-Type") || "application/json");
  out.set("Cache-Control", "no-store");
  return new Response(r.body, { status: r.status, headers: out });
}

export default { fetch: handle };
