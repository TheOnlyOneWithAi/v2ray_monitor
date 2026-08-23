import asyncio, logging
import uvicorn
from .api import app
from .bot import run_bot
from .workers import sync_loop, probe_loop
from .config import get_settings
async def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    tasks=[asyncio.create_task(sync_loop()),asyncio.create_task(probe_loop())]
    if get_settings().bot_token: tasks.append(asyncio.create_task(run_bot()))
    config=uvicorn.Config(app,host='0.0.0.0',port=8000,log_level='info')
    server=uvicorn.Server(config)
    tasks.append(asyncio.create_task(server.serve()))
    await asyncio.gather(*tasks)
if __name__=='__main__': asyncio.run(main())
