#!/bin/bash
# Instala tu servidor Pure Driving MX en Oracle Cloud gratis (Ubuntu, AMD o Ampere).
# - Página + puente a OpenF1 en https://IP-CON-GUIONES.nip.io (certificado gratis automático).
# - Se queda prendido 24/7 y se reinicia solo. Celular en / y transmisión (OBS) en /tv, todo desde tu servidor.
#
# Al crear la instancia: pégalo en "Initialization script" (cloud-init), o en cualquier momento:
#   curl -fsSL https://raw.githubusercontent.com/julioga120-tech/pdmx-live./main/puente/oracle.sh | sudo bash
# Volver a correrlo es seguro: actualiza y conserva tu configuración.
set -u
RAW="https://raw.githubusercontent.com/julioga120-tech/pdmx-live./main/puente"
log() { echo "[pdmx] $*"; }
if [ "$(id -u)" != 0 ]; then echo "Córrelo con sudo."; exit 1; fi
export DEBIAN_FRONTEND=noninteractive HOME="${HOME:-/root}"
APT="apt-get -y -o DPkg::Lock::Timeout=600"
# en el primer arranque Ubuntu se actualiza solo y tiene apt ocupado: esperar a que termine
esperar_apt() { for i in $(seq 1 120); do fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1 || return 0; sleep 5; done; }

# 1) IP pública → direcciones con HTTPS gratis (nip.io y sslip.io apuntan solas a tu IP)
IP=""
for u in https://api.ipify.org https://ifconfig.me/ip https://icanhazip.com https://checkip.amazonaws.com; do
  IP=$(curl -fsS --max-time 10 "$u" 2>/dev/null | tr -d '[:space:]')
  echo "$IP" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' && break
  IP=""
done
[ -n "$IP" ] || { log "No pude saber la IP pública (¿la instancia tiene IP pública?)"; exit 1; }
D=$(echo "$IP" | tr . -)
H1="$D.nip.io"; H2="$D.sslip.io"
log "IP pública $IP → https://$H1"

# 2) Firewall de la máquina (Oracle trae todo cerrado menos SSH)
if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-service=http >/dev/null && firewall-cmd --permanent --add-service=https >/dev/null && firewall-cmd --reload >/dev/null
elif command -v iptables >/dev/null 2>&1; then
  for P in 443 80; do iptables -C INPUT -p tcp --dport "$P" -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport "$P" -j ACCEPT; done
  if command -v netfilter-persistent >/dev/null 2>&1; then netfilter-persistent save >/dev/null 2>&1 || true
  elif [ -d /etc/iptables ]; then iptables-save > /etc/iptables/rules.v4; fi
fi
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; fi

# 3) Python 3 y Caddy (HTTPS automático)
if command -v apt-get >/dev/null 2>&1; then
  esperar_apt; $APT update >/dev/null 2>&1
  esperar_apt; $APT install curl gnupg python3 >/dev/null 2>&1
  if ! command -v caddy >/dev/null 2>&1; then
    log "Instalando Caddy…"
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt -o /etc/apt/sources.list.d/caddy-stable.list
    esperar_apt; $APT update >/dev/null 2>&1; esperar_apt; $APT install caddy >/dev/null 2>&1 || true
  fi
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 curl >/dev/null 2>&1 || true
fi
if ! command -v caddy >/dev/null 2>&1; then
  log "Instalando Caddy (binario oficial)…"
  case "$(uname -m)" in aarch64|arm64) A=arm64;; *) A=amd64;; esac
  curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=$A" -o /usr/bin/caddy && chmod +x /usr/bin/caddy
  id caddy >/dev/null 2>&1 || useradd --system --home-dir /var/lib/caddy --create-home --shell /usr/sbin/nologin caddy
  cat > /etc/systemd/system/caddy.service <<'EOF'
[Unit]
Description=Caddy
After=network-online.target
Wants=network-online.target
[Service]
User=caddy
Group=caddy
ExecStart=/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile --force
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF
fi
command -v caddy >/dev/null 2>&1 || { log "No se pudo instalar Caddy."; exit 1; }
command -v python3 >/dev/null 2>&1 || { log "No hay python3."; exit 1; }

