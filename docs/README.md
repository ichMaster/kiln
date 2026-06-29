# Документація kiln

Технічна документація двіжка. Для «як запустити й користуватися» дивись
[README у корені](../README.md).

- **[architecture.md](architecture.md)** — устрій у цілому: ідея, два «мозки»,
  карта модуля, потік даних на тіку, персистентність, рацій дизайну.
- **[how-it-works.md](how-it-works.md)** — механіка в деталях: цикл тіків,
  модель потреб (дрейф + закриття), класифікація й роутинг, self-тригери
  (гістерезис + кулдаун), канали вводу, історія, пам'ять і транскрипти,
  конфігурація та калібрування, слеш-команди.
- **[server.md](server.md)** — tick-server (v1.1): how to set up and connect to
  the server (install the `[server]` extra, run `uvicorn`, attach the TUI or a raw
  WebSocket client, the HTTP/WS API, `agent_id`-scoped data, troubleshooting).
