import asyncio
import logging

import uvicorn

from .api import app
from .bot import run_bot
from .config import get_settings
from .db import init_db
from .workers import probe_loop, sync_loop


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()

    # Initialize the schema exactly once before starting concurrent workers.
    # Calling create_all() concurrently from sync_loop/probe_loop can race on
    # SQLite and produce: "table ... already exists".
    await init_db()

    tasks = [
        asyncio.create_task(sync_loop()),
        asyncio.create_task(probe_loop()),
    ]
    if settings.bot_token:
        tasks.append(asyncio.create_task(run_bot()))
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.web_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    tasks.append(asyncio.create_task(server.serve()))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
