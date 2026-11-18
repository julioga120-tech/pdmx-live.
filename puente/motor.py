#!/usr/bin/env python3
"""Motor del servidor Pure Driving MX: el ÚNICO que habla con OpenF1.

- Sigue el calendario: se prende 60 min antes de cada sesión y se apaga 60 min después de que
  termina de verdad (si se alarga por banderas rojas, sigue prendido mientras haya actividad).
- En vivo recibe todo por MQTT (al instante) y repasa por REST cada minuto por si algo se perdió.
- Guarda la sesión completa y la publica como archivos: una foto completa (snap.json) y pedacitos
  de 1 segundo (live/<gen>/<n>.json). Las páginas solo leen esos archivos: nunca tocan OpenF1.

Solo Python 3 (sin dependencias).
"""
import base64
import collections
import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

OPENF1 = os.environ.get("PDMX_OPENF1", "https://api.openf1.org").rstrip("/")
MQTT_URLS = [u for u in os.environ.get("PDMX_MQTT", "tls://mqtt.openf1.org:8883,wss://mqtt.openf1.org:8084/mqtt").split(",") if u]
ANTES = float(os.environ.get("PDMX_ANTES", "3600"))      # se prende este tiempo antes de la sesión
DESPUES = float(os.environ.get("PDMX_DESPUES", "3600"))  # y se apaga este tiempo después de que acaba
TOPICS = ["position", "intervals", "laps", "race_control", "stints", "pit", "weather", "team_radio", "drivers", "sessions", "location"]
LIM_MIN, LIM_SEG = 48, 4   # OpenF1 (cuenta patrocinador): 60 por minuto y 6 por segundo; dejamos margen


def ms(iso):
    """Fecha ISO de OpenF1 → milisegundos."""
    if not iso:
        return 0
    try:
        s = iso.replace("Z", "+00:00")
        if "." in s:  # Python viejo no acepta fracciones de largo variable
            a, b = s.split(".", 1)
            frac = b
            tz = ""
            for sep in ("+", "-"):
                if sep in b:
                    frac, tz = b.split(sep, 1)
                    tz = sep + tz
                    break
            s = a + "." + (frac + "000000")[:6] + tz
        import datetime
        d = datetime.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return int(d.timestamp() * 1000)
    except Exception:
        return 0


def iso(t_ms):
    import datetime
    return datetime.datetime.fromtimestamp(t_ms / 1000, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- MQTT mínimo (3.1.1, solo recibir)
class Transporte:
    """TLS directo (tls://, tcp://) o WebSocket (wss://, ws://) hacia el broker."""

    def __init__(self, url, timeout=20):
        u = urllib.parse.urlsplit(url)
        self.ws = u.scheme in ("ws", "wss")
        port = u.port or {"tls": 8883, "tcp": 1883, "wss": 443, "ws": 80}[u.scheme]
        s = socket.create_connection((u.hostname, port), timeout=timeout)
        if u.scheme in ("tls", "wss"):
            s = ssl.create_default_context().wrap_socket(s, server_hostname=u.hostname)
        self.s = s
        self.buf = b""
        self.raw = b""
        if self.ws:
            key = base64.b64encode(os.urandom(16)).decode()
            req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                   "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: mqtt\r\n\r\n") % (u.path or "/", u.hostname, key)
            s.sendall(req.encode())
            head = b""
            while b"\r\n\r\n" not in head:
                c = s.recv(1024)
                if not c:
                    raise OSError("ws cerrado")
                head += c
            if b" 101 " not in head.split(b"\r\n", 1)[0]:
                raise OSError("ws rechazado: " + head[:60].decode("latin1"))
            self.raw = head.split(b"\r\n\r\n", 1)[1]

    def settimeout(self, t):
        self.s.settimeout(t)

    def send(self, data):
        if not self.ws:
            return self.s.sendall(data)
        n, mask = len(data), os.urandom(4)
        h = b"\x82" + (bytes([0x80 | n]) if n < 126 else b"\xfe" + struct.pack(">H", n) if n < 65536 else b"\xff" + struct.pack(">Q", n))
        self.s.sendall(h + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(data)))

    def _raw(self, n):
        while len(self.raw) < n:
            c = self.s.recv(65536)
            if not c:
                raise OSError("conexión cerrada")
            self.raw += c
        out, self.raw = self.raw[:n], self.raw[n:]
        return out

    def recv(self, n):
        if not self.ws:
            while len(self.buf) < n:
                c = self.s.recv(65536)
                if not c:
                    raise OSError("conexión cerrada")
                self.buf += c
        else:
            while len(self.buf) < n:  # leer cuadros de WebSocket hasta juntar n bytes
                b0, b1 = self._raw(2)
                ln = b1 & 0x7F
                if ln == 126:
                    ln = struct.unpack(">H", self._raw(2))[0]
                elif ln == 127:
                    ln = struct.unpack(">Q", self._raw(8))[0]
                mask = self._raw(4) if b1 & 0x80 else None
                p = self._raw(ln)
                if mask:
                    p = bytes(b ^ mask[i & 3] for i, b in enumerate(p))
                op = b0 & 0x0F
                if op == 8:
                    raise OSError("ws cerrado por el broker")
                if op == 9:  # ping → pong
                    self.s.sendall(b"\x8a\x80" + os.urandom(4))
                elif op in (0, 1, 2):
                    self.buf += p
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def _mstr(s):
    b = s.encode() if isinstance(s, str) else s
    return struct.pack(">H", len(b)) + b


