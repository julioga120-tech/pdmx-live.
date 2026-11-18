#!/bin/bash
# Pure Driving MX · ejecutor del panel (corre como root, lo dispara pdmx-panel.path).
# El servidor web (usuario pdmx) solo deja un pedido en /var/lib/pdmx/actualizar/pedido.json;
# aquí se valida y se ejecuta: actualizar desde un zip, volver a la versión anterior o reiniciar.
set -u
A=/var/lib/pdmx/actualizar
PEDIDO=$A/pedido.json
RES=$A/resultado.json
LOG=$A/ultima.log
RESP=/var/lib/pdmx-respaldo
[ -s "$PEDIDO" ] || exit 0
mkdir -p "$RESP"
chmod 700 "$RESP"

resultado() {  # estado mensaje [version]
  python3 - "$RES" "$1" "$2" "${3:-}" <<'PY'
import json, os, sys, time
ruta, estado, msg, ver = sys.argv[1:5]
tmp = ruta + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"estado": estado, "mensaje": msg, "version": ver, "at": int(time.time() * 1000)}, f, ensure_ascii=False)
os.replace(tmp, ruta)
PY
  chown pdmx:pdmx "$RES" 2>/dev/null; chmod 640 "$RES"
}

version_de() {  # archivo servidor.py
  sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$1" 2>/dev/null | head -1
}

sano() {  # el servidor responde en menos de 40 s
  for i in $(seq 1 20); do
    curl -fsS --max-time 3 http://127.0.0.1:8787/estado >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

respaldar() {
  local v; v="$(version_de /opt/pdmx/servidor.py)"
  tar czf "$RESP/anterior.tgz.tmp" -C /opt pdmx -C /etc/caddy/sitios pdmx.caddy 2>/dev/null \
    || tar czf "$RESP/anterior.tgz.tmp" -C /opt pdmx
  mv "$RESP/anterior.tgz.tmp" "$RESP/anterior.tgz"
  python3 -c 'import json,sys,time;json.dump({"version":sys.argv[1],"at":int(time.time()*1000)},open(sys.argv[2],"w"))' "$v" "$A/respaldo.json"
  chown pdmx:pdmx "$A/respaldo.json"
}

restaurar() {
  [ -s "$RESP/anterior.tgz" ] || return 1
  local T; T="$(mktemp -d)"
  tar xzf "$RESP/anterior.tgz" -C "$T" || { rm -rf "$T"; return 1; }
  [ -f "$T/pdmx/servidor.py" ] || { rm -rf "$T"; return 1; }
  rm -rf /opt/pdmx.nuevo && mv "$T/pdmx" /opt/pdmx.nuevo
  rm -rf /opt/pdmx.quitar && mv /opt/pdmx /opt/pdmx.quitar && mv /opt/pdmx.nuevo /opt/pdmx && rm -rf /opt/pdmx.quitar
  if [ -f "$T/pdmx.caddy" ] && [ -d /etc/caddy/sitios ]; then
    cp /etc/caddy/sitios/pdmx.caddy /etc/caddy/sitios/pdmx.caddy.bak 2>/dev/null
    cp "$T/pdmx.caddy" /etc/caddy/sitios/pdmx.caddy
    if caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
      systemctl reload caddy
    else
      mv /etc/caddy/sitios/pdmx.caddy.bak /etc/caddy/sitios/pdmx.caddy 2>/dev/null
    fi
  fi
  rm -rf "$T"
  chmod -R a+rX /opt/pdmx/web 2>/dev/null
  systemctl restart pdmx
}

ACCION="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("accion",""))' "$PEDIDO" 2>/dev/null)"
rm -f "$PEDIDO"
: > "$LOG"; chown pdmx:pdmx "$LOG"; chmod 640 "$LOG"

case "$ACCION" in
  actualizar)
    Z="$A/nuevo.zip"
    [ -s "$Z" ] || { resultado error "No se encontró el archivo subido."; exit 0; }
    resultado trabajando "Instalando la actualización…"
    T="$(mktemp -d)"
    # extracción segura: sin rutas absolutas ni "..", sin enlaces, tamaño limitado
    if ! python3 - "$Z" "$T" >>"$LOG" 2>&1 <<'PY'
