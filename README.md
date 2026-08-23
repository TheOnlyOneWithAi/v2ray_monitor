# v2ray_monitor

Telegram-administered VLESS/VMess subscription monitor using Xray-core.

## Security model

- Subscription URLs and parsed node credentials are encrypted at rest with Fernet.
- Public `/api/nodes` exposes only node id, display name, protocol, status and latency.
- Raw VLESS/VMess links, UUIDs, passwords, SNI, Reality keys and subscription URLs are never returned by the public API.
- Subscription fetching requires HTTPS and blocks private, loopback, link-local, reserved and multicast destinations; redirects are disabled.
- Probe configuration exists only in memory/temp files and is deleted after each Xray process exits.
- User-visible names are HTML escaped by the built-in UI.
- CI compiles the project and runs parser tests.

## What it does

1. Admin sends a subscription URL to the Telegram bot.
2. The server fetches and parses VLESS and VMess entries.
3. Node credentials are encrypted and stored.
4. Xray-core is started per probe and performs a real proxied HTTPS request.
5. Results are stored as latency/status.
6. Normal users see only names and health information.
7. Admin can replace the HTML template from Telegram.

## Important limitation

A normal browser cannot safely run arbitrary Xray VLESS/VMess probes itself. This implementation therefore measures from the monitoring server. If you need measurements from each user's own ISP/IP, a separate trusted local Xray agent or a client application is required; the web page must not receive the node credentials.

## Setup

Generate a key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Create `.env` from `.env.example`, set `ENCRYPTION_KEY`, `BOT_TOKEN`, and `ADMIN_IDS` (comma-separated Telegram numeric IDs), then:

```bash
docker compose up -d --build
```

Open port 8000 behind HTTPS/reverse proxy. The bot accepts:

- `/addsub Name | https://example.com/sub`
- `/list`
- `/sync`
- `/settemplate` followed by HTML

Template placeholders:

`{{name}}`, `{{status}}`, `{{ping}}`, `{{protocol}}`, `{{last_check}}`

There is deliberately no `{{config}}`, `{{url}}`, `{{uuid}}` or `{{subscription}}` placeholder.

## Xray

The Docker image pins Xray-core v26.3.27, an official Xray release. Update the `XRAY_VERSION` build argument deliberately after testing rather than silently tracking a moving latest tag.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
pytest -q
python -m app.main
```
