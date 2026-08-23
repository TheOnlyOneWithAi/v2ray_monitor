# v2ray_monitor

Telegram-administered VLESS/VMess subscription monitor powered by Xray-core.

## Security model

- Subscription URLs and parsed node credentials are encrypted at rest with Fernet.
- Public `/api/nodes` exposes only `id`, display name, protocol, status, latency and last-check time.
- Raw VLESS/VMess links, UUIDs, passwords, SNI, Reality keys and subscription URLs are never returned by the public API.
- There is no public probe endpoint: probing happens only in the background worker.
- Subscription fetching requires HTTPS, validates DNS destinations and blocks private, loopback, link-local, reserved and multicast addresses; redirects are disabled.
- Xray probe configs are temporary and are deleted with their temporary directory after each probe.
- Logs intentionally contain node/subscription IDs rather than raw credentials or subscription URLs.
- The public UI escapes node data before inserting it into the built-in page.
- Docker runs as an unprivileged user with dropped capabilities and `no-new-privileges`.

## Features

- Telegram admin bot for adding, syncing, deleting and enabling/disabling subscriptions.
- VLESS and VMess parsing, including common WS/gRPC/HTTPUpgrade/XHTTP/TLS/REALITY settings.
- Configurable node limits, probe timeout, probe interval and probe concurrency.
- Stable node records so a subscription refresh does not unnecessarily erase latency history.
- Xray-core real proxied HTTPS health checks.
- Responsive public monitor showing only names/protocol/status/latency.
- Admin-controlled HTML template, either pasted into Telegram or uploaded as UTF-8 `.html`.
- Safe template placeholders: `{{name}}`, `{{status}}`, `{{ping}}`, `{{protocol}}`, `{{last_check}}`.
- No sensitive template placeholders such as `{{config}}`, `{{url}}`, `{{uuid}}` or `{{subscription}}`.
- Health endpoint at `/health` and Docker healthcheck.
- CI runs compile checks, import checks, parser tests and a Docker build.

## Important client-network limitation

A normal browser cannot run arbitrary VLESS/VMess/Xray probes itself. Browsers do not provide the raw TCP/UDP capabilities needed for arbitrary Xray transports, and sending the node credentials to the browser would defeat the no-leak requirement. Therefore the built-in monitor measures from the monitoring server.

If measurements must originate from each user's own ISP/IP, the safe architecture is an optional trusted local Xray agent or native client. That agent can perform the probe locally while the web UI receives only `{name,status,latency}`. The current server never sends the node credentials to ordinary web users.

## Setup

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Create `.env` from `.env.example` and set:

- `BOT_TOKEN` — Telegram bot token.
- `ADMIN_IDS` — comma-separated numeric Telegram user IDs allowed to administer the bot.
- `ENCRYPTION_KEY` — the generated Fernet key.
- `WEBAPP_URL` — the public HTTPS URL used by the Telegram Web App button.

Then:

```bash
docker compose up -d --build
```

Put the service behind HTTPS/reverse proxy before exposing it publicly. Port 8000 is the application port.

## Bot commands

```text
/start
/help
/addsub Name | https://example.com/sub
/list
/sync [subscription_id]
/delsub ID
/toggle ID
/nodes [subscription_id]
/settemplate
/template
```

After `/settemplate`, send the HTML directly or upload a `.html`/`.htm` UTF-8 file. Maximum template size is 100 KB.

## Xray

The Docker image pins an Xray-core release through `ARG XRAY_VERSION=26.3.27`. Upgrade deliberately and let CI validate the image rather than silently tracking `latest`.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
pytest -q
python -m compileall -q app tests
python -m app.main
```
