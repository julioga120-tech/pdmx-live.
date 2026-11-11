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
import hmac
import json
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
VERSION = "3.0"

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

ESTILO = """<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#070a10;color:#e8eef5;font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:480px;margin:0 auto;padding:26px 16px 40px}h1{font-size:23px;letter-spacing:.5px;margin:0 0 6px}
p{color:#9aa7b7;font-weight:600;margin:0 0 14px}label{display:block;font-size:12px;font-weight:700;color:#9aa7b7;letter-spacing:1px;margin:14px 0 5px}
input{width:100%;padding:12px;background:#080d14;border:1px solid #2a3b4f;border-radius:6px;color:#e8eef5;font-size:16px}
button,.btn{display:block;width:100%;margin-top:16px;padding:13px;border:0;border-radius:6px;background:#0090b4;color:#fff;font-weight:800;letter-spacing:1.2px;font-size:15px;text-align:center;text-decoration:none;cursor:pointer}
.ghost{background:transparent;border:1px solid #2d3d52}.err{color:#ff8a9b;font-weight:700;margin-top:12px}.ok{color:#5be39a;font-weight:800}
code{display:block;word-break:break-all;background:#0d1520;border:1px solid #233246;border-radius:6px;padding:10px;margin:8px 0;color:#7fd8ff;font-size:14px}
small{color:#8592a3}</style>"""
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

    def html(self, status, body):
        self.responder(status, "<!doctype html><html lang=es><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                       "<title>Servidor Pure Driving MX</title>" + ESTILO + "<main>" + body + "</main></html>", "text/html; charset=utf-8",
                       {"Cache-Control": "no-store", "X-Frame-Options": "DENY"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        p = urllib.parse.urlsplit(self.path).path
        if p == "/estado":
            return self.responder(200, json.dumps(dict(motor.resumen(), version=VERSION, configurado=bool(leer_cfg())), ensure_ascii=False, indent=1),
                                  "application/json; charset=utf-8", {"Cache-Control": "no-store"})
        if p == "/configurar":
            return self.form()
        if p.startswith("/d/"):
            return self.archivo(PUB, p[2:], datos=True)
        return self.archivo(WEB_DIR, p)

    def do_POST(self):
        p = urllib.parse.urlsplit(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536:
            self.close_connection = True
            return self.responder(413, "Demasiado grande")
        body = self.rfile.read(n) if n > 0 else b""
        if p == "/configurar":
            return self.configurar(body)
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
        esc = lambda s: str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")  # noqa: E731
        actual = ('<label>TU CLAVE DE ADMINISTRADOR</label><input name=actual type=password autocomplete=off required>'
                  '<p style="margin-top:8px"><small>Deja la contraseña vacía para no cambiarla.</small></p>') if c else (
                  '<label>CÓDIGO DE INSTALACIÓN (SALIÓ EN LA TERMINAL)</label><input name=codigo autocomplete=off autocapitalize=characters spellcheck=false required>')
        self.html(200,
                  "<h1>" + ("Cambiar la cuenta del servidor" if c else "Configura tu servidor") + "</h1>"
                  "<p>Tu cuenta de OpenF1 se guarda <b>solo en este servidor</b>. La página es pública: quien entra solo ve los datos, nunca tu cuenta.</p>"
                  "<form method=post action=/configurar>" + actual +
                  '<label>USUARIO O CORREO DE OPENF1</label><input name=user autocomplete=username autocapitalize=none spellcheck=false value="' + esc(v.get("user") or (c.get("user") if c else "")) + '"' + ("" if c else " required") + ">"
                  "<label>CONTRASEÑA DE OPENF1</label><input name=pass type=password autocomplete=current-password" + ("" if c else " required") + ">"
                  '<label>' + ("NUEVA CLAVE DE ADMINISTRADOR" if c else "INVENTA UNA CLAVE DE ADMINISTRADOR (6 O MÁS)") + '</label><input name=clave type=password autocomplete=new-password required minlength=6>'
                  "<p style='margin-top:8px'><small>La clave de administrador solo sirve para volver a entrar aquí.</small></p>"
                  "<button>GUARDAR Y PROBAR</button></form>" + ('<div class=err>' + esc(err) + "</div>" if err else ""))

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
                  "<h1 class=ok>✓ Listo, tu servidor funciona</h1>"
                  "<p>El motor ya está conectado con OpenF1. Se prende solo 1 hora antes de cada sesión y se apaga 1 hora después.</p>"
                  "<p>Página pública (celular y compu):</p><code>" + base + "/</code>"
                  "<p>Transmisión (OBS → Fuente Navegador, 1920 × 1080):</p><code>" + base + "/tv</code>"
                  "<p>Cómo va el motor:</p><code>" + base + "/estado</code>"
                  "<a class=btn href='/'>ABRIR LA PÁGINA</a>")


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