def _pkt(first, body):
    n, ln = len(body), b""
    while True:
        d, n = n % 128, n // 128
        ln += bytes([d | (0x80 if n else 0)])
        if not n:
            break
    return bytes([first]) + ln + body


def mqtt_escuchar(url, user, password, topics, on_msg, vivo, on_estado):
    """Se conecta y entrega cada mensaje a on_msg(topic, bytes) hasta que falle o vivo() sea False."""
    t = Transporte(url)
    try:
        cid = "pdmx-" + base64.b16encode(os.urandom(5)).decode().lower()
        t.send(_pkt(0x10, _mstr("MQTT") + b"\x04\xc2" + struct.pack(">H", 30) + _mstr(cid) + _mstr(user) + _mstr(password)))
        t.settimeout(20)
        ultimo_ping = time.time()
        while vivo():
            t.settimeout(5)
            try:
                b0 = t.recv(1)[0]
            except socket.timeout:
                b0 = None
            if time.time() - ultimo_ping > 15:
                t.send(b"\xc0\x00")
                ultimo_ping = time.time()
            if b0 is None:
                continue
            t.settimeout(20)
            ln, mult = 0, 1
            while True:
                d = t.recv(1)[0]
                ln += (d & 127) * mult
                mult *= 128
                if not d & 128:
                    break
            body = t.recv(ln) if ln else b""
            tipo = b0 >> 4
            if tipo == 2:  # CONNACK
                if len(body) < 2 or body[1] != 0:
                    raise PermissionError("el broker rechazó la cuenta (código %s)" % (body[1] if len(body) > 1 else "?"))
                t.send(_pkt(0x82, struct.pack(">H", 1) + b"".join(_mstr("v1/" + x) + b"\x00" for x in topics)))
                on_estado(True)
            elif tipo == 3:  # PUBLISH
                tl = struct.unpack(">H", body[:2])[0]
                topic = body[2:2 + tl].decode("utf-8", "replace")
                off = 2 + tl + (2 if (b0 >> 1) & 3 else 0)
                on_msg(topic, body[off:])
    finally:
        on_estado(False)
        t.close()