# 4) Tu servidor (página + puente). Tu cuenta se guarda en /var/lib/pdmx (solo lo lee el servidor).
id pdmx >/dev/null 2>&1 || useradd --system --home-dir /var/lib/pdmx --shell /usr/sbin/nologin pdmx
mkdir -p /opt/pdmx /var/lib/pdmx /etc/caddy
chown pdmx:pdmx /var/lib/pdmx && chmod 700 /var/lib/pdmx
# si lo corres desde tu zip, usa los archivos del zip
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)"
if [ -d "$SRC_DIR/web" ]; then mkdir -p /opt/pdmx/web && cp -r "$SRC_DIR/web/." /opt/pdmx/web/ && chmod -R a+rX /opt/pdmx/web; fi
if [ -f "$SRC_DIR/servidor.py" ] && python3 -m py_compile "$SRC_DIR/servidor.py"; then
  cp "$SRC_DIR/servidor.py" /opt/pdmx/servidor.py
elif curl -fsSL "$RAW/servidor.py?t=$(date +%s)" -o /opt/pdmx/servidor.py.new && python3 -m py_compile /opt/pdmx/servidor.py.new; then
  mv /opt/pdmx/servidor.py.new /opt/pdmx/servidor.py
else
  rm -f /opt/pdmx/servidor.py.new
  [ -f /opt/pdmx/servidor.py ] || { log "No pude bajar servidor.py de GitHub."; exit 1; }
fi
cat > /etc/systemd/system/pdmx.service <<'EOF'
[Unit]
Description=Pure Driving MX - pagina y puente a OpenF1
After=network-online.target
Wants=network-online.target
[Service]
User=pdmx
Environment=PDMX_DATA=/var/lib/pdmx
ExecStart=/usr/bin/python3 /opt/pdmx/servidor.py
Restart=always
RestartSec=3
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
EOF

# 5) Todo vive en tu servidor: sin actualizaciones automáticas (para actualizar, vuelve a correr el instalador del zip nuevo)
systemctl disable --now pdmx-actualizar.timer >/dev/null 2>&1 || true
rm -f /etc/systemd/system/pdmx-actualizar.timer /etc/systemd/system/pdmx-actualizar.service /opt/pdmx/actualizar.sh
if [ ! -f /opt/pdmx/web/index.html ]; then  # instalado sin zip: baja la página una sola vez
  mkdir -p /opt/pdmx/web/tv
  for f in index.html tv.html mqtt.min.js manifest.json logo.png helmet.png icon-180.png icon-192.png icon-512.png tv/app.js tv/style.css; do
    curl -fsSL "https://raw.githubusercontent.com/julioga120-tech/pdmx-live./main/$f" -o "/opt/pdmx/web/$f" || log "No pude bajar $f"
  done
  chmod -R a+rX /opt/pdmx/web
fi

# 6) Caddy: tu sitio en su propio archivo (así puedes agregar otros sitios para tus agentes)
cat > /etc/caddy/pdmx.caddy <<EOF
$H1, $H2 {
	encode gzip
	reverse_proxy 127.0.0.1:8787
}
http://$IP {
	redir https://$H1{uri}
}
EOF
if [ ! -s /etc/caddy/Caddyfile ] || grep -q "/usr/share/caddy" /etc/caddy/Caddyfile; then
  printf 'import /etc/caddy/*.caddy\n' > /etc/caddy/Caddyfile
elif ! grep -Eq 'import /etc/caddy/(\*|pdmx)\.caddy' /etc/caddy/Caddyfile; then
  printf '\nimport /etc/caddy/pdmx.caddy\n' >> /etc/caddy/Caddyfile
fi

systemctl daemon-reload
systemctl enable pdmx caddy >/dev/null 2>&1
systemctl restart pdmx
systemctl restart caddy
echo "https://$H1" > /var/lib/pdmx/link.txt

# 7) Avisar (el certificado llega solo en 1 a 15 minutos, cuando los puertos 80 y 443 estén abiertos en Oracle)
MSG="Configúralo en https://$H1 (si no abre: https://$H2). Celular: https://$H1  ·  Transmisión OBS: https://$H1/tv"
printf '\n==== SERVIDOR PURE DRIVING MX ====\n%s\n==================================\n' "$MSG" | tee /etc/motd
