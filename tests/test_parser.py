import pytest
from app.parser import parse_subscription

def test_vless_parser():
    s='vless://00000000-0000-0000-0000-000000000001@example.com:443?security=tls&type=ws&sni=example.com&path=%2Fws#Germany'
    n=parse_subscription(s)[0]
    assert n.protocol=='vless'; assert n.name=='Germany'; assert n.config['address']=='example.com'; assert n.config['streamSettings']['network']=='ws'

def test_vmess_parser():
    import base64,json
    raw={'v':'2','ps':'Japan','add':'example.com','port':'443','id':'00000000-0000-0000-0000-000000000002','aid':'0','net':'ws','type':'none','host':'example.com','path':'/ws','tls':'tls'}
    uri='vmess://'+base64.b64encode(json.dumps(raw).encode()).decode()
    n=parse_subscription(uri)[0]
    assert n.protocol=='vmess'; assert n.name=='Japan'; assert n.config['uuid']==raw['id']

def test_mixed_subscription():
    s='vless://00000000-0000-0000-0000-000000000001@example.com:443?type=tcp#one\ninvalid\n'
    assert len(parse_subscription(s))==1
