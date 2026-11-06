#!/usr/bin/env python3
"""Servidor Pure Driving MX (Oracle Cloud gratis): tu página + puente a OpenF1, prendido 24/7.

- Sirve las dos páginas desde este servidor: celular (/) y transmisión para OBS (/tv).
- Puente: durante las sesiones OpenF1 no le contesta al navegador; el servidor sí.
- Modo nube: tu cuenta de OpenF1 se guarda SOLO aquí (/var/lib/pdmx/config.json, permisos 600).
  Tus celulares y la compu entran con tu CLAVE y reciben un token de OpenF1 que dura máximo 1 hora.
- Primera vez: abre https://TU-SERVIDOR/configurar y escribe tu cuenta de OpenF1 y tu CLAVE.

Sin dependencias: solo Python 3. Lo instala puente/oracle.sh.
"""
import hmac
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPENF1 = os.environ.get("PDMX_OPENF1", "https://api.openf1.org").rstrip("/")
ORIGENES = {"https://julioga120-tech.github.io"}
DATA = os.environ.get("PDMX_DATA", "/var/lib/pdmx")
CFG = os.path.join(DATA, "config.json")
CACHE_DIR = os.path.join(DATA, "pagina")
WEB_DIR = os.environ.get("PDMX_WEB", "/opt/pdmx/web")   # la página (celular y transmisión) vive aquí
PORT = int(os.environ.get("PDMX_PORT", "8787"))
VERSION = "2026-11-05"

lock = threading.Lock()
tok = {"t": "", "exp": 0.0}      # token de tu cuenta (en memoria, se renueva solo)
fallos = {}                      # ip -> (intentos con clave equivocada, desde cuándo)
cache = {}                       # ruta -> (hora, content-type, bytes) de la página


def leer_cfg():
    try:
        with open(CFG, encoding="utf-8") as f:
            c = json.load(f)
        return c if c.get("user") and c.get("pass") and c.get("clave") else {}
    except Exception:
        return {}


def guardar_cfg(c):
    os.makedirs(DATA, exist_ok=True)
    tmp = CFG + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(c, f)
    os.replace(tmp, CFG)


