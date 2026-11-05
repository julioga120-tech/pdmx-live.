# Pure Driving MX · Live Timing (celular)

Página: https://julioga120-tech.github.io/pdmx-live./

- Torre, mapa, vueltas, mundial, clima y Race Control con datos de OpenF1 (cuenta patrocinador).
- En vivo los datos instantáneos llegan por MQTT (WebSocket). El historial (al entrar tarde o al
  volver de bloquear el celular) se rellena por REST.
- Durante las sesiones OpenF1 no le contesta al navegador por REST. Para tener todo, crea el
  puente gratis con `puente/` (Cloudflare Workers, botón de un toque en Ajustes) y pega su dirección en Ajustes → PUENTE.