# ---------------------------------------------------------------- el motor
class Motor:
    def __init__(self, data_dir, pub_dir, leer_cfg, log=print):
        self.data, self.pub, self.leer_cfg, self.log = data_dir, pub_dir, leer_cfg, log
        self.lock = threading.RLock()
        self.tok, self.tok_exp = "", 0.0
        self.reqs = collections.deque()
        self.year, self.year_at = [], 0.0
        self.sess, self.key = None, 0
        self.gen, self.seq = 0, 0
        self.activo = False
        self.mq_ok, self.mq_at, self.mq_msgs, self.mq_err, self.mq_i = False, 0.0, 0, "", 0
        self.mq_thread = None
        self.pend, self.pend_loc = [], []
        self.sucio, self.snap_at, self.head_at = True, 0.0, 0.0
        self.act_at = 0.0          # última actividad real de pista (vueltas, Race Control, posiciones) en vivo
        self.rec_at, self.rap_at = 0.0, 0.0
        self.res_try, self.champ_at, self.track_try = 0.0, 0.0, 0.0
        self.err = ""
        self.cambio = False
        self._vaciar()

    def _vaciar(self):
        self.drivers, self.drivers_de = {}, ""
        self.pos, self.intv, self.laps, self.stints, self.pits = {}, {}, {}, {}, {}
        self.rc, self.rc_k, self.wx, self.wx_k, self.radios, self.rad_k = [], set(), [], set(), [], set()
        self.loc = {}
        self.grid, self.result, self.track = {}, None, None
        self.cur = {}
        self.pend, self.pend_loc = [], []

    # ---------- OpenF1 por REST (con freno para respetar los límites) ----------
    def _freno(self):
        while True:
            now = time.time()
            while self.reqs and now - self.reqs[0] > 60:
                self.reqs.popleft()
            ult = [x for x in self.reqs if now - x < 1]
            if len(self.reqs) < LIM_MIN and len(ult) < LIM_SEG:
                self.reqs.append(now)
                return
            time.sleep(0.25)

    def token(self, forzar=False):
        c = self.leer_cfg()
        if not c:
            return ""
        if self.tok and not forzar and time.time() < self.tok_exp - 300:
            return self.tok
        body = urllib.parse.urlencode({"username": c["user"], "password": c["pass"]}).encode()
        req = urllib.request.Request(OPENF1 + "/token", data=body, headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "pdmx-servidor"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.loads(r.read() or b"{}")
            self.tok, self.tok_exp = j.get("access_token", ""), time.time() + float(j.get("expires_in") or 3600)
            self.err = ""
        except urllib.error.HTTPError as e:
            self.err = "OpenF1 rechazó la cuenta (HTTP %s)" % e.code
            self.tok = ""
        except Exception as e:
            self.err = "sin conexión con OpenF1: %s" % e
        return self.tok

    def q(self, path):
        """Consulta a OpenF1. Regresa lista (vacía si no hay datos) o None si falló."""
        for intento in (0, 1):
            self._freno()
            h = {"User-Agent": "pdmx-servidor", "Accept": "application/json"}
            tk = self.token(forzar=intento == 1)
            if tk:
                h["Authorization"] = "Bearer " + tk
            try:
                with urllib.request.urlopen(urllib.request.Request(OPENF1 + "/v1/" + path, headers=h), timeout=30) as r:
                    j = json.loads(r.read() or b"[]")
                self.rest_at = time.time()
                if self.err.startswith("OpenF1 HTTP") or self.err.startswith("OpenF1 no respondió"):
                    self.err = ""
                return j if isinstance(j, list) else []
            except urllib.error.HTTPError as e:
                if e.code in (404, 422):
                    return []
                if e.code == 401 and intento == 0:
                    continue
                if e.code == 429:
                    time.sleep(5)
                self.err = "OpenF1 HTTP %s en %s" % (e.code, path.split("?")[0])
                return None
            except Exception as e:
                self.err = "OpenF1 no respondió (%s)" % type(e).__name__
                if intento == 0:
                    time.sleep(1)
                    continue
                return None
        return None

    # ---------- guardar cada dato (REST y MQTT llegan repetidos y desordenados) ----------
    def aplicar(self, topic, r, emitir=True):
        """Mete un dato a la memoria. Si es nuevo o cambió, lo manda a las páginas."""
        n = r.get("driver_number")
        nuevo = False
        if topic == "location":
            if not n or (not r.get("x") and not r.get("y")):
                return
            t = ms(r.get("date"))
            a = self.loc.setdefault(n, collections.deque(maxlen=160))
            if a and t <= a[-1][0]:
                return
            a.append((t, r["x"], r["y"]))
            if emitir:
                self.pend_loc.append([n, t, r["x"], r["y"]])
            return
        if topic == "position":
            o = self.pos.get(n)
            if n and r.get("position") and (not o or (r.get("date") or "") > (o.get("date") or "")):
                self.pos[n] = r
                nuevo = True
        elif topic == "intervals":
            o = self.intv.get(n)
            if n and (not o or (r.get("date") or "") > (o.get("date") or "")):
                self.intv[n] = r
                nuevo = True
        elif topic == "laps":
            ln = r.get("lap_number")
            if n and ln:
                o = self.laps.setdefault(n, {}).setdefault(ln, {})
                for k, v in r.items():
                    if v is not None and o.get(k) != v:
                        o[k] = v
                        nuevo = True
                r = o
        elif topic == "race_control":
            k = (r.get("date") or "") + "|" + (r.get("message") or "")
            if k not in self.rc_k:
                self.rc_k.add(k)
                self.rc.append(r)
                if len(self.rc) > 1 and (self.rc[-2].get("date") or "") > (r.get("date") or ""):
                    self.rc.sort(key=lambda x: x.get("date") or "")
                nuevo = True
        elif topic == "stints":
            sn = r.get("stint_number")
            if n and sn is not None:
                o = self.stints.setdefault(n, {})
                if o.get(sn) != r:
                    o[sn] = r
                    nuevo = True
        elif topic == "pit":
            if n:
                a = self.pits.setdefault(n, [])
                i = next((i for i, x in enumerate(a) if x.get("lap_number") == r.get("lap_number")), -1)
                if i < 0:
                    a.append(r)
                    nuevo = True
                elif a[i] != r:
                    a[i] = r
                    nuevo = True
        elif topic == "weather":
            k = r.get("date")
            if k and k not in self.wx_k:
                self.wx_k.add(k)
                self.wx.append(r)
                nuevo = True
        elif topic == "team_radio":
            k = r.get("recording_url")
            if k and k not in self.rad_k:
                self.rad_k.add(k)
                self.radios.append(r)
                nuevo = True
        elif topic == "drivers":
            if n and self.drivers.get(n) != r:
                self.drivers[n] = dict(self.drivers.get(n) or {}, **r)
                nuevo = True
        elif topic == "sessions":
            if r.get("session_key") == self.key and self.sess is not None:
                cambios = {k: v for k, v in r.items() if v is not None and self.sess.get(k) != v}
                if cambios:
                    self.sess.update(cambios)
                    nuevo = True
                    r = self.sess
        if nuevo:
            self.sucio = True
            if emitir:
                self.pend.append([topic, r])

    def _mqtt_msg(self, topic, raw):
        try:
            r = json.loads(raw)
        except Exception:
            return
        if not isinstance(r, dict):
            return
        t = topic[3:] if topic.startswith("v1/") else topic
        with self.lock:
            self.mq_msgs += 1
            self.mq_at = time.time()
            k = r.get("session_key")
            if k and self.key and k != self.key:
                if k > self.key and t in ("position", "laps", "intervals", "race_control", "location"):
                    self.cambio = True      # empezó otra sesión antes de lo que decía el calendario
                return
            if t in ("laps", "race_control", "position", "intervals"):
                self.act_at = time.time()
            self.aplicar(t, r)

    def _mqtt_loop(self):
        espera = 2
        while self.activo:
            c = self.leer_cfg()
            tk = self.token()
            if not c or not tk:
                time.sleep(10)
                continue
            url = MQTT_URLS[self.mq_i % len(MQTT_URLS)]
            try:
                def estado(ok):
                    self.mq_ok = ok
                    if ok:
                        self.mq_err = ""
                mqtt_escuchar(url, c["user"], tk, TOPICS, self._mqtt_msg, lambda: self.activo and time.time() < self.tok_exp - 240, estado)
                espera = 2
            except PermissionError as e:
                self.mq_err = str(e)
                self.tok = ""
                espera = 15
            except Exception as e:
                self.mq_err = "%s: %s" % (url.split(":")[0], e)
                self.mq_i += 1     # probar la otra vía (TLS directo ↔ WebSocket)
                espera = min(20, espera * 2)
            self.mq_ok = False
            if self.activo:
                time.sleep(espera if self.mq_err else 0.5)

    # ---------- calendario ----------
    def _calendario(self, now):
        cada = 300 if self.activo or self._cerca(now) else 900
        if self.year and now - self.year_at < cada:
            return
        y = time.gmtime(now).tm_year
        a = self.q("sessions?year=%d" % y)
        if a:
            a = [s for s in a if not s.get("is_cancelled")]
            a.sort(key=lambda s: s.get("date_start") or "")
            with self.lock:
                self.year, self.year_at = a, now
                if self.sess:  # horarios movidos: se actualiza la sesión que tenemos
                    for s in a:
                        if s.get("session_key") == self.key and (s.get("date_start") != self.sess.get("date_start") or s.get("date_end") != self.sess.get("date_end")):
                            self.sess.update(s)
                            self.sucio = True
        elif not self.year:
            self.year_at = now - cada + 30

    def _cerca(self, now):
        return any(abs(ms(s.get("date_start")) / 1000 - now) < 3 * 3600 for s in self.year)

    def _elegir(self, now):
        """La sesión a mostrar: la última que ya empezó o empieza en menos de ANTES."""
        pick = None
        for s in self.year:
            if ms(s.get("date_start")) / 1000 - ANTES <= now:
                pick = s
        return pick

    def siguiente(self, now):
        for s in self.year:
            if ms(s.get("date_start")) / 1000 > now and s.get("session_key") != self.key:
                return s
        return None

    def _ventana(self, now):
        if not self.sess:
            return False
        ini, fin = ms(self.sess.get("date_start")) / 1000, ms(self.sess.get("date_end")) / 1000
        # si se alarga (banderas rojas, retrasos) sigue prendido mientras haya actividad en pista
        return ini - ANTES <= now <= max(fin, self.act_at) + DESPUES

    def terminada(self, now=None):
        now = now or time.time()
        if not self.sess:
            return False
        if now > ms(self.sess.get("date_end")) / 1000 and now - self.act_at > 600:
            return True
        tipo = (self.sess.get("session_type") or "").lower()
        chq = [m for m in self.rc if (m.get("flag") or "").upper() == "CHEQUERED"]
        if tipo == "qualifying":
            return any(m.get("qualifying_phase") == 3 for m in chq)
        return bool(chq)

    # ---------- cargar una sesión ----------
    def _K(self):
        return "session_key=%d" % self.key

    def _cambiar(self, s):
        with self.lock:
            self._vaciar()
            self.sess, self.key = dict(s), s["session_key"]
            self.gen, self.seq = int(time.time()), 0
            self.act_at, self.res_try, self.track_try, self.rec_at, self.rap_at = 0.0, 0.0, 0.0, 0.0, 0.0
            self.sucio = True
        self.log("sesión: %s %s (%s)" % (s.get("location"), s.get("session_name"), self.key))
        self._llenar()

    def _llenar(self):
        """Baja TODA la sesión por REST (al prender o al cambiar de sesión)."""
        self._pilotos()
        race = (self.sess.get("session_type") or "").lower() == "race"
        tabla = [("position", "position?" + self._K()), ("laps", "laps?" + self._K()), ("race_control", "race_control?" + self._K()),
                 ("stints", "stints?" + self._K()), ("pit", "pit?" + self._K()), ("weather", "weather?" + self._K()), ("team_radio", "team_radio?" + self._K())]
        if race:
            tabla.append(("intervals", "intervals?" + self._K()))
        for topic, path in tabla:
            a = self.q(path)
            with self.lock:
                for r in a or []:
                    self.aplicar(topic, r, emitir=False)
                self._cursor(topic, a or [])
        if race:
            a = self.q("starting_grid?" + self._K())
            with self.lock:
                for g in a or []:
                    if g.get("driver_number"):
                        self.grid[g["driver_number"]] = g.get("position")
        with self.lock:
            self.wx = self.wx[-300:]
            self.gen, self.seq = int(time.time()), 0   # las páginas vuelven a bajar la foto completa
            self.pend, self.pend_loc = [], []
            self.rec_at = time.time()
            self.sucio = True
        self._pista()
        self._resultado()
        self._campeonato()

    def _cursor(self, topic, a):
        campo = "date_start" if topic == "laps" else "date"
        m = max([r.get(campo) or "" for r in a] + [self.cur.get(topic) or ""])
        if m:
            self.cur[topic] = m

    def _pilotos(self):
        a, de = self.q("drivers?" + self._K()), "session"
        if not a and self.sess.get("meeting_key"):
            a, de = self.q("drivers?meeting_key=%s" % self.sess["meeting_key"]) or [], "meeting"
            mk = max([d.get("session_key") or 0 for d in a] + [0])
            a = [d for d in a if (d.get("session_key") or 0) == mk]
        if a:
            with self.lock:
                nuevo = {d["driver_number"]: d for d in a if d.get("driver_number")}
                if nuevo != self.drivers:
                    self.drivers, self.drivers_de = nuevo, de
                    self.sucio = True
                    self.pend.append(["drivers_all", {"rows": a}])
                self.drivers_de = de

    def _repaso(self, rapido=False):
        """Repaso por REST: lo que el tiempo real pudo perder (o todo, si el tiempo real está caído)."""
        race = (self.sess.get("session_type") or "").lower() == "race"

        def desde(topic, campo="date", atras=0):
            c = self.cur.get(topic)
            if c and atras:
                c = iso(ms(c) - atras)
            return ("&%s>%s" % (campo, urllib.parse.quote(c))) if c else ""
        tabla = [("position", "position?" + self._K() + desde("position")), ("laps", "laps?" + self._K() + desde("laps", "date_start", 240000)),
                 ("race_control", "race_control?" + self._K() + desde("race_control"))]
        if race:
            tabla.append(("intervals", "intervals?" + self._K() + desde("intervals")))
        if not rapido:
            tabla += [("stints", "stints?" + self._K()), ("pit", "pit?" + self._K()), ("weather", "weather?" + self._K() + desde("weather")),
                      ("team_radio", "team_radio?" + self._K() + desde("team_radio"))]
        for topic, path in tabla:
            a = self.q(path)
            if a:
                with self.lock:
                    antes = len(self.pend)
                    for r in a:
                        self.aplicar(topic, r)
                    if len(self.pend) > antes and topic in ("laps", "race_control", "position") and self.activo:
                        self.act_at = max(self.act_at, time.time()) if self._en_horario() or rapido else self.act_at
                    self._cursor(topic, a)
        if not rapido:
            if self.drivers_de != "session" or not self.drivers:
                self._pilotos()
            if race and not self.grid:
                a = self.q("starting_grid?" + self._K())
                with self.lock:
                    for g in a or []:
                        if g.get("driver_number"):
                            self.grid[g["driver_number"]] = g.get("position")
                            self.sucio = True
            if not self.track:
                self._pista()
            self._resultado()

    def _en_horario(self):
        now = time.time()
        return ms(self.sess.get("date_start")) / 1000 - 600 <= now

    def _resultado(self):
        if self.result or not self.sess or time.time() - self.res_try < 90 or not self.terminada():
            return
        self.res_try = time.time()
        a = self.q("session_result?" + self._K())
        if a and len(a) >= 10:
            with self.lock:
                self.result = a
                self.pend.append(["session_result", {"rows": a}])
                self.sucio = True
            self.champ_at = 0

    def _campeonato(self):
        if time.time() - self.champ_at < 1800 or not self.year:
            return
        self.champ_at = time.time()
        now = time.time()
        hechas = [s for s in self.year if (s.get("session_type") or "").lower() == "race" and ms(s.get("date_end")) / 1000 + 1800 < now]
        for s in reversed(hechas[-3:]):
            dr = self.q("championship_drivers?session_key=%s" % s["session_key"])
            if dr:
                tm = self.q("championship_teams?session_key=%s" % s["session_key"]) or []
                with self.lock:
                    champ = {"after": s, "dr": dr, "tm": tm}
                    if getattr(self, "champ", None) != champ:
                        self.champ = champ
                        self.sucio = True
                return

    def _pista(self):
        """Trazado: GPS de una vuelta limpia (de esta sesión o de una anterior en el mismo circuito)."""
        ck = self.sess.get("circuit_key")
        if self.track or time.time() - self.track_try < 120:
            return
        self.track_try = time.time()
        f = os.path.join(self.data, "pista_%s.json" % ck)
        try:
            with open(f) as fh:
                pts = json.load(fh)
            if len(pts) > 120:
                self.track = pts
                self.sucio = True
                return
        except Exception:
            pass
        cands = []
        for n, m in self.laps.items():
            for l in m.values():
                if (l.get("lap_duration") or 0) > 0 and not l.get("is_pit_out_lap") and l.get("date_start"):
                    cands.append((self.key, l))
        cands.sort(key=lambda x: x[1]["lap_duration"])
        cands = cands[:2]
        if ck:
            ss = [x for x in (self.q("sessions?circuit_key=%s" % ck) or []) if ms(x.get("date_end")) / 1000 < time.time() - 600 and x.get("session_key") != self.key]
            ss.sort(key=lambda x: x.get("date_start") or "", reverse=True)
            for x in ss[:2]:
                laps = [l for l in (self.q("laps?session_key=%s&lap_number=%d" % (x["session_key"], 12 if x.get("session_type") == "Race" else 6)) or [])
                        if (l.get("lap_duration") or 0) > 0 and l.get("date_start") and not l.get("is_pit_out_lap")]
                laps.sort(key=lambda l: l["lap_duration"])
                if laps:
                    cands.append((x["session_key"], laps[0]))
                    break
        for key, l in cands:
            t0 = ms(l["date_start"])
            a = self.q("location?session_key=%s&driver_number=%s&date>=%s&date<=%s" % (key, l["driver_number"], urllib.parse.quote(iso(t0)), urllib.parse.quote(iso(t0 + int(l["lap_duration"] * 1000)))))
            pts = [[p["x"], p["y"]] for p in (a or []) if p.get("x") or p.get("y")]
            if len(pts) > 120:
                with self.lock:
                    self.track = pts
                    self.sucio = True
                try:
                    with open(f, "w") as fh:
                        json.dump(pts, fh)
                except Exception:
                    pass
                return

    # ---------- publicar para las páginas ----------
    def _escribir(self, rel, obj):
        p = os.path.join(self.pub, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, p)

    def estado(self):
        now = time.time()
        if not self.leer_cfg():
            return "config"
        if not self.sess:
            return "espera"
        if self.activo:
            if now < ms(self.sess.get("date_start")) / 1000 and not self.laps:
                return "previa"
            return "final" if self.terminada(now) else "vivo"
        return "final"

    def _head(self, now):
        """Lo que la página pública necesita para decir si la sesión está ON, OFF o fallando."""
        ult = max(getattr(self, "rest_at", 0) or 0, self.mq_at or 0)
        err = ""
        if "rechazó la cuenta" in (self.err or ""):
            err = "cuenta"
        elif self.activo and self.estado() == "vivo" and ((ult and now - ult > 120) or (not ult and self.err)):
            err = "openf1"           # sesión en curso, motor prendido, pero OpenF1 no le da datos (ni tiempo real ni repaso)
        h = {"gen": self.gen, "seq": self.seq, "t": int(now * 1000), "key": self.key, "estado": self.estado(), "mqtt": self.mq_ok,
             "on": bool(self.activo), "dato": int(now - ult) if ult else None, "err": err}
        if not self.activo:
            p = self.siguiente(now) if self.year else None
            if p:
                h["prox"] = {"nombre": p.get("session_name"), "lugar": p.get("location") or p.get("circuit_short_name"),
                             "pais": p.get("country_name"), "ini": ms(p.get("date_start")), "on": ms(p.get("date_start")) - ANTES * 1000}
        return h

    def _publicar(self, now):
        with self.lock:
            if self.pend or self.pend_loc:
                self.seq += 1
                self._escribir("live/%d/%d.json" % (self.gen, self.seq), {"s": self.seq, "t": int(now * 1000), "e": self.pend, "L": self.pend_loc})
                self.pend, self.pend_loc = [], []
                viejo = os.path.join(self.pub, "live", str(self.gen), "%d.json" % (self.seq - 900))
                if self.seq > 900 and os.path.exists(viejo):
                    os.remove(viejo)
                self.head_at = 0
            if now - self.head_at >= (2 if self.activo else 10):
                self._escribir("head.json", self._head(now))
                self.head_at = now
            if (self.sucio and now - self.snap_at >= 5) or now - self.snap_at >= 60:
                self._escribir("snap.json", self.foto(now))
                self.snap_at, self.sucio = now, False
                self._limpiar()

    def foto(self, now):
        L = []
        for n, a in self.loc.items():
            for t, x, y in list(a)[-110:]:
                L.append([n, t, x, y])
        return dict(self._head(now), session=self.sess, next=self.siguiente(now), year=self.year, drivers=list(self.drivers.values()),
                    grid=self.grid, result=self.result, champ=getattr(self, "champ", None), track=self.track,
                    position=list(self.pos.values()), intervals=list(self.intv.values()),
                    laps=[l for m in self.laps.values() for l in m.values()], race_control=self.rc,
                    stints=[s for m in self.stints.values() for s in m.values()], pit=[p for a in self.pits.values() for p in a],
                    weather=self.wx[-300:], team_radio=self.radios[-80:], L=L, aviso=self.err if not self.leer_cfg() else "")

    def _limpiar(self):
        base = os.path.join(self.pub, "live")
        try:
            for d in os.listdir(base):
                if d != str(self.gen):
                    p = os.path.join(base, d)
                    if time.time() - os.path.getmtime(p) > 120:
                        for f in os.listdir(p):
                            os.remove(os.path.join(p, f))
                        os.rmdir(p)
        except Exception:
            pass

    # ---------- bucle principal ----------
    def paso(self):
        now = time.time()
        if not self.leer_cfg():
            self.activo = False
            self._publicar(now)
            return
        self._calendario(now)
        s = self._elegir(now)
        if s and (s["session_key"] != self.key):
            self.activo = False
            self._cambiar(s)
        elif self.cambio:
            self.cambio = False
            self.year_at = 0
        activo = self._ventana(now)
        if activo != self.activo:
            self.activo = activo
            self.sucio = True
            self.log("motor %s" % ("PRENDIDO" if activo else "apagado"))
            if activo and time.time() - self.rec_at > 30:
                self._repaso()
                self.rec_at = time.time()
        if self.activo:
            if not self.mq_thread or not self.mq_thread.is_alive():
                self.mq_thread = threading.Thread(target=self._mqtt_loop, daemon=True)
                self.mq_thread.start()
            caido = not self.mq_ok or now - self.mq_at > 25
            if now - self.rec_at >= 60:
                self._repaso()
                self.rec_at = self.rap_at = time.time()
            elif caido and self._en_horario() and now - self.rap_at >= 6:
                self._repaso(rapido=True)   # sin tiempo real: se consulta seguido (dentro del límite)
                self.rap_at = time.time()
        else:
            if self.sess and not self.result and self.terminada(now) and now - ms(self.sess.get("date_end")) / 1000 < 6 * 3600:
                self._resultado()
            self._campeonato()
        self._publicar(time.time())

    def correr(self):
        os.makedirs(self.pub, exist_ok=True)
        while True:
            try:
                self.paso()
            except Exception as e:
                self.err = "error interno: %s" % e
                self.log("error: %r" % e)
                time.sleep(2)
            time.sleep(1.0)

    def resumen(self):
        now = time.time()
        s = self.sess or {}
        return {"estado": self.estado(), "sesion": ("%s · %s" % (s.get("location"), s.get("session_name"))) if s else None, "session_key": self.key,
                "motor_prendido": self.activo, "tiempo_real": self.mq_ok, "mensajes_tiempo_real": self.mq_msgs,
                "ultimo_dato_hace_s": int(now - self.mq_at) if self.mq_at else None, "consultas_ultimo_minuto": len([x for x in self.reqs if now - x < 60]),
                "pilotos": len(self.drivers), "vueltas": sum(len(m) for m in self.laps.values()), "race_control": len(self.rc),
                "resultado_oficial": bool(self.result), "pista": bool(self.track), "gen": self.gen, "seq": self.seq,
                "aviso": self.mq_err or self.err or None}
