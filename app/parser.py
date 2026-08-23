import base64, json
from urllib.parse import urlparse, parse_qs, unquote
from dataclasses import dataclass

@dataclass
class ParsedNode:
    name: str
    protocol: str
    config: dict

def _b64decode(s: str) -> bytes:
    s = s.strip().replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)
    return base64.b64decode(s)

def _name_from_fragment(fragment: str, fallback: str) -> str:
    return unquote(fragment).strip() or fallback

def parse_vless(line: str, index: int) -> ParsedNode:
    u=urlparse(line.strip())
    if u.scheme.lower()!='vless' or not u.hostname or not u.username:
        raise ValueError('invalid VLESS URI')
    q=parse_qs(u.query)
    def one(k, default=''): return q.get(k,[default])[0]
    security=one('security','none')
    transport=one('type','tcp')
    stream={'network':transport}
    if security in ('tls','reality'):
        stream['security']=security
    if security=='tls':
        stream['tlsSettings']={'serverName':one('sni'), 'allowInsecure': one('allowInsecure','0') in ('1','true')}
    if security=='reality':
        stream['realitySettings']={'serverName':one('sni'),'fingerprint':one('fp','chrome'),'publicKey':one('pbk'),'shortId':one('sid'),'spiderX':one('spx','/')}
    if transport=='ws': stream['wsSettings']={'path':one('path','/'),'headers':({'Host':one('host')} if one('host') else {})}
    elif transport=='grpc': stream['grpcSettings']={'serviceName':one('serviceName',''),'multiMode':one('mode')=='multi'}
    elif transport=='httpupgrade': stream['httpupgradeSettings']={'path':one('path','/'),'host':one('host')}
    elif transport=='xhttp': stream['xhttpSettings']={'path':one('path','/'),'host':one('host')}
    return ParsedNode(_name_from_fragment(u.fragment,f'VLESS {index}'),'vless',{'address':u.hostname,'port':u.port or 443,'uuid':unquote(u.username),'flow':one('flow'),'streamSettings':stream})

def parse_vmess(line: str, index: int) -> ParsedNode:
    raw=line.split('://',1)[1].split('#',1)[0]
    data=json.loads(_b64decode(raw).decode('utf-8'))
    host=data.get('add') or data.get('host')
    if not host or not data.get('id'): raise ValueError('invalid VMess URI')
    net=data.get('net','tcp'); tls=data.get('tls','')
    stream={'network':net}
    if tls: stream['security']='tls'; stream['tlsSettings']={'serverName':data.get('sni') or data.get('host',''),'allowInsecure':False}
    if net=='ws': stream['wsSettings']={'path':data.get('path') or '/','headers':({'Host':data.get('host')} if data.get('host') else {})}
    elif net=='grpc': stream['grpcSettings']={'serviceName':data.get('path','')}
    return ParsedNode(data.get('ps') or f'VMess {index}','vmess',{'address':host,'port':int(data.get('port') or 443),'uuid':data['id'],'alterId':int(data.get('aid') or 0),'security':data.get('scy') or 'auto','streamSettings':stream})

def parse_subscription(text: str, max_nodes=2000) -> list[ParsedNode]:
    text=text.strip()
    try:
        decoded=_b64decode(text).decode('utf-8') if not ('vless://' in text.lower() or 'vmess://' in text.lower()) else text
    except Exception: decoded=text
    out=[]
    for i,line in enumerate(decoded.splitlines(),1):
        line=line.strip()
        if not line: continue
        try:
            low=line.lower()
            if low.startswith('vless://'): out.append(parse_vless(line,i))
            elif low.startswith('vmess://'): out.append(parse_vmess(line,i))
        except Exception:
            continue
        if len(out)>=max_nodes: break
    return out
