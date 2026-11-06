# Pure Driving MX · Live Timing (celular y compu)

Página: https://julioga120-tech.github.io/pdmx-live./

- Torre, mapa, vueltas, mundial, clima y Race Control con datos de OpenF1 (cuenta patrocinador).
- En vivo los datos instantáneos llegan por MQTT (WebSocket). El historial (al entrar tarde o al
  volver de bloquear el celular) se rellena por REST.
- Durante las sesiones OpenF1 no le contesta al navegador por REST. Para tener todo, usa tu servidor:

## Tu servidor gratis (24/7, sin tu computadora)

**Oracle Cloud:** al crear la instancia Ubuntu, pega en *Initialization script (cloud-init)*:

```
#!/bin/bash
curl -fsSL --retry 5 --retry-delay 5 https://raw.githubusercontent.com/julioga120-tech/pdmx-live./main/puente/oracle.sh | bash
```

Abre los puertos 80 y 443 en la Security List y entra a `https://f1.TU-IP-CON-GUIONES.sslip.io` (respaldo `f1-TU-IP-CON-GUIONES.nip.io`):
ahí escribes tu cuenta de OpenF1 (se guarda solo en el servidor) e inventas tu CLAVE. Te da tu link
(`https://f1.…sslip.io/#k=CLAVE`) para abrir en cualquier celular o en la compu, sin contraseña.
El servidor (`puente/servidor.py`, solo Python 3) sirve la página, hace de puente a OpenF1 y se
actualiza solo desde este repo cada hora.

**Cloudflare Workers (alternativa):** `puente/worker.js` con los secretos `OPENF1_USER`,
`OPENF1_PASS` y `CLAVE` (botón de un toque en Ajustes). Luego en Ajustes pegas la dirección
`.workers.dev` y tu CLAVE, y compartes tu link.
