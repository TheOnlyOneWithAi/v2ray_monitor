import base64
import json

from app.parser import parse_subscription


def test_vless_tls_ws():
    uri = "vless://00000000-0000-0000-0000-000000000001@example.com:443?security=tls&type=ws&sni=example.com&path=%2Fws#Germany"
    node = parse_subscription(uri)[0]
    assert node.protocol == "vless"
    assert node.name == "Germany"
    assert node.config["address"] == "example.com"
    assert node.config["streamSettings"]["network"] == "ws"
    assert node.config["streamSettings"]["tlsSettings"]["serverName"] == "example.com"


def test_vless_reality():
    uri = "vless://00000000-0000-0000-0000-000000000001@example.com:443?security=reality&sni=www.example.com&fp=chrome&pbk=PUBLICKEY&sid=abcd&type=tcp#Reality"
    node = parse_subscription(uri)[0]
    reality = node.config["streamSettings"]["realitySettings"]
    assert reality["publicKey"] == "PUBLICKEY"
    assert reality["shortId"] == "abcd"


def test_vmess_parser():
    raw = {
        "v": "2", "ps": "Japan", "add": "example.com", "port": "443",
        "id": "00000000-0000-0000-0000-000000000002", "aid": "0", "net": "ws",
        "host": "example.com", "path": "/ws", "tls": "tls",
    }
    uri = "vmess://" + base64.b64encode(json.dumps(raw).encode()).decode()
    node = parse_subscription(uri)[0]
    assert node.protocol == "vmess"
    assert node.name == "Japan"
    assert node.config["uuid"] == raw["id"]
    assert node.config["streamSettings"]["network"] == "ws"


def test_subscription_base64_and_mixed_lines():
    lines = "vless://00000000-0000-0000-0000-000000000001@example.com:443?type=tcp#one\ninvalid\n"
    encoded = base64.b64encode(lines.encode()).decode()
    nodes = parse_subscription(encoded)
    assert len(nodes) == 1
    assert nodes[0].name == "one"


def test_invalid_nodes_are_skipped():
    assert parse_subscription("vless://not-valid#x\nvmess://bad") == []


def test_node_limit():
    lines = "\n".join(
        f"vless://00000000-0000-0000-0000-{i:012d}@example.com:443?type=tcp#{i}"
        for i in range(5)
    )
    assert len(parse_subscription(lines, max_nodes=2)) == 2
