#!/usr/bin/env python3
"""User-side probe agent. Run this on the user's own network to report real latency."""
import argparse,asyncio,json,os,socket,tempfile,time
from pathlib import Path
import httpx

def build_xray(c,protocol,port):
    p=protocol.lower(); stream=c.get('streamSettings',{})
    if p=='vless': user={'id':c['uuid'],'encryption':'none'}
    elif p=='vmess': user={'id':c['uuid'],'alterId':c.get('alterId',0),'security':c.get('security','auto')}
    else: raise RuntimeError('agent currently supports vless/vmess')
    return {'log':{'loglevel':'error'},'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'socks','settings':{'auth':'noauth','udp':False}}],'outbounds':[{'protocol':p,'settings':{'vnext':[{'address':c['address'],'port':int(c['port']),'users':[user]}]},'streamSettings':stream,'tag':'probe'}]}
async def run(args):
    base=args.url.rstrip('/');headers={'Accept':'application/json'}
    async with httpx.AsyncClient(timeout=20) as client:
        r=await client.get(base+'/api/probe/config/'+args.token,headers=headers);r.raise_for_status();data=r.json()
    protocol=data['protocol'];c=data['config'];xray=args.xray
    with tempfile.TemporaryDirectory(prefix='v2m-agent-') as td:
        port=0
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        cfg=Path(td)/'config.json';cfg.write_text(json.dumps(build_xray(c,protocol,port),separators=(',',':')))
        started=time.perf_counter();proc=await asyncio.create_subprocess_exec(xray,'run','-c',str(cfg),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        ok=False;lat=None
        try:
            for _ in range(80):
                try:
                    rd,wr=await asyncio.open_connection('127.0.0.1',port);wr.close();await wr.wait_closed();break
                except OSError:await asyncio.sleep(.05)
            proxy=f'socks5://127.0.0.1:{port}'
            async with httpx.AsyncClient(proxy=proxy,timeout=10,verify=False) as c2:
                rr=await c2.get('https://www.gstatic.com/generate_204')
                if rr.status_code not in (200,204):raise RuntimeError(f'HTTP {rr.status_code}')
            lat=round((time.perf_counter()-started)*1000,1);ok=True
        except Exception as e:print('probe failed:',e)
        finally:
            proc.terminate()
            try:await asyncio.wait_for(proc.wait(),2)
            except asyncio.TimeoutError:proc.kill();await proc.wait()
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(base+'/api/probe/report',json={'probe_token':args.token,'status':'online' if ok else 'offline','latency_ms':lat})
    print('ONLINE' if ok else 'OFFLINE',lat if lat is not None else '-')
async def main():
    p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--token',required=True);p.add_argument('--xray',default='xray');p.add_argument('--once',action='store_true');a=p.parse_args()
    while True:
        await run(a)
        if a.once:break
        await asyncio.sleep(30)
if __name__=='__main__':asyncio.run(main())
