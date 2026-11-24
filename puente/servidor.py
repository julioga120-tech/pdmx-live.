#!/usr/bin/env python3
"""Servidor Pure Driving MX: live timing público, prendido 24/7 en tu servidor.

- El motor (motor.py) es el único que habla con OpenF1 y publica los datos como archivos.
- Las páginas (celular en /, transmisión para OBS en /tv) solo leen esos archivos: cualquiera puede
  entrar sin cuenta ni clave y no gasta consultas de OpenF1.
- Tu cuenta de OpenF1 se guarda SOLO aquí (/var/lib/pdmx/config.json, permisos 600).
- Primera vez: https://TU-SERVIDOR/configurar con el código que sale al instalar.
- https://TU-SERVIDOR/estado dice cómo va el motor.

Sin dependencias: solo Python 3.
"""
import hashlib
import hmac
import io
import json
import secrets
import zipfile
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import motor as M  # noqa: E402

DATA = os.environ.get("PDMX_DATA", "/var/lib/pdmx")
PUB = os.environ.get("PDMX_PUB", "/var/lib/pdmx-pub")      # datos públicos (los sirve Caddy en /d/)
WEB_DIR = os.environ.get("PDMX_WEB", "/opt/pdmx/web")
CFG = os.path.join(DATA, "config.json")
CODIGO = os.path.join(DATA, "codigo")   # código de instalación (sale en la terminal); se borra al configurar
PORT = int(os.environ.get("PDMX_PORT", "8787"))
VERSION = "3.5"
ACT = os.path.join(DATA, "actualizar")           # pedidos del panel (los ejecuta pdmx-panel, como root)
SESIONES = os.path.join(DATA, "sesiones.json")    # sesiones del panel (solo se guarda el hash)
MAX_ZIP = 40 * 1024 * 1024

fallos = {}
_cfg = {"at": 0.0, "v": {}}


def leer_cfg():
    now = time.time()
    if now - _cfg["at"] < 3:
        return _cfg["v"]
    try:
        with open(CFG, encoding="utf-8") as f:
            c = json.load(f)
        c = c if c.get("user") and c.get("pass") and c.get("clave") else {}
    except Exception:
        c = {}
    _cfg["at"], _cfg["v"] = now, c
    return c


def guardar_cfg(c):
    os.makedirs(DATA, exist_ok=True)
    tmp = CFG + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(c, f)
    os.replace(tmp, CFG)
    _cfg["at"] = 0


def leer_codigo():
    try:
        with open(CODIGO, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def bloqueado(ip):
    x = fallos.get(ip)
    return bool(x and x[0] >= 8 and time.time() - x[1] < 900)


def fallo(ip):
    now = time.time()
    if len(fallos) > 2000:
        fallos.clear()
    x = fallos.get(ip)
    fallos[ip] = ((x[0] + 1) if x and now - x[1] < 900 else 1, x[1] if x and now - x[1] < 900 else now)


def iguales(a, b):
    return hmac.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))


motor = M.Motor(DATA, PUB, leer_cfg, log=lambda *a: print(time.strftime("%H:%M:%S"), *a, flush=True))