def pedir(url, data=None, headers=None, timeout=25):
    """Regresa (status, content_type, bytes). No lanza por HTTP 4xx/5xx."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "pdmx-servidor/" + VERSION, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        return e.code, (e.headers.get("Content-Type", "") if e.headers else ""), body


def token_openf1(user, pw):
    body = urllib.parse.urlencode({"username": user, "password": pw}).encode()
    st, _, b = pedir(OPENF1 + "/token", body, {"Content-Type": "application/x-www-form-urlencoded"}, 20)
    if st != 200:
        return st, None
    try:
        j = json.loads(b or b"{}")
    except Exception:
        return 502, None
    return (200, j) if j.get("access_token") else (502, None)


def token_nube(c):
    """Token vigente de tu cuenta guardada; lo renueva 5 min antes de que venza."""
    with lock:
        if tok["t"] and time.time() < tok["exp"] - 300:
            return 200
        st, j = token_openf1(c["user"], c["pass"])
        if st != 200:
            return st
        tok["t"] = j["access_token"]
        tok["exp"] = time.time() + float(j.get("expires_in") or 3600)
        return 200


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


ESTILO = """<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#070a10;color:#e8eef5;font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:460px;margin:0 auto;padding:26px 16px 40px}h1{font-size:23px;letter-spacing:.5px;margin:0 0 6px}
p{color:#9aa7b7;font-weight:600;margin:0 0 14px}label{display:block;font-size:12px;font-weight:700;color:#9aa7b7;letter-spacing:1px;margin:14px 0 5px}
input{width:100%;padding:12px;background:#080d14;border:1px solid #2a3b4f;border-radius:6px;color:#e8eef5;font-size:16px}
button,.btn{display:block;width:100%;margin-top:16px;padding:13px;border:0;border-radius:6px;background:#0090b4;color:#fff;font-weight:800;letter-spacing:1.2px;font-size:15px;text-align:center;text-decoration:none;cursor:pointer}
.ghost{background:transparent;border:1px solid #2d3d52}.err{color:#ff8a9b;font-weight:700;margin-top:12px}.ok{color:#5be39a;font-weight:800}
code{display:block;word-break:break-all;background:#0d1520;border:1px solid #233246;border-radius:6px;padding:10px;margin:8px 0;color:#7fd8ff;font-size:14px}
small{color:#8592a3}</style>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PDMX"
    sys_version = ""
    timeout = 60

    def log_message(self, fmt, *args):  # no se registra nada (ni claves ni tokens)
        pass

    # ---------- utilidades ----------
    def ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def propio(self):
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",")[0].strip()
        proto = "http" if host.startswith(("127.0.0.1", "localhost")) else "https"
        return proto + "://" + host

    def origen_ok(self):
        o = self.headers.get("Origin")
        return not o or o in ORIGENES or o == self.propio()

    def responder(self, status, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        o = self.headers.get("Origin") or ""
        self.send_response(status)
        if o and (o in ORIGENES or o == self.propio()):
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, status, obj):
        self.responder(status, json.dumps(obj), "application/json", {"Cache-Control": "no-store"})

    def html(self, status, body):
        self.responder(status, "<!doctype html><html lang=es><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                       "<title>Tu servidor PDMX</title>" + ESTILO + "<main>" + body + "</main></html>", "text/html; charset=utf-8",
                       {"Cache-Control": "no-store", "X-Frame-Options": "DENY"})

    def cuerpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536:
            self.close_connection = True
            return b""
        return self.rfile.read(n) if n > 0 else b""

    # ---------- rutas ----------
    def do_OPTIONS(self):
        self.responder(204, b"")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        p = u.path
        if p.startswith("/v1/"):
            return self.openf1_get(p, u.query)
        if p == "/estado":
            return self.json(200, {"servidor": "ok", "version": VERSION, "nube": bool(leer_cfg())})
        if p == "/configurar":
            return self.form()
        if p in ("/", "/index.html", "/tv", "/tv.html") and not leer_cfg():
            return self.responder(302, b"", extra={"Location": "/configurar"})
        return self.pagina(p)

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        body = self.cuerpo()
        if u.path == "/token":
            return self.token(body)
        if u.path == "/configurar":
            return self.configurar(body)
        self.responder(405, "Método no permitido")

    # ---------- puente a OpenF1 ----------
    def openf1_get(self, p, q):
        if not self.origen_ok():
            return self.responder(403, "Origen no permitido")
        h = {}
        a = self.headers.get("Authorization")
        if a:
            h["Authorization"] = a
        try:
            st, ct, b = pedir(OPENF1 + p + ("?" + q if q else ""), None, h, 25)
        except Exception:
            return self.json(502, {"error": "OpenF1 no respondió"})
        self.responder(st, b, ct or "application/json", {"Cache-Control": "no-store"})

    def token(self, body):
        if not self.origen_ok():
            return self.responder(403, "Origen no permitido")
        f = urllib.parse.parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        if "clave" in f:
            # MODO NUBE: el dispositivo manda tu CLAVE y recibe un token de tu cuenta guardada
            c = leer_cfg()
            if not c:
                return self.json(501, {"error": "sin-nube"})
            ip = self.ip()
            if bloqueado(ip):
                return self.json(429, {"error": "espera"})
            if not iguales(f["clave"][0], c["clave"]):
                fallo(ip)
                return self.json(401, {"error": "clave"})
            fallos.pop(ip, None)
            try:
                st = token_nube(c)
            except Exception:
                return self.json(504, {"error": "openf1"})
            if st != 200:
                return self.json(502, {"error": "cuenta" if st in (400, 401, 403, 422) else "openf1"})
            return self.json(200, {"access_token": tok["t"], "token_type": "bearer",
                                   "expires_in": max(60, int(tok["exp"] - time.time())), "username": c["user"]})
        # paso directo: usuario y contraseña que manda la página
        try:
            st, ct, b = pedir(OPENF1 + "/token", body, {"Content-Type": "application/x-www-form-urlencoded"}, 20)
        except Exception:
            return self.json(502, {"error": "OpenF1 no respondió"})
        self.responder(st, b, ct or "application/json", {"Cache-Control": "no-store"})

    # ---------- configuración (primera vez) ----------
    def form(self, err="", vals=None):
        c = leer_cfg()
        v = vals or {}
        esc = lambda s: str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
        actual = ('<label>TU CLAVE ACTUAL</label><input name=actual type=password autocomplete=off required>'
                  '<p style="margin-top:8px"><small>Para cambiar algo necesitas tu clave actual. Deja la contraseña vacía para no cambiarla.</small></p>') if c else ""
        self.html(200,
                  "<h1>" + ("Cambiar mi servidor" if c else "Configura tu servidor") + "</h1>"
                  "<p>Tu cuenta de OpenF1 se guarda <b>solo en este servidor</b>. Tus celulares y la compu entran con la CLAVE que inventes aquí.</p>"
                  "<form method=post action=/configurar>" + actual +
                  '<label>USUARIO O CORREO DE OPENF1</label><input name=user autocomplete=username autocapitalize=none spellcheck=false value="' + esc(v.get("user") or (c.get("user") if c else "")) + '"' + ("" if c else " required") + ">"
                  "<label>CONTRASEÑA DE OPENF1</label><input name=pass type=password autocomplete=current-password" + ("" if c else " required") + ">"
                  '<label>INVENTA TU CLAVE (6 O MÁS LETRAS O NÚMEROS)</label><input name=clave autocomplete=off autocapitalize=none spellcheck=false required minlength=6 value="' + esc(v.get("clave")) + '">'
                  "<button>GUARDAR Y PROBAR</button></form>" + ('<div class=err>' + esc(err) + "</div>" if err else ""))

    def configurar(self, body):
        if not self.origen_ok():
            return self.responder(403, "Origen no permitido")
        f = {k: v[0].strip() for k, v in urllib.parse.parse_qs(body.decode("utf-8", "replace")).items()}
        c = leer_cfg()
        ip = self.ip()
        if c:
            if bloqueado(ip):
                return self.form("Demasiados intentos. Espera 15 minutos.")
            if not iguales(f.get("actual"), c["clave"]):
                fallo(ip)
                return self.form("Tu clave actual no coincide.", f)
        user = f.get("user") or (c.get("user") if c else "")
        pw = f.get("pass") or (c.get("pass") if c else "")
        clave = f.get("clave", "")
        if not user or not pw:
            return self.form("Escribe tu usuario y contraseña de OpenF1.", f)
        if len(clave) < 6 or any(ch.isspace() for ch in clave):
            return self.form("Inventa una CLAVE de 6 o más letras o números, sin espacios.", f)
        try:
            st, j = token_openf1(user, pw)
        except Exception:
            st, j = 504, None
        if st != 200:
            return self.form("OpenF1 rechazó esa cuenta: revisa usuario y contraseña." if st in (400, 401, 403, 422)
                             else "OpenF1 no respondió (" + str(st) + "). Intenta otra vez en un momento.", f)
        guardar_cfg({"user": user, "pass": pw, "clave": clave})
        with lock:
            tok["t"] = j["access_token"]
            tok["exp"] = time.time() + float(j.get("expires_in") or 3600)
        link = self.propio() + "/#k=" + urllib.parse.quote(clave, safe="")
        tv = self.propio() + "/tv#k=" + urllib.parse.quote(clave, safe="")
        self.html(200,
                  "<h1 class=ok>✓ Listo, tu servidor funciona</h1>"
                  "<p>Este es tu link. Ábrelo en tu celular, en el de tu hermano o en la compu y entra solo, sin contraseña:</p>"
                  "<code id=l>" + link.replace("&", "&amp;").replace("<", "&lt;") + "</code>"
                  "<button id=s>COMPARTIR MI LINK</button><a class='btn ghost' href='" + link.replace("'", "%27") + "'>ABRIR LA PÁGINA</a>"
                  "<p style='margin-top:22px'>Link para transmitir (OBS → Fuente Navegador, 1920 × 1080):</p>"
                  "<code>" + tv.replace("&", "&amp;").replace("<", "&lt;") + "</code>"
                  "<p style='margin-top:16px'><small>Si alguien ve tu link, cambia tu CLAVE aquí mismo (" + self.propio() + "/configurar) y vuelve a compartirlo.</small></p>"
                  "<script>document.getElementById('s').onclick=async()=>{const u=document.getElementById('l').textContent;"
                  "try{if(navigator.share){await navigator.share({title:'Pure Driving MX Live',url:u});return}}catch(e){if(e&&e.name==='AbortError')return}"
                  "try{await navigator.clipboard.writeText(u);document.getElementById('s').textContent='LINK COPIADO ✓'}catch(e){}}</script>")

    # ---------- la página: se sirve desde este mismo servidor (/opt/pdmx/web) ----------
    def pagina(self, p):
        if p == "/":
            p = "/index.html"
        if p == "/tv":
            p = "/tv.html"
        if ".." in p or not re.fullmatch(r"/[A-Za-z0-9._\-/]{1,120}", p):
            return self.responder(404, "No encontrado")
        f = os.path.join(WEB_DIR, p.strip("/"))
        hit = cache.get(p)
        try:
            mt = os.path.getmtime(f)
        except OSError:
            return self.responder(404, "No encontrado")
        if not hit or hit[0] != mt:
            ct = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                  ".png": "image/png", ".json": "application/json", ".svg": "image/svg+xml"}.get(os.path.splitext(f)[1], "application/octet-stream")
            with open(f, "rb") as fh:
                hit = (mt, ct, fh.read())
            cache[p] = hit
        body = hit[2]
        if p == "/index.html":  # la página sabe que está en tu servidor (el puente es este mismo)
            body = body.replace(b"<html lang=\"es\">", b"<html lang=\"es\" data-servidor=\"1\">", 1)
        self.responder(200, body, hit[1], {"Cache-Control": "no-cache"})


def main():
    os.makedirs(DATA, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print("Servidor PDMX en 127.0.0.1:%d (configurado: %s)" % (PORT, "sí" if leer_cfg() else "no"), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
