"""Public, non-secret monitoring API.

The public surface intentionally exposes only display-safe node metadata. Raw
subscription URLs and Xray credentials never leave the server.
"""
import asyncio
import html
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .db import Session, init_db
from .models import Node, Template

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
app = FastAPI(title="V2Ray Monitor", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

DEFAULT_TEMPLATE = (
    '<article class="node">'
    '<div><b>{{name}}</b><small>{{protocol}}</small></div>'
    '<div class="right"><span>{{status}}</span><strong>{{ping}} ms</strong></div>'
    '</article>'
)
_TEMPLATE_LOCK = asyncio.Lock()


@app.on_event("startup")
async def startup() -> None:
    await init_db()
    async with Session() as db:
        template = (await db.execute(select(Template).where(Template.name == "default"))).scalars().first()
        if template is None:
            db.add(Template(name="default", html=DEFAULT_TEMPLATE))
            await db.commit()


def _safe_node(node: Node) -> dict:
    return {
        "id": node.id,
        "name": node.name,
        "protocol": node.protocol,
        "status": node.status,
        "latency_ms": node.latency_ms,
        "last_checked": node.last_checked.isoformat() if node.last_checked else None,
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    path = FRONTEND_DIR / "index.html"
    return HTMLResponse(path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/nodes")
async def nodes() -> dict:
    async with Session() as db:
        rows = (await db.execute(select(Node).where(Node.enabled.is_(True)).order_by(Node.id))).scalars().all()
    return {"nodes": [_safe_node(node) for node in rows]}


@app.get("/api/view", response_class=HTMLResponse)
async def view() -> HTMLResponse:
    async with _TEMPLATE_LOCK:
        async with Session() as db:
            template = (await db.execute(select(Template).where(Template.name == "default"))).scalars().first()
            rows = (await db.execute(select(Node).where(Node.enabled.is_(True)).order_by(Node.id))).scalars().all()

    template_html = template.html if template else DEFAULT_TEMPLATE
    allowed = ("name", "status", "ping", "protocol", "last_check")
    rendered = []
    for node in rows:
        values = {
            "name": html.escape(node.name, quote=True),
            "status": html.escape(node.status, quote=True),
            "ping": "—" if node.latency_ms is None else str(node.latency_ms),
            "protocol": html.escape(node.protocol, quote=True),
            "last_check": node.last_checked.isoformat() if node.last_checked else "—",
        }
        item = template_html
        for key in allowed:
            item = item.replace("{{" + key + "}}", values[key])
        rendered.append(item)
    return HTMLResponse("\n".join(rendered), headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