ESTILO = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@500;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{color-scheme:dark;--bg:#070a10;--panel:#0d131c;--line:#1e2936;--line2:#283547;--tx:#eef3f8;--mut:#8593a6;--cyan:#00d8ff;--green:#25e889;--red:#ff4d65;--yellow:#ffd400}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.cab{display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--line)}
.cab img{height:30px}.cab .t{font:800 15px/1 "Barlow Semi Condensed",sans-serif;letter-spacing:2.5px}
.cab .t small{display:block;margin-top:3px;font:600 11px/1 Inter,sans-serif;letter-spacing:1.4px;color:var(--mut)}
.cab .der{margin-left:auto}
main{max-width:620px;margin:0 auto;padding:22px 16px 56px}
h1{font:800 26px/1.15 "Barlow Semi Condensed",sans-serif;letter-spacing:.6px;margin:4px 0 6px}
.sub{color:var(--mut);margin:0 0 20px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin:0 0 14px;overflow:hidden}
.card h2{margin:0;padding:13px 16px;font:700 13px/1 "Barlow Semi Condensed",sans-serif;letter-spacing:2px;color:var(--mut);border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}
.card .in{padding:16px}
.kv{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line)}
.kv div{background:var(--panel);padding:12px 16px}.kv b{display:block;font:700 18px/1.2 "Barlow Semi Condensed",sans-serif;letter-spacing:.4px}
.kv span{font-size:12px;color:var(--mut);letter-spacing:.3px}
label{display:block;font-size:12px;font-weight:600;color:var(--mut);letter-spacing:.6px;margin:14px 0 6px}
input[type=text],input[type=password]{width:100%;padding:12px 13px;background:#080d14;border:1px solid var(--line2);border-radius:8px;color:var(--tx);font:16px Inter,sans-serif}
input:focus{outline:none;border-color:var(--cyan)}
.btn,button{display:inline-flex;align-items:center;justify-content:center;gap:8px;width:100%;margin-top:14px;padding:13px 16px;border:0;border-radius:8px;background:var(--cyan);color:#001018;font:800 15px/1 "Barlow Semi Condensed",sans-serif;letter-spacing:1.4px;text-decoration:none;cursor:pointer}
.btn.sec,button.sec{background:transparent;color:var(--tx);border:1px solid var(--line2)}
.btn.peligro,button.peligro{background:transparent;color:#ff8a9b;border:1px solid rgba(255,77,101,.45)}
button:disabled{opacity:.45;cursor:default}
.link{width:auto;margin:0;padding:8px 12px;font-size:13px}
.pill{display:inline-flex;align-items:center;gap:7px;padding:4px 10px;border-radius:999px;font:700 12px/1 "Barlow Semi Condensed",sans-serif;letter-spacing:1.3px;border:1px solid var(--line2);color:var(--mut)}
.pill i{width:7px;height:7px;border-radius:50%;background:currentColor}
.pill.ok{color:var(--green);border-color:rgba(37,232,137,.35)}.pill.warn{color:var(--yellow);border-color:rgba(255,212,0,.35)}.pill.err{color:#ff8a9b;border-color:rgba(255,77,101,.4)}
.drop{display:block;border:1.5px dashed var(--line2);border-radius:10px;padding:22px 16px;text-align:center;cursor:pointer;color:var(--mut)}
.drop b{display:block;color:var(--tx);font:700 17px/1.3 "Barlow Semi Condensed",sans-serif;letter-spacing:.6px;margin-bottom:3px}
.drop.lleno{border-style:solid;border-color:var(--cyan)}
.barra{height:6px;background:#0a1018;border-radius:6px;overflow:hidden;margin-top:14px;display:none}.barra i{display:block;height:100%;width:0;background:var(--cyan);transition:width .2s}
.msg{margin-top:12px;font-size:14px;color:var(--mut)}.msg.ok{color:var(--green)}.msg.err{color:#ff8a9b}
.nota{font-size:13px;color:var(--mut);margin:10px 0 0}
details{margin-top:12px}summary{cursor:pointer;font-size:13px;color:var(--mut)}
pre{white-space:pre-wrap;word-break:break-word;font:12px/1.45 ui-monospace,Menlo,Consolas,monospace;color:#9fb0c4;background:#080d14;border:1px solid var(--line);border-radius:8px;padding:10px;max-height:260px;overflow:auto}
.links{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}.links a{color:var(--cyan);text-decoration:none;font-weight:600;font-size:14px}
code{display:block;word-break:break-all;background:#080d14;border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:6px 0 12px;color:var(--cyan);font:13px ui-monospace,Menlo,Consolas,monospace}
.err{color:#ff8a9b;font-weight:600;margin-top:12px}.ok{color:var(--green)}
.pie{margin-top:26px;text-align:center;font-size:12px;color:#5d6b7d}
</style>"""
TIPOS = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".png": "image/png", ".json": "application/json", ".svg": "image/svg+xml", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PDMX"
    sys_version = ""
    timeout = 60

    def log_message(self, fmt, *args):  # no se registra nada
        pass

    def ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def propio(self):
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",")[0].strip()
        return ("http" if host.startswith(("127.0.0.1", "localhost")) else "https") + "://" + host

    def responder(self, status, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def html(self, status, body, titulo="Servidor", cab_der="", extra=None):
        self.responder(status, "<!doctype html><html lang=es><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                       "<meta name=robots content=noindex><meta name=theme-color content='#070a10'><link rel=icon href='/icon-192.png'>"
                       "<title>" + titulo + " · Pure Driving MX</title>" + ESTILO + "</head><body>"
                       "<div class=cab><img src='/helmet.png' alt=''><div class=t>PURE DRIVING MX<small>LIVE TIMING · SERVIDOR</small></div>"
                       "<div class=der>" + cab_der + "</div></div><main>" + body + "<div class=pie>Pure Driving MX Live Timing · versión " + VERSION + "</div></main></body></html>",
                       "text/html; charset=utf-8", dict({"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin"}, **(extra or {})))

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        p = urllib.parse.urlsplit(self.path).path
        if p == "/estado":
            return self.responder(200, json.dumps(dict(motor.resumen(), version=VERSION, configurado=bool(leer_cfg())), ensure_ascii=False, indent=1),
                                  "application/json; charset=utf-8", {"Cache-Control": "no-store"})
        if p == "/configurar":
            return self.form()
        if p == "/panel" or p == "/panel/":
            return self.panel()
        if p.startswith("/panel/api/"):
            return self.panel_api_get(p)
        if p.startswith("/d/"):
            return self.archivo(PUB, p[2:], datos=True)
        return self.archivo(WEB_DIR, p)

    def do_POST(self):
        p = urllib.parse.urlsplit(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        if p == "/panel/api/subir":
            return self.panel_subir(n)
        if n > 65536:
            self.close_connection = True
            return self.responder(413, "Demasiado grande")
        body = self.rfile.read(n) if n > 0 else b""
        if p == "/configurar":
            return self.configurar(body)
        if p.startswith("/panel/api/"):
            return self.panel_api_post(p, body)
        self.responder(405, "Método no permitido")

    # ---------- archivos (respaldo: normalmente los sirve Caddy directo) ----------
    def archivo(self, base, p, datos=False):
        if p in ("", "/"):
            p = "/index.html"
        if p == "/tv":
            p = "/tv.html"
        if ".." in p or not re.fullmatch(r"/[A-Za-z0-9._\-/]{1,120}", p):
            return self.responder(404, "No encontrado")
        f = os.path.join(base, p.strip("/"))
        try:
            with open(f, "rb") as fh:
                body = fh.read()
        except OSError:
            return self.responder(404, "No encontrado", extra={"Cache-Control": "no-store"})
        cache = "public, max-age=300, immutable" if datos and p.startswith("/live/") else "no-cache"
        self.responder(200, body, TIPOS.get(os.path.splitext(f)[1], "application/octet-stream"), {"Cache-Control": cache})

    # ---------- configuración ----------
    def form(self, err="", vals=None):
        c = leer_cfg()
        v = vals or {}
        esc = lambda x: str(x or "").replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")  # noqa: E731
        actual = ('<label>Clave de administrador actual</label><input name=actual type=password autocomplete=current-password required>') if c else (
                  '<label>Código de instalación</label><input type=text name=codigo autocomplete=off autocapitalize=characters spellcheck=false required>'
                  '<p class=nota>Es el código que apareció en la terminal al instalar.</p>')
        self.html(200,
                  "<h1>" + ("Cuenta de OpenF1" if c else "Configurar el servidor") + "</h1>"
                  "<p class=sub>La cuenta de OpenF1 se guarda únicamente en este servidor. Los visitantes de la página nunca la ven.</p>"
                  "<form method=post action=/configurar class=card><div class=in>" + actual +
                  '<label>Usuario o correo de OpenF1</label><input type=text name=user autocomplete=username autocapitalize=none spellcheck=false value="' + esc(v.get("user") or (c.get("user") if c else "")) + '"' + ("" if c else " required") + ">"
                  "<label>Contraseña de OpenF1</label><input name=pass type=password autocomplete=off" + ("" if c else " required") + ">"
                  + ("<p class=nota>Déjala vacía para conservar la actual.</p>" if c else "") +
                  '<label>' + ("Nueva clave de administrador" if c else "Clave de administrador (mínimo 6 caracteres)") + '</label><input name=clave type=password autocomplete=new-password required minlength=6>'
                  "<p class=nota>Con esta clave entras al panel del servidor (" + self.propio() + "/panel).</p>"
                  "<button>Guardar y probar conexión</button>" + ('<div class=err>' + esc(err) + "</div>" if err else "") + "</div></form>",
                  "Configuración", "<a class='btn sec link' href='/panel'>Panel</a>" if c else "")

    def configurar(self, body):
        o = self.headers.get("Origin")
        if o and o != self.propio():
            return self.responder(403, "Origen no permitido")
        f = {k: v[0].strip() for k, v in urllib.parse.parse_qs(body.decode("utf-8", "replace")).items()}
        c = leer_cfg()
        ip = self.ip()
        if bloqueado(ip):
            return self.form("Demasiados intentos. Espera 15 minutos.")
        if c:
            if not iguales(f.get("actual"), c["clave"]):
                fallo(ip)
                return self.form("Tu clave de administrador no coincide.", f)
        else:
            cod = leer_codigo()
            if not cod:
                return self.form("Falta el código de instalación. En la terminal del servidor corre: sudo bash instalar.sh")
            if not iguales(f.get("codigo", "").upper(), cod.upper()):
                fallo(ip)
                return self.form("El código de instalación no coincide (es el que salió en la terminal al instalar).", f)
        user = f.get("user") or (c.get("user") if c else "")
        pw = f.get("pass") or (c.get("pass") if c else "")
        clave = f.get("clave", "")
        if not user or not pw:
            return self.form("Escribe tu usuario y contraseña de OpenF1.", f)
        if len(clave) < 6 or any(ch.isspace() for ch in clave):
            return self.form("La clave de administrador debe tener 6 o más letras o números, sin espacios.", f)
        guardar_cfg({"user": user, "pass": pw, "clave": clave})
        _cfg["at"] = 0
        motor.tok = ""
        if not motor.token(forzar=True):
            aviso = motor.err or "OpenF1 no aceptó la cuenta"
            if c:
                guardar_cfg(c)
            else:
                try:
                    os.remove(CFG)
                except OSError:
                    pass
                _cfg["at"] = 0
            return self.form(aviso + ". Revisa usuario y contraseña.", f)
        try:
            os.remove(CODIGO)
        except OSError:
            pass
        base = self.propio()
        self.html(200,
                  "<h1>Servidor listo</h1>"
                  "<p class=sub>La conexión con OpenF1 funciona. El motor se enciende una hora antes de cada sesión y se apaga una hora después.</p>"
                  "<div class=card><div class=in><label>Página pública</label><code>" + base + "/</code>"
                  "<label>Transmisión (OBS · Fuente Navegador 1920 × 1080)</label><code>" + base + "/tv</code>"
                  "<label>Panel del servidor</label><code>" + base + "/panel</code>"
                  "<a class=btn href='/panel'>Ir al panel</a></div></div>", "Listo")


# ---------------- panel de administración ----------------
def _ses_leer():
    try:
        with open(SESIONES, encoding="utf-8") as f:
            d = json.load(f)
        now = time.time()
        return {k: v for k, v in d.items() if v > now}
    except Exception:
        return {}


def _ses_guardar(d):
    tmp = SESIONES + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, SESIONES)


def _h(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def _leer_json(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _pedir(accion, **extra):
    os.makedirs(ACT, exist_ok=True)
    tmp = os.path.join(ACT, "pedido.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dict(accion=accion, at=int(time.time() * 1000), **extra), f)
    tmp2 = os.path.join(ACT, "resultado.tmp")
    with open(tmp2, "w", encoding="utf-8") as f:
        json.dump({"estado": "pendiente", "mensaje": "Esperando al servidor…", "at": int(time.time() * 1000)}, f, ensure_ascii=False)
    os.replace(tmp2, os.path.join(ACT, "resultado.json"))
    os.replace(tmp, os.path.join(ACT, "pedido.json"))


def validar_zip(datos):
    """Revisa que el zip sea una actualización de este servidor. Regresa (versión, error)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(datos))
    except zipfile.BadZipFile:
        return None, "El archivo no es un .zip válido."
    nombres = zf.namelist()
    total = sum(i.file_size for i in zf.infolist())
    if total > 150 * 1024 * 1024:
        return None, "El zip es demasiado grande."
    for n in nombres:
        if n.startswith("/") or ".." in n.split("/") or "\\" in n:
            return None, "El zip trae rutas no permitidas."
    inst = [n for n in nombres if n.endswith("instalar.sh") and n.count("/") <= 2]
    if not inst:
        return None, "Este zip no es una actualización del live timing (falta instalar.sh)."
    base = inst[0][: -len("instalar.sh")]
    for req in ("servidor.py", "motor.py", "web/index.html"):
        if base + req not in nombres:
            return None, "Al zip le falta " + req + "."
    m = re.search(r'^VERSION = "([^"]+)"', zf.read(base + "servidor.py").decode("utf-8", "replace"), re.M)
    return (m.group(1) if m else "?"), None


PANEL_JS = r"""
const $ = (id) => document.getElementById(id);
const H = { "X-PDMX": "panel" };
const api = async (ruta, opt = {}) => {
  const r = await fetch("/panel/api/" + ruta, { credentials: "same-origin", ...opt, headers: { ...H, ...(opt.headers || {}) } });
  if (r.status === 401) { location.reload(); throw new Error("sesión"); }
  return r;
};
const dur = (s) => s == null ? "—" : s < 60 ? s + " s" : s < 3600 ? Math.round(s / 60) + " min" : Math.round(s / 3600) + " h";
const fecha = (ms) => ms ? new Date(ms).toLocaleString("es-MX", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
let trabajando = false, ultimoAt = 0;

async function estado() {
  let j;
  try { j = await (await api("estado")).json(); } catch { $("srv").className = "pill err"; $("srv").innerHTML = "<i></i>SIN RESPUESTA"; return; }
  const m = j.motor || {};
  $("ver").textContent = j.version;
  $("motor").textContent = m.motor_prendido ? "Encendido" : "En espera";
  $("motor_s").textContent = m.motor_prendido ? "Recibiendo la sesión" : "Se enciende 1 h antes de cada sesión";
  $("ses").textContent = m.sesion || "—";
  $("rt").textContent = m.motor_prendido ? (m.tiempo_real ? "Conectado" : "Respaldo") : "—";
  $("rt_s").textContent = m.ultimo_dato_hace_s != null ? "Último dato hace " + dur(m.ultimo_dato_hace_s) : "Sin datos recientes";
  const p = $("srv");
  if (m.aviso && m.motor_prendido) { p.className = "pill warn"; p.innerHTML = "<i></i>REVISAR"; $("aviso").textContent = m.aviso; $("aviso").style.display = ""; }
  else { p.className = "pill ok"; p.innerHTML = "<i></i>EN LÍNEA"; $("aviso").style.display = "none"; }
  const rp = j.respaldo;
  $("ant_info").textContent = rp ? "Versión " + (rp.version || "?") + " · guardada el " + fecha(rp.at) : "Todavía no hay una versión anterior guardada.";
  $("ant").disabled = !rp;
  const r = j.resultado;
  if (r && r.at >= ultimoAt) mostrarResultado(r);
}
function mostrarResultado(r) {
  const enCurso = r.estado === "pendiente" || r.estado === "trabajando";
  const msg = $("res");
  msg.className = "msg " + (r.estado === "listo" ? "ok" : r.estado === "error" ? "err" : "");
  msg.textContent = r.mensaje + (r.estado === "listo" && r.version ? " Versión " + r.version + "." : "") + (r.at && !enCurso ? " · " + fecha(r.at) : "");
  if (trabajando && !enCurso) { trabajando = false; habilitar(true); verLog(); }
  if (enCurso) { trabajando = true; habilitar(false); }
}
function habilitar(si) { for (const id of ["subir", "ant", "rei"]) $(id).disabled = !si || (id === "subir" && !archivo); }
async function verLog() { try { $("log").textContent = await (await api("log")).text() || "Sin registro."; } catch {} }

let archivo = null;
$("zip").addEventListener("change", (e) => {
  archivo = e.target.files[0] || null;
  $("drop").classList.toggle("lleno", !!archivo);
  $("drop_t").textContent = archivo ? archivo.name : "Elegir archivo .zip";
  $("drop_s").textContent = archivo ? (archivo.size / 1048576).toFixed(1) + " MB" : "La actualización que te enviaron";
  $("subir").disabled = !archivo;
  $("res").textContent = ""; $("res").className = "msg";
});
$("subir").addEventListener("click", () => {
  if (!archivo) return;
  habilitar(false); trabajando = true; ultimoAt = Date.now();
  const x = new XMLHttpRequest();
  x.open("POST", "/panel/api/subir");
  x.setRequestHeader("X-PDMX", "panel"); x.setRequestHeader("Content-Type", "application/zip");
  $("barra").style.display = "block";
  x.upload.onprogress = (e) => { if (e.lengthComputable) $("barra").firstChild.style.width = (100 * e.loaded / e.total) + "%"; };
  x.onload = () => {
    let j = {}; try { j = JSON.parse(x.responseText); } catch {}
    $("barra").style.display = "none"; $("barra").firstChild.style.width = "0";
    if (x.status === 401) return location.reload();
    if (x.status !== 200) { trabajando = false; habilitar(true); $("res").className = "msg err"; $("res").textContent = j.error || "No se pudo subir el archivo."; return; }
    $("res").className = "msg"; $("res").textContent = "Archivo recibido (versión " + j.version + "). Instalando…";
  };
  x.onerror = () => { trabajando = false; habilitar(true); $("barra").style.display = "none"; $("res").className = "msg err"; $("res").textContent = "Se cortó la conexión al subir. Intenta de nuevo."; };
  $("res").className = "msg"; $("res").textContent = "Subiendo…";
  x.send(archivo);
});
function confirmar(btn, texto, accion) {
  let armado = false, t = null;
  const original = btn.textContent;
  btn.addEventListener("click", async () => {
    if (!armado) { armado = true; btn.textContent = texto; t = setTimeout(() => { armado = false; btn.textContent = original; }, 4000); return; }
    clearTimeout(t); armado = false; btn.textContent = original;
    habilitar(false); trabajando = true; ultimoAt = Date.now();
    const r = await api("accion", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ accion }) });
    if (!r.ok) { trabajando = false; habilitar(true); $("res").className = "msg err"; $("res").textContent = "No se pudo enviar la orden."; }
  });
}
confirmar($("ant"), "Toca otra vez para confirmar", "anterior");
confirmar($("rei"), "Toca otra vez para confirmar", "reiniciar");
$("salir").addEventListener("click", async () => { await api("salir", { method: "POST" }); location.reload(); });
$("vlog").addEventListener("toggle", (e) => { if (e.target.open) verLog(); });
estado(); setInterval(estado, 2500);
"""

LOGIN_JS = r"""
document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const b = document.getElementById("b"), m = document.getElementById("m");
  b.disabled = true; m.textContent = "";
  try {
    const r = await fetch("/panel/api/entrar", { method: "POST", credentials: "same-origin", headers: { "X-PDMX": "panel", "Content-Type": "application/json" }, body: JSON.stringify({ clave: document.getElementById("c").value }) });
    const j = await r.json().catch(() => ({}));
    if (r.ok) return location.reload();
    m.textContent = j.error || "No se pudo entrar.";
  } catch { m.textContent = "Sin conexión con el servidor."; }
  b.disabled = false;
});
"""


def _panel_metodos():
    def sesion(self):
        ck = self.headers.get("Cookie") or ""
        m = re.search(r"(?:^|;\s*)pdmx_panel=([A-Za-z0-9_\-]{20,})", ck)
        return bool(m and _h(m.group(1)) in _ses_leer())

    def guardia(self, post=True):
        """Solo peticiones del propio panel: sesión + encabezado propio (bloquea envíos desde otros sitios)."""
        if self.headers.get("X-PDMX") != "panel":
            self.responder(403, "No permitido")
            return False
        o = self.headers.get("Origin")
        if post and o and o != self.propio():
            self.responder(403, "Origen no permitido")
            return False
        return True

    def json_out(self, status, obj, extra=None):
        self.responder(status, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", dict({"Cache-Control": "no-store"}, **(extra or {})))

    def panel(self):
        c = leer_cfg()
        if not c:
            return self.responder(302, "", extra={"Location": "/configurar"})
        if not self.sesion():
            return self.html(200,
                             "<h1>Panel del servidor</h1><p class=sub>Entra con tu clave de administrador.</p>"
                             "<form id=f class=card><div class=in><label>Clave de administrador</label>"
                             "<input id=c type=password autocomplete=current-password required autofocus>"
                             "<button id=b>Entrar</button><div id=m class=err></div></div></form>"
                             "<script>" + LOGIN_JS + "</script>", "Panel")
        base = self.propio()
        self.html(200,
                  "<h1>Panel del servidor</h1><p class=sub>Estado del live timing, actualizaciones y mantenimiento.</p>"
                  "<div class=card><h2>ESTADO <span id=srv class=pill><i></i>…</span></h2>"
                  "<div class=kv><div><b id=ver>—</b><span>Versión instalada</span></div>"
                  "<div><b id=motor>—</b><span id=motor_s>Motor</span></div>"
                  "<div><b id=ses>—</b><span>Sesión</span></div>"
                  "<div><b id=rt>—</b><span id=rt_s>Tiempo real</span></div></div>"
                  "<div class=in id=aviso style='display:none;color:var(--yellow);font-size:14px'></div></div>"
                  "<div class=card><h2>ACTUALIZAR</h2><div class=in>"
                  "<label class=drop id=drop><input id=zip type=file accept='.zip,application/zip' hidden>"
                  "<b id=drop_t>Elegir archivo .zip</b><span id=drop_s>La actualización que te enviaron</span></label>"
                  "<button id=subir disabled>Instalar actualización</button>"
                  "<div class=barra id=barra><i></i></div><div class=msg id=res></div>"
                  "<p class=nota>Antes de instalar se guarda la versión actual. Si algo falla, el servidor regresa solo a la versión anterior.</p>"
                  "<details id=vlog><summary>Detalle técnico de la última operación</summary><pre id=log>…</pre></details>"
                  "</div></div>"
                  "<div class=card><h2>VERSIÓN ANTERIOR</h2><div class=in><p class=nota id=ant_info style='margin:0'>—</p>"
                  "<button id=ant class=sec disabled>Volver a la versión anterior</button></div></div>"
                  "<div class=card><h2>MANTENIMIENTO</h2><div class=in>"
                  "<button id=rei class=peligro>Reiniciar el servidor</button>"
                  "<a class='btn sec' href='/configurar'>Cuenta de OpenF1 y clave</a></div></div>"
                  "<div class=card><h2>ENLACES</h2><div class=in><div class=links>"
                  "<a href='/' target=_blank>Página pública</a><a href='/tv' target=_blank>Transmisión OBS</a><a href='/estado' target=_blank>Estado (JSON)</a></div>"
                  "<code>" + base + "</code></div></div>"
                  "<script>" + PANEL_JS + "</script>", "Panel", "<button id=salir class='sec link'>Salir</button>")

    def panel_api_get(self, p):
        if not self.guardia(post=False):
            return
        if not self.sesion():
            return self.json_out(401, {"error": "sesión"})
        if p == "/panel/api/estado":
            return self.json_out(200, {"version": VERSION, "motor": motor.resumen(),
                                       "resultado": _leer_json(os.path.join(ACT, "resultado.json")),
                                       "respaldo": _leer_json(os.path.join(ACT, "respaldo.json"))})
        if p == "/panel/api/log":
            try:
                with open(os.path.join(ACT, "ultima.log"), encoding="utf-8", errors="replace") as f:
                    t = f.read()[-20000:]
            except OSError:
                t = ""
            return self.responder(200, t, "text/plain; charset=utf-8", {"Cache-Control": "no-store"})
        self.json_out(404, {"error": "no existe"})

    def panel_api_post(self, p, body):
        if not self.guardia():
            return
        ip = self.ip()
        if p == "/panel/api/entrar":
            if bloqueado(ip):
                return self.json_out(429, {"error": "Demasiados intentos. Espera 15 minutos."})
            try:
                clave = json.loads(body or b"{}").get("clave", "")
            except Exception:
                clave = ""
            c = leer_cfg()
            if not c or not iguales(clave, c.get("clave")):
                fallo(ip)
                return self.json_out(401, {"error": "Clave incorrecta."})
            fallos.pop(ip, None)
            tok = secrets.token_urlsafe(32)
            d = _ses_leer()
            d[_h(tok)] = time.time() + 12 * 3600
            _ses_guardar(d)
            seguro = "; Secure" if self.propio().startswith("https") else ""
            return self.json_out(200, {"ok": True}, {"Set-Cookie": "pdmx_panel=" + tok + "; Path=/panel; HttpOnly; SameSite=Strict; Max-Age=43200" + seguro})
        if not self.sesion():
            return self.json_out(401, {"error": "sesión"})
        if p == "/panel/api/salir":
            ck = re.search(r"(?:^|;\s*)pdmx_panel=([A-Za-z0-9_\-]{20,})", self.headers.get("Cookie") or "")
            d = _ses_leer()
            if ck:
                d.pop(_h(ck.group(1)), None)
            _ses_guardar(d)
            return self.json_out(200, {"ok": True}, {"Set-Cookie": "pdmx_panel=; Path=/panel; Max-Age=0"})
        if p == "/panel/api/accion":
            try:
                acc = json.loads(body or b"{}").get("accion")
            except Exception:
                acc = None
            if acc not in ("anterior", "reiniciar"):
                return self.json_out(400, {"error": "Acción no válida."})
            if os.path.exists(os.path.join(ACT, "pedido.json")):
                return self.json_out(409, {"error": "Ya hay una operación en curso."})
            _pedir(acc)
            return self.json_out(200, {"ok": True})
        self.json_out(404, {"error": "no existe"})

    def panel_subir(self, n):
        if not self.guardia():
            self.close_connection = True
            return
        if not self.sesion():
            self.close_connection = True
            return self.json_out(401, {"error": "sesión"})
        if n <= 0 or n > MAX_ZIP:
            self.close_connection = True
            return self.json_out(413, {"error": "El archivo debe pesar menos de 40 MB."})
        if os.path.exists(os.path.join(ACT, "pedido.json")):
            self.close_connection = True
            return self.json_out(409, {"error": "Ya hay una operación en curso."})
        datos, falta = [], n
        while falta > 0:
            trozo = self.rfile.read(min(falta, 1 << 20))
            if not trozo:
                break
            datos.append(trozo)
            falta -= len(trozo)
        datos = b"".join(datos)
        if len(datos) != n:
            return self.json_out(400, {"error": "El archivo llegó incompleto. Intenta de nuevo."})
        ver, err = validar_zip(datos)
        if err:
            return self.json_out(400, {"error": err})
        os.makedirs(ACT, exist_ok=True)
        tmp = os.path.join(ACT, "nuevo.zip.tmp")
        with open(tmp, "wb") as f:
            f.write(datos)
        os.replace(tmp, os.path.join(ACT, "nuevo.zip"))
        _pedir("actualizar", version=ver)
        self.json_out(200, {"ok": True, "version": ver})

    for f in (sesion, guardia, json_out, panel, panel_api_get, panel_api_post, panel_subir):
        setattr(Handler, f.__name__, f)


_panel_metodos()


def main():
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(PUB, exist_ok=True)
    threading.Thread(target=motor.correr, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print("Servidor PDMX %s en 127.0.0.1:%d (configurado: %s)" % (VERSION, PORT, "sí" if leer_cfg() else "no"), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
