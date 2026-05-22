"""
ساخت URI اتصال (vless://, vmess://, trojan://, ss://) از روی داده‌های inbound
و client که از API پنل 3x-ui گرفته می‌شود.

این ماژول هیچ HTTP call ای نمی‌زند — فقط رشته‌ها را از روی ساختار JSON
پنل می‌سازد. تمام داده‌ی مورد نیاز از `/panel/api/inbounds/list` یا
`/panel/api/inbounds/get/{id}` قابل استخراج است.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, Optional
from urllib.parse import quote, urlparse

# =============================================================================
# کمکی‌ها
# =============================================================================

def parse_json(value: Any) -> dict:
    """اگر value رشته‌ی JSON باشد، آن را پارس می‌کند؛ در غیر این صورت dict خالی."""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}

def default_host_for(panel: Dict[str, Any]) -> str:
    """
    تشخیص host عمومی برای ساخت لینک (مقداری که در vless://HOST:PORT قرار می‌گیرد):
      ۱) فیلد config_address پنل (که ادمین دستی تنظیم می‌کند) — اولویت اول.
      ۲) در غیر این صورت از sub_base استخراج می‌شود.
      ۳) و نهایتاً از api_url.
    مقدار config_address می‌تواند دامنه‌ی خام (مثل `example.com`) یا یک URL کامل باشد.
    """
    raw = panel.get("config_address")
    if raw:
        s = str(raw).strip()
        if s:
            # اگر شبیه URL بود، hostname را درمی‌آوریم؛ در غیر این صورت خودش را برمی‌گردانیم
            if "://" in s:
                try:
                    p = urlparse(s)
                    if p.hostname:
                        return p.hostname
                except Exception:
                    pass
            return s

    for key in ("sub_base", "api_url"):
        v = panel.get(key)
        if not v:
            continue
        try:
            p = urlparse(str(v))
            if p.hostname:
                return p.hostname
        except Exception:
            continue
    return ""

def default_port_for(panel: Dict[str, Any]) -> Optional[int]:
    """
    پورت عمومی برای ساخت لینک (مقداری که در vless://HOST:PORT قرار می‌گیرد):
      ۱) فیلد config_port پنل (که ادمین دستی تنظیم می‌کند) — اولویت اول.
      ۲) اگر config_address به‌صورت URL باشد و پورت داشته باشد، از همان استفاده می‌شود.
      ۳) None — یعنی به‌صورت خودکار از پورت اینباند استفاده شود.
    """
    raw = panel.get("config_port")
    if raw not in (None, "", 0):
        try:
            n = int(raw)
            if 0 < n < 65536:
                return n
        except (TypeError, ValueError):
            pass

    addr = panel.get("config_address")
    if addr:
        s = str(addr).strip()
        if "://" in s:
            try:
                p = urlparse(s)
                if p.port:
                    return int(p.port)
            except Exception:
                pass

    return None

def _encode_params(params: Dict[str, Any]) -> str:
    parts = []
    for k, v in params.items():
        if v is None or v == "":
            continue
        parts.append(f"{k}={quote(str(v), safe='')}")
    return "&".join(parts)

def _merge_client(settings: dict, client: dict) -> dict:
    """
    کلاینت کامل را از داخل settings پیدا می‌کند (برای دسترسی به flow، password، …)
    و با داده‌هایی که به تابع پاس شده ادغام می‌کند.
    """
    clients = settings.get("clients") or []
    email = client.get("email") or ""
    key = client.get("id") or client.get("password") or ""

    for c in clients:
        if email and c.get("email") == email:
            return {**c, **{k: v for k, v in client.items() if v}}
        if key and (c.get("id") == key or c.get("password") == key):
            return {**c, **{k: v for k, v in client.items() if v}}
    return client

def find_client(inbound: dict, email: str) -> Optional[dict]:
    """پیدا کردن کلاینت داخل inbound.settings.clients بر اساس email."""
    settings = parse_json(inbound.get("settings"))
    for c in (settings.get("clients") or []):
        if c.get("email") == email:
            return c
    return None

# =============================================================================
# استخراج پارامترهای stream
# =============================================================================

def _ws_host(ws: dict) -> str:
    """
    استخراج Host header از wsSettings.
    3x-ui در نسخه‌های مختلف این مقدار را در جاهای متفاوتی ذخیره می‌کند:
      • wsSettings.headers.Host  (رایج‌ترین حالت)
      • wsSettings.host          (برخی نسخه‌های 3x-ui)
    """
    return (
        (ws.get("headers") or {}).get("Host")
        or (ws.get("headers") or {}).get("host")
        or ws.get("host")
        or ""
    )

def _stream_params(stream: dict, fallback_host: str = "") -> Dict[str, Any]:
    """
    query parameterهای URL برای vless/trojan را از streamSettings می‌سازد.
    پشتیبانی از: tcp / ws / grpc / http(h2) / quic + tls / reality / none

    fallback_host: اگر در تنظیمات transport هیچ host پیدا نشد، از این مقدار استفاده می‌شود.
                   معمولاً همان config_address یا hostname پنل است.
    """
    net = stream.get("network", "tcp")
    sec = stream.get("security", "none")
    params: Dict[str, Any] = {"type": net}
    if sec and sec != "none":
        params["security"] = sec

    # Reality
    if sec == "reality":
        rs = stream.get("realitySettings") or {}
        inner = rs.get("settings") or {}
        server_names = rs.get("serverNames") or []
        short_ids = rs.get("shortIds") or []
        sni = inner.get("serverName") or (server_names[0] if server_names else "")
        if inner.get("publicKey"):     params["pbk"] = inner["publicKey"]
        if inner.get("fingerprint"):   params["fp"]  = inner["fingerprint"]
        if sni:                        params["sni"] = sni
        if short_ids and short_ids[0]: params["sid"] = short_ids[0]
        if inner.get("spiderX"):       params["spx"] = inner["spiderX"]

    # TLS / XTLS
    elif sec in ("tls", "xtls"):
        ts = stream.get("tlsSettings") or stream.get("xtlsSettings") or {}
        sni = ts.get("serverName") or ""
        if sni:
            params["sni"] = sni
        alpn = ts.get("alpn") or []
        if alpn:
            params["alpn"] = ",".join(alpn)
        fp = ts.get("fingerprint") or (ts.get("settings") or {}).get("fingerprint", "")
        if fp:
            params["fp"] = fp

    # transport-specific
    if net == "ws":
        ws = stream.get("wsSettings") or {}
        path = ws.get("path") or "/"
        params["path"] = path

        host = _ws_host(ws) or fallback_host
        if host:
            params["host"] = host

    elif net == "httpupgrade":
        hus = stream.get("httpupgradeSettings") or {}
        path = hus.get("path") or "/"
        params["path"] = path
        host = hus.get("host") or fallback_host
        if host:
            params["host"] = host

    elif net == "splithttp":
        shs = stream.get("splithttpSettings") or {}
        path = shs.get("path") or "/"
        params["path"] = path
        host = shs.get("host") or fallback_host
        if host:
            params["host"] = host

    elif net == "grpc":
        gs = stream.get("grpcSettings") or {}
        if gs.get("serviceName"):
            params["serviceName"] = gs["serviceName"]
        if gs.get("multiMode"):
            params["mode"] = "multi"

    elif net == "tcp":
        tcp = stream.get("tcpSettings") or {}
        hdr = tcp.get("header") or {}
        if hdr.get("type") == "http":
            params["headerType"] = "http"
            req = hdr.get("request") or {}
            h = (req.get("headers") or {}).get("Host")
            if h:
                params["host"] = ",".join(h) if isinstance(h, list) else h
            paths = req.get("path") or []
            if paths:
                params["path"] = ",".join(paths) if isinstance(paths, list) else paths

    elif net in ("http", "h2"):
        hs = stream.get("httpSettings") or {}
        h = hs.get("host")
        if h:
            params["host"] = ",".join(h) if isinstance(h, list) else h
        if hs.get("path"):
            params["path"] = hs["path"]

    elif net == "quic":
        qs = stream.get("quicSettings") or {}
        if qs.get("security"):
            params["quicSecurity"] = qs["security"]
        if qs.get("key"):
            params["key"] = qs["key"]
        htype = (qs.get("header") or {}).get("type")
        if htype:
            params["headerType"] = htype

    elif net == "kcp":
        ks = stream.get("kcpSettings") or {}
        htype = (ks.get("header") or {}).get("type")
        if htype and htype != "none":
            params["headerType"] = htype

    return params

# =============================================================================
# builderها
# =============================================================================

def build_vless(host: str, port: int, client: dict, stream: dict, remark: str) -> str:
    uuid_ = client.get("id", "")
    flow  = client.get("flow", "")
    params = _stream_params(stream, fallback_host=host)
    params["encryption"] = "none"
    if flow:
        params["flow"] = flow
    qs = _encode_params(params)
    return f"vless://{uuid_}@{host}:{port}?{qs}#{quote(remark, safe='')}"

def build_trojan(host: str, port: int, client: dict, stream: dict, remark: str) -> str:
    pwd = client.get("password", "") or client.get("id", "")
    params = _stream_params(stream, fallback_host=host)
    if "security" not in params:
        params["security"] = "tls"
    qs = _encode_params(params)
    return f"trojan://{quote(pwd, safe='')}@{host}:{port}?{qs}#{quote(remark, safe='')}"

def build_vmess(host: str, port: int, client: dict, stream: dict, remark: str) -> str:
    net = stream.get("network", "tcp")
    sec = stream.get("security", "none")

    obj: Dict[str, Any] = {
        "v":    "2",
        "ps":   remark,
        "add":  host,
        "port": str(port),
        "id":   client.get("id", ""),
        "aid":  str(client.get("alterId", 0)),
        "scy":  client.get("security", "auto"),
        "net":  net,
        "type": "none",
        "host": "",
        "path": "",
        "tls":  "tls" if sec == "tls" else ("reality" if sec == "reality" else ""),
        "sni":  "",
        "alpn": "",
        "fp":   "",
    }

    if net == "ws":
        ws = stream.get("wsSettings") or {}
        obj["path"] = ws.get("path") or "/"
        obj["host"] = _ws_host(ws) or host
    elif net == "tcp":
        tcp = stream.get("tcpSettings") or {}
        hdr = tcp.get("header") or {}
        if hdr.get("type") == "http":
            obj["type"] = "http"
            req = hdr.get("request") or {}
            h = (req.get("headers") or {}).get("Host", "")
            obj["host"] = ",".join(h) if isinstance(h, list) else (h or "")
            p = req.get("path") or []
            obj["path"] = ",".join(p) if isinstance(p, list) else (p or "")
    elif net == "grpc":
        gs = stream.get("grpcSettings") or {}
        obj["path"] = gs.get("serviceName", "")
    elif net in ("http", "h2"):
        hs = stream.get("httpSettings") or {}
        h = hs.get("host") or []
        obj["host"] = ",".join(h) if isinstance(h, list) else (h or "")
        obj["path"] = hs.get("path", "")
    elif net in ("httpupgrade", "splithttp"):
        key = f"{net}Settings"
        s = stream.get(key) or {}
        obj["path"] = s.get("path") or "/"
        obj["host"] = s.get("host") or host

    if sec in ("tls", "reality"):
        ts = stream.get("tlsSettings") or stream.get("realitySettings") or {}
        obj["sni"]  = ts.get("serverName", "") or (ts.get("serverNames") or [""])[0]
        alpn = ts.get("alpn") or []
        if alpn:
            obj["alpn"] = ",".join(alpn)
        obj["fp"] = ts.get("fingerprint", "") or (ts.get("settings") or {}).get("fingerprint", "")

    j = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    b64 = base64.b64encode(j.encode("utf-8")).decode("ascii").rstrip("=")
    return f"vmess://{b64}"

def build_ss(host: str, port: int, client: dict, settings: dict, remark: str) -> str:
    method = client.get("method") or settings.get("method", "")
    pwd    = client.get("password") or settings.get("password", "")
    user_info = f"{method}:{pwd}"
    b64 = base64.urlsafe_b64encode(user_info.encode("utf-8")).decode("ascii").rstrip("=")
    return f"ss://{b64}@{host}:{port}#{quote(remark, safe='')}"

# =============================================================================
# رابط اصلی
# =============================================================================

def build_link(
    inbound: dict,
    client: dict,
    default_host: str,
    flag: str = "",
    default_port: Optional[int] = None,
) -> str:
    """
    از روی یک inbound (خروجی خام API) و یک client، URI اتصال می‌سازد.

    default_host: host عمومی که کاربر نهایی با آن وصل می‌شود.
    default_port: پورت عمومی (CDN/reverse-proxy) — در صورت None از پورت اینباند استفاده می‌شود.
    flag:         پیشوند remark؛ فرمت نهایی: "<flag> <email>"
                  مثلاً "🇩🇪" → "🇩🇪 ez_716167338_abc123"
    """
    proto = (inbound.get("protocol") or "").lower()
    inbound_port = int(inbound.get("port") or 0)
    port = int(default_port) if default_port else inbound_port

    listen = (inbound.get("listen") or "").strip()
    host = listen if listen and listen not in ("0.0.0.0", "::", "::0") else default_host
    if not host:
        raise RuntimeError("host عمومی برای ساخت لینک مشخص نیست.")

    settings = parse_json(inbound.get("settings"))
    stream   = parse_json(inbound.get("streamSettings"))

    full_client = _merge_client(settings, client)
    email = full_client.get("email") or client.get("email") or ""

    if flag and email:
        tag = f"{flag.strip()} {email}"
    elif flag:
        tag = flag.strip()
    elif email:
        tag = email
    else:
        tag = inbound.get("remark") or "ezterari"

    if proto == "vless":
        return build_vless(host, port, full_client, stream, tag)
    if proto == "vmess":
        return build_vmess(host, port, full_client, stream, tag)
    if proto == "trojan":
        return build_trojan(host, port, full_client, stream, tag)
    if proto == "shadowsocks":
        return build_ss(host, port, full_client, settings, tag)

    raise RuntimeError(f"پروتکل '{proto}' برای ساخت لینک پشتیبانی نمی‌شود.")
