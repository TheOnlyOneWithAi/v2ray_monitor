import asyncio, json, os, tempfile, time
from pathlib import Path
from .config import get_settings

class XrayProbe:
    def __init__(self): self.s=get_settings()
    def _config(self,node):
        c=node.config.copy(); stream=c.pop('streamSettings',{})
        if node.protocol=='vless': user={'id':c['uuid'],'encryption':'none'}
        else: user={'id':c['uuid'],'alterId':c.get('alterId',0),'security':c.get('security','auto')}
        outbound={'protocol':node.protocol,'settings':{'vnext':[{'address':c['address'],'port':c['port'],'users':[user]}]},'streamSettings':stream,'tag':'probe'}
        return {'log':{'loglevel':'error'},'inbounds':[{'listen':'127.0.0.1','port':0,'protocol':'socks','settings':{'auth':'noauth','udp':False}}],'outbounds':[outbound]}
    async def probe(self,node):
        start=time.perf_counter()
        with tempfile.TemporaryDirectory(prefix='v2m-') as td:
            cfg=Path(td)/'config.json'; cfg.write_text(json.dumps(self._config(node),separators=(',',':')),encoding='utf-8')
            # Xray can choose an ephemeral inbound only when port is omitted on supported builds; use a free local port instead.
            import socket
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
            data=json.loads(cfg.read_text()); data['inbounds'][0]['port']=port; cfg.write_text(json.dumps(data,separators=(',',':')))
            proc=await asyncio.create_subprocess_exec(self.s.xray_binary,'run','-c',str(cfg),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            try:
                for _ in range(40):
                    if proc.returncode is not None: raise RuntimeError('xray exited')
                    try:
                        r,w=await asyncio.open_connection('127.0.0.1',port); w.close(); await w.wait_closed(); break
                    except OSError: await asyncio.sleep(.05)
                else: raise RuntimeError('xray startup timeout')
                # A local SOCKS handshake proves Xray is alive; then measure a proxied HTTPS request.
                import httpx
                proxy=f'socks5://127.0.0.1:{port}'
                async with httpx.AsyncClient(proxy=proxy,timeout=self.s.probe_timeout,verify=False) as client:
                    r=await client.get('https://www.gstatic.com/generate_204')
                    if r.status_code not in (200,204): raise RuntimeError(f'probe HTTP {r.status_code}')
                return round((time.perf_counter()-start)*1000,1)
            finally:
                proc.terminate()
                try: await asyncio.wait_for(proc.wait(),2)
                except asyncio.TimeoutError: proc.kill(); await proc.wait()
