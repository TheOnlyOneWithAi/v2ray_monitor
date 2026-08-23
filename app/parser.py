"""Strict, secret-preserving VLESS/VMess subscription parser."""
import base64
import binascii
import json
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse


@dataclass(frozen=True)
class ParsedNode:
    name: str
    protocol: str
    config: dict


def _b64decode(value: str) -> bytes:
    value = "".join(value.strip().split()).replace("-", "+").replace("_", "/")
    if not value:
        return b""
    value += "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error as exc:
        raise ValueError("invalid base64") from exc


def _decode_text(text: str) -> str:
    text = text.strip().lstrip("\ufeff")
    if not text:
        return ""
    if "vless://" in text.lower() or "vmess://" in text.lower():
        return text
    try:
        decoded = _b64decode(text).decode("utf-8-sig")
        return decoded if ("vless://" in decoded.lower() or "vmess://" in decoded.lower()) else text
    except (UnicodeDecodeError, ValueError):
        return text


def _one(query: dict, key: str, default: str = "") -> str:
    values = query.get(key)
    return values[0] if values else default


def _bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def _name(fragment: str, fallback: str) -> str:
    return unquote(fragment or "").strip()[:200] or fallback


def _safe_port(value, default=443) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    if not 1 <= port <= 65535:
        raise ValueError("invalid port")
    return port


def _stream_from_vless(q: dict, address: str) -> dict:
    network = _one(q, "type", _one(q, "network", "tcp")).lower()
    security = _one(q, "security", "none").lower()
    if network not in {"tcp", "kcp", "ws", "http", "grpc", "httpupgrade", "xhttp"}:
        raise ValueError("unsupported VLESS transport")
    if security not in {"none", "tls", "reality"}:
        raise ValueError("unsupported VLESS security")
    stream = {"network": network}
    if security != "none":
        stream["security"] = security
    if security == "tls":
        stream["tlsSettings"] = {
            "serverName": _one(q, "sni", address),
            "allowInsecure": _bool(_one(q, "allowInsecure", "0")),
        }
        alpn = _one(q, "alpn")
        if alpn:
            stream["tlsSettings"]["alpn"] = [x for x in alpn.split(",") if x]
    elif security == "reality":
        stream["realitySettings"] = {
            "serverName": _one(q, "sni", address),
            "fingerprint": _one(q, "fp", "chrome"),
            "publicKey": _one(q, "pbk"),
            "shortId": _one(q, "sid"),
            "spiderX": _one(q, "spx", "/"),
        }
        if not stream["realitySettings"]["publicKey"]:
            raise ValueError("REALITY public key missing")
    if network == "ws":
        headers = {}
        host = _one(q, "host")
        if host:
            headers["Host"] = host
        stream["wsSettings"] = {"path": _one(q, "path", "/"), "headers": headers}
    elif network == "grpc":
        stream["grpcSettings"] = {
            "serviceName": _one(q, "serviceName", ""),
            "multiMode": _one(q, "mode") == "multi",
        }
    elif network == "httpupgrade":
        stream["httpupgradeSettings"] = {"path": _one(q, "path", "/"), "host": _one(q, "host", "")}
    elif network == "xhttp":
        stream["xhttpSettings"] = {"path": _one(q, "path", "/"), "host": _one(q, "host", "")}
    return stream


def parse_vless(line: str, index: int) -> ParsedNode:
    uri = urlparse(line.strip())
    if uri.scheme.lower() != "vless" or not uri.hostname or not uri.username:
        raise ValueError("invalid VLESS URI")
    query = parse_qs(uri.query, keep_blank_values=True)
    config = {
        "address": uri.hostname,
        "port": _safe_port(uri.port, 443),
        "uuid": unquote(uri.username),
        "flow": _one(query, "flow", ""),
        "streamSettings": _stream_from_vless(query, uri.hostname),
    }
    return ParsedNode(_name(uri.fragment, f"VLESS {index}"), "vless", config)


def parse_vmess(line: str, index: int) -> ParsedNode:
    payload = line.strip().split("://", 1)[1].split("#", 1)[0]
    data = json.loads(_b64decode(payload).decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("invalid VMess JSON")
    address = str(data.get("add") or data.get("address") or data.get("host") or "").strip()
    uuid = str(data.get("id") or "").strip()
    if not address or not uuid:
        raise ValueError("invalid VMess URI")
    network = str(data.get("net") or "tcp").lower()
    tls = str(data.get("tls") or "").lower()
    if network not in {"tcp", "kcp", "ws", "http", "grpc", "httpupgrade", "xhttp"}:
        raise ValueError("unsupported VMess transport")
    stream = {"network": network}
    if tls in {"tls", "reality"}:
        stream["security"] = tls
        if tls == "tls":
            stream["tlsSettings"] = {"serverName": data.get("sni") or data.get("host") or address, "allowInsecure": False}
        else:
            stream["realitySettings"] = {
                "serverName": data.get("sni") or data.get("host") or address,
                "fingerprint": data.get("fp") or "chrome",
                "publicKey": data.get("pbk") or "",
                "shortId": data.get("sid") or "",
                "spiderX": data.get("spx") or "/",
            }
    if network == "ws":
        host = str(data.get("host") or "")
        stream["wsSettings"] = {"path": data.get("path") or "/", "headers": {"Host": host} if host else {}}
    elif network == "grpc":
        stream["grpcSettings"] = {"serviceName": data.get("path") or data.get("serviceName") or "", "multiMode": False}
    elif network == "httpupgrade":
        stream["httpupgradeSettings"] = {"path": data.get("path") or "/", "host": data.get("host") or ""}
    elif network == "xhttp":
        stream["xhttpSettings"] = {"path": data.get("path") or "/", "host": data.get("host") or ""}
    config = {
        "address": address,
        "port": _safe_port(data.get("port"), 443),
        "uuid": uuid,
        "alterId": int(data.get("aid") or 0),
        "security": str(data.get("scy") or "auto"),
        "streamSettings": stream,
    }
    return ParsedNode(str(data.get("ps") or f"VMess {index}")[:200], "vmess", config)


def parse_subscription(text: str, max_nodes: int = 2000) -> list[ParsedNode]:
    if max_nodes < 1:
        return []
    decoded = _decode_text(text)
    result: list[ParsedNode] = []
    for index, raw in enumerate(decoded.splitlines(), 1):
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        try:
            low = line.lower()
            if low.startswith("vless://"):
                result.append(parse_vless(line, index))
            elif low.startswith("vmess://"):
                result.append(parse_vmess(line, index))
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeError):
            continue
        if len(result) >= max_nodes:
            break
    return result