import os, stat, sys, zipfile
z, dest = sys.argv[1], os.path.realpath(sys.argv[2])
total = 0
with zipfile.ZipFile(z) as zf:
    for i in zf.infolist():
        n = i.filename
        if n.startswith("/") or ".." in n.split("/") or "\\" in n:
            sys.exit("ruta no permitida: " + n)
        if stat.S_ISLNK(i.external_attr >> 16):
            sys.exit("enlace no permitido: " + n)
        total += i.file_size
        if total > 150 * 1024 * 1024:
            sys.exit("archivo demasiado grande")
        out = os.path.realpath(os.path.join(dest, n))
        if not out.startswith(dest + os.sep) and out != dest:
            sys.exit("ruta no permitida: " + n)
        if n.endswith("/"):
            os.makedirs(out, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with zf.open(i) as src, open(out, "wb") as dst:
            dst.write(src.read())
PY
    then
      rm -rf "$T"; resultado error "El archivo no es una actualización válida."; exit 0
    fi
    INST="$(find "$T" -maxdepth 3 -name instalar.sh -type f | head -1)"
    DIR="$(dirname "$INST" 2>/dev/null)"
    if [ -z "$INST" ] || [ ! -f "$DIR/servidor.py" ] || [ ! -f "$DIR/motor.py" ] || [ ! -f "$DIR/web/index.html" ]; then
      rm -rf "$T"; resultado error "Al zip le faltan archivos (instalar.sh, servidor.py, motor.py o web/)."; exit 0
    fi
    for F in servidor.py motor.py; do
      python3 -m py_compile "$DIR/$F" >>"$LOG" 2>&1 || { rm -rf "$T"; resultado error "El código de $F tiene errores; no se instaló nada."; exit 0; }
    done
    NUEVA="$(version_de "$DIR/servidor.py")"
    respaldar
    if PDMX_RAPIDO=1 bash "$INST" >>"$LOG" 2>&1 && sano; then
      resultado listo "Actualizado correctamente." "$NUEVA"
    else
      echo "--- la actualización falló, regresando a la versión anterior ---" >>"$LOG"
      restaurar && sano
      resultado error "La actualización falló y se regresó a la versión anterior. El servidor sigue funcionando." "$(version_de /opt/pdmx/servidor.py)"
    fi
    rm -rf "$T" "$Z"
    ;;
  anterior)
    resultado trabajando "Regresando a la versión anterior…"
    if [ ! -s "$RESP/anterior.tgz" ]; then resultado error "No hay una versión anterior guardada."; exit 0; fi
    ACTUAL_TGZ="$RESP/actual.tgz"
    V_ANTES="$(version_de /opt/pdmx/servidor.py)"
    tar czf "$ACTUAL_TGZ" -C /opt pdmx -C /etc/caddy/sitios pdmx.caddy 2>/dev/null || tar czf "$ACTUAL_TGZ" -C /opt pdmx
    if restaurar && sano; then
      mv "$ACTUAL_TGZ" "$RESP/anterior.tgz"   # así se puede volver a la que estaba
      python3 -c 'import json,sys,time;json.dump({"version":sys.argv[1],"at":int(time.time()*1000)},open(sys.argv[2],"w"))' "$V_ANTES" "$A/respaldo.json"
      chown pdmx:pdmx "$A/respaldo.json"
      resultado listo "Se regresó a la versión anterior." "$(version_de /opt/pdmx/servidor.py)"
    else
      resultado error "No se pudo regresar a la versión anterior." "$(version_de /opt/pdmx/servidor.py)"
    fi
    ;;
  reiniciar)
    resultado trabajando "Reiniciando el servidor…"
    systemctl restart pdmx
    if sano; then resultado listo "Servidor reiniciado." "$(version_de /opt/pdmx/servidor.py)"
    else resultado error "El servidor no respondió después de reiniciar." "$(version_de /opt/pdmx/servidor.py)"; fi
    ;;
  *)
    resultado error "Pedido no reconocido."
    ;;
esac
exit 0
