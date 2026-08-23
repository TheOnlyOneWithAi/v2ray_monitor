import ipaddress, socket
from urllib.parse import urlparse
import httpx
from .config import get_settings

async def safe_fetch(url: str) -> str:
    p=urlparse(url)
    if p.scheme!='https' or not p.hostname: raise ValueError('subscription URL must use HTTPS')
    host=p.hostname
    try:
        infos=await __import__('asyncio').to_thread(socket.getaddrinfo,host,None,type=socket.SOCK_STREAM)
        ips={i[4][0] for i in infos}
    except Exception as e: raise ValueError('DNS resolution failed') from e
    for ip in ips:
        obj=ipaddress.ip_address(ip)
        if obj.is_private or obj.is_loopback or obj.is_link_local or obj.is_reserved or obj.is_multicast:
            raise ValueError('private/reserved destination blocked')
    s=get_settings()
    async with httpx.AsyncClient(timeout=15,follow_redirects=False,limits=httpx.Limits(max_connections=4)) as client:
        r=await client.get(url,headers={'User-Agent':'v2ray-monitor/1.0'})
        if r.status_code in (301,302,303,307,308): raise ValueError('redirects are disabled for SSRF protection')
        r.raise_for_status()
        if len(r.content)>s.max_subscription_bytes: raise ValueError('subscription is too large')
        return r.text
