# Pure Driving MX · Live Timing

Live timing público (no oficial) con datos de OpenF1. Todo corre en tu servidor:

- `puente/motor.py`: el único que habla con OpenF1. Se prende 1 h antes de cada sesión y se apaga 1 h
  después (si la sesión se alarga, sigue mientras haya actividad). Recibe en vivo por MQTT, repasa por
  REST y publica la sesión como archivos (`snap.json` + pedacitos por segundo).
- `puente/servidor.py`: configuración (`/configurar`) y estado del motor (`/estado`).
- `index.html`: página pública (celular y compu). `tv.html` + `tv/`: vista de transmisión para OBS (`/tv`).
  Las páginas solo leen los archivos del servidor: nadie necesita cuenta y no gastan consultas de OpenF1.

## Instalar (Ubuntu: Oracle, Azure…)

```
sudo bash instalar.sh                      # dirección gratis https://f1.IP-CON-GUIONES.sslip.io
sudo bash instalar.sh live.tudominio.com   # además tu dominio (registro A apuntando a la IP del servidor)
```

Al final sale el código de instalación; con él se configura la cuenta de OpenF1 en `/configurar`.
No está afiliado a Formula 1. F1 y marcas relacionadas son de Formula One Licensing B.V.
