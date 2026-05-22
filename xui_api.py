"""
Simple 3x-ui panel API client.
Docs: https://github.com/MHSanaei/3x-ui/wiki/Configuration#api-documentation
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import requests
from requests.exceptions import SSLError

import xui_link

log = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
}
_CSRF_HEADER = "X-CSRF-Token"
_SESSION_TTL = 55 * 60


@dataclass
class PanelConfig:
    name: str
    api_url: str
    username: str
    password: str
    sub_base: str
    verify_ssl: Optional[bool] = None  # None = use global .env setting
    config_address: Optional[str] = None  # public host for vless/vmess links (optional)
    config_port: Optional[int] = None
    flag: str = ""


@dataclass
class SubscriptionInfo:
    panel_name: str
    sub_id: str
    sub_url: str
    email: str
    enabled: bool
    up: int
    down: int
    total_limit: int
    expiry_ms: int
    pending_days: Optional[int]
    inbound_id: Optional[int]
    protocol: Optional[str]
    config_links: List[str] = field(default_factory=list)


def panel_to_dict(cfg: PanelConfig) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "sub_base": cfg.sub_base,
        "api_url": cfg.api_url,
    }
    if cfg.config_address:
        d["config_address"] = cfg.config_address
    if cfg.config_port:
        d["config_port"] = cfg.config_port
    if cfg.flag:
        d["flag"] = cfg.flag
    return d


_CONFIG_PREFIXES = ("vless://", "vmess://", "trojan://", "ss://", "ssr://")


def _is_config_line(line: str) -> bool:
    s = line.strip().lower()
    return any(s.startswith(p) for p in _CONFIG_PREFIXES)


def fetch_config_links_from_sub(
    sub_url: str,
    *,
    verify_ssl: bool,
    timeout: int = 15,
) -> List[str]:
    """Fetch vless/vmess/… links from the subscription URL."""
    headers = {
        **_DEFAULT_HEADERS,
        "Accept": "text/plain, application/json, */*",
    }
    try:
        r = requests.get(
            sub_url,
            timeout=timeout,
            verify=verify_ssl,
            headers=headers,
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        log.debug("fetch sub url failed: %s", exc)
        return []

    raw = (r.text or "").strip()
    if not raw:
        return []

    candidates: List[str] = []

    # Typical 3x-ui output: base64 with vless:// lines
    try:
        pad = "=" * (-len(raw) % 4)
        decoded = base64.b64decode(raw + pad).decode("utf-8", errors="replace")
        candidates.extend(decoded.splitlines())
    except Exception:
        candidates.append(raw)

    # Sometimes JSON array of links
    if raw.startswith("{") or raw.startswith("["):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, str):
                        candidates.append(item)
        except json.JSONDecodeError:
            pass

    links: List[str] = []
    seen: set[str] = set()
    for line in candidates:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if _is_config_line(s) and s not in seen:
            seen.add(s)
            links.append(s)
    return links


def _parse_settings(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def normalize_sub_base(url: str) -> str:
    """Normalize subscription base URL for panels.json key matching."""
    u = str(url or "").strip().rstrip("/")
    parsed = urlparse(u)
    if not parsed.scheme or not parsed.netloc:
        return u.lower()
    path = parsed.path.rstrip("/") or ""
    return f"{parsed.scheme}://{parsed.netloc}{path}".lower()


def parse_sub_link(text: str) -> Tuple[str, str]:
    """Parse subscription link → (sub_base, sub_id)."""
    raw = (text or "").strip()
    m = re.search(r"(https?://[^\s]+)", raw, re.I)
    url = (m.group(1) if m else raw).rstrip("/")

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("invalid subscription link")

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError("invalid subscription link")

    sub_id = parts[-1].strip()
    if not sub_id:
        raise ValueError("invalid subscription link")

    base_path = "/" + "/".join(parts[:-1])
    sub_base = f"{parsed.scheme}://{parsed.netloc}{base_path}"
    return sub_base, sub_id


def _wrap_request_error(exc: Exception, *, verify_ssl: bool) -> RuntimeError:
    """Wrap request errors (especially SSL) for logging."""
    msg = str(exc)
    if isinstance(exc, SSLError) or "CERTIFICATE_VERIFY_FAILED" in msg:
        hint = (
            "Panel SSL certificate invalid (expired or self-signed). "
            "Set VERIFY_SSL=false in .env or \"verify_ssl\": false in panels.json"
        )
        if verify_ssl:
            return RuntimeError(hint)
        return RuntimeError(f"{hint} ({msg[:200]})")
    return RuntimeError(f"Network error: {msg}")


class XuiPanel:
    def __init__(self, panel: PanelConfig, *, verify_ssl: bool = False, timeout: int = 15):
        self.panel = panel
        # Per-panel verify_ssl in json overrides global setting
        if panel.verify_ssl is not None:
            self.verify_ssl = bool(panel.verify_ssl)
        else:
            self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._session: Optional[requests.Session] = None
        self._session_exp: float = 0.0

    @property
    def _base(self) -> str:
        return self.panel.api_url.rstrip("/")

    def _new_session(self) -> requests.Session:
        s = requests.Session()
        s.verify = self.verify_ssl
        s.headers.update(_DEFAULT_HEADERS)
        return s

    def _fetch_csrf(self, sess: requests.Session) -> str:
        for path in ("/csrf-token", "/panel/csrf-token"):
            try:
                r = sess.get(
                    f"{self._base}{path}",
                    timeout=self.timeout,
                    verify=self.verify_ssl,
                )
            except requests.RequestException:
                continue
            if r.status_code == 404:
                continue
            if not r.ok:
                continue
            try:
                data = r.json()
            except Exception:
                continue
            if isinstance(data, dict) and data.get("success"):
                token = data.get("obj") or ""
                if isinstance(token, str):
                    return token
        return ""

    def _login(self) -> requests.Session:
        sess = self._new_session()
        csrf = self._fetch_csrf(sess)
        if csrf:
            sess.headers[_CSRF_HEADER] = csrf

        payload = {
            "username": self.panel.username,
            "password": self.panel.password,
        }
        for path in ("/login", "/panel/login"):
            try:
                r = sess.post(
                    f"{self._base}{path}",
                    json=payload,
                    timeout=self.timeout,
                    verify=self.verify_ssl,
                )
            except requests.RequestException as exc:
                raise _wrap_request_error(exc, verify_ssl=self.verify_ssl) from exc
            if r.status_code == 404:
                continue
            if r.status_code == 403:
                raise RuntimeError("Panel login denied (403). Check credentials or CSRF.")
            try:
                data = r.json()
            except Exception:
                data = {}
            if isinstance(data, dict) and data.get("success"):
                return sess
            msg = data.get("msg") if isinstance(data, dict) else r.text[:200]
            raise RuntimeError(f"Panel login failed: {msg or r.status_code}")

        raise RuntimeError("Panel login endpoint not found.")

    def _session_get(self) -> requests.Session:
        if self._session and time.time() < self._session_exp:
            return self._session
        self._session = self._login()
        self._session_exp = time.time() + _SESSION_TTL
        return self._session

    def _invalidate(self) -> None:
        self._session = None
        self._session_exp = 0.0

    def _request(self, method: str, path: str, *, retry: bool = True) -> dict:
        url = f"{self._base}{path}"
        sess = self._session_get()
        try:
            r = sess.request(
                method,
                url,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as exc:
            raise _wrap_request_error(exc, verify_ssl=self.verify_ssl) from exc

        if r.status_code in (401, 403) and retry:
            self._invalidate()
            return self._request(method, path, retry=False)

        try:
            data = r.json()
        except Exception:
            data = None

        if isinstance(data, dict) and data.get("success") is False:
            raise RuntimeError(str(data.get("msg") or "API request failed"))

        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")

        if not isinstance(data, dict):
            raise RuntimeError("Invalid JSON response from panel")
        return data

    def list_inbounds(self) -> List[dict]:
        data = self._request("GET", "/panel/api/inbounds/list")
        return list(data.get("obj") or [])

    def get_inbound(self, inbound_id: int) -> dict:
        data = self._request("GET", f"/panel/api/inbounds/get/{int(inbound_id)}")
        return dict(data.get("obj") or {})

    def build_config_links_from_api(self, sub_id: str) -> List[str]:
        """Build config links from API when direct sub URL fetch fails."""
        target = str(sub_id or "").strip()
        if not target:
            return []

        panel_d = panel_to_dict(self.panel)
        host = xui_link.default_host_for(panel_d)
        port_override = xui_link.default_port_for(panel_d)
        flag = str(panel_d.get("flag") or "").strip()

        links: List[str] = []
        seen: set[str] = set()
        for ib in self.list_inbounds():
            try:
                ibid = int(ib.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if ibid <= 0:
                continue
            try:
                full = self.get_inbound(ibid)
            except Exception as exc:
                log.debug("build_config_links get_inbound %s: %s", ibid, exc)
                continue
            settings = _parse_settings(full.get("settings"))
            for client in settings.get("clients") or []:
                if not isinstance(client, dict):
                    continue
                if str(client.get("subId") or "").strip() != target:
                    continue
                try:
                    link = xui_link.build_link(
                        full,
                        client,
                        host,
                        flag=flag,
                        default_port=port_override,
                    )
                except Exception as exc:
                    log.debug("build_link failed inbound %s: %s", ibid, exc)
                    continue
                if link and link not in seen:
                    seen.add(link)
                    links.append(link)
        return links

    def collect_config_links(self, sub_id: str, sub_url: str) -> List[str]:
        links = fetch_config_links_from_sub(
            sub_url, verify_ssl=self.verify_ssl, timeout=self.timeout,
        )
        if links:
            return links
        return self.build_config_links_from_api(sub_id)

    def find_client_by_sub_id(self, sub_id: str) -> Tuple[Optional[dict], Optional[int], Optional[dict]]:
        target = str(sub_id or "").strip()
        if not target:
            return None, None, None

        for ib in self.list_inbounds():
            try:
                ibid = int(ib.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if ibid <= 0:
                continue
            try:
                full = self.get_inbound(ibid)
            except Exception as exc:
                log.debug("get_inbound %s failed: %s", ibid, exc)
                continue
            settings = _parse_settings(full.get("settings"))
            for client in settings.get("clients") or []:
                if not isinstance(client, dict):
                    continue
                if str(client.get("subId") or "").strip() == target:
                    return full, ibid, client
        return None, None, None

    def get_client_traffics(
        self,
        email: str,
        *,
        client_uuid: Optional[str] = None,
    ) -> Optional[dict]:
        em = str(email or "").strip()

        def _obj(data: Optional[dict]) -> Optional[dict]:
            if not isinstance(data, dict):
                return None
            o = data.get("obj")
            return o if isinstance(o, dict) else None

        if em:
            try:
                data = self._request(
                    "GET",
                    f"/panel/api/inbounds/getClientTraffics/{quote(em, safe='')}",
                )
                out = _obj(data)
                if out:
                    return out
            except Exception as exc:
                log.debug("traffics by email: %s", exc)

        cu = str(client_uuid or "").strip()
        if cu:
            try:
                data = self._request(
                    "GET",
                    f"/panel/api/inbounds/getClientTrafficsById/{quote(cu, safe='')}",
                )
                return _obj(data)
            except Exception as exc:
                log.debug("traffics by id: %s", exc)
        return None

    def get_subscription_info(self, sub_id: str, sub_url: str) -> SubscriptionInfo:
        inbound, ibid, client = self.find_client_by_sub_id(sub_id)
        if not client:
            raise LookupError("subscription client not found")

        email = str(client.get("email") or "").strip()
        client_uuid = str(client.get("id") or client.get("password") or "").strip()
        stats = self.get_client_traffics(email, client_uuid=client_uuid) or {}

        up = int(stats.get("up") or 0)
        down = int(stats.get("down") or 0)
        total = int(stats.get("total") or client.get("totalGB") or 0)
        exp_ms = int(stats.get("expiryTime") or client.get("expiryTime") or 0)
        pending_days: Optional[int] = None
        if exp_ms < 0:
            pending_days = int(abs(exp_ms) // (86400 * 1000))

        config_links = self.collect_config_links(sub_id, sub_url)

        return SubscriptionInfo(
            panel_name=self.panel.name,
            sub_id=sub_id,
            sub_url=sub_url,
            email=email,
            enabled=bool(stats.get("enable", client.get("enable", True))),
            up=up,
            down=down,
            total_limit=total,
            expiry_ms=exp_ms,
            pending_days=pending_days,
            inbound_id=ibid,
            protocol=str(inbound.get("protocol") or "") if inbound else None,
            config_links=config_links,
        )


def load_panels(path: str) -> Dict[str, PanelConfig]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("panels.json must be a JSON object")

    panels: Dict[str, PanelConfig] = {}
    for key, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        api_url = str(cfg.get("api_url") or "").strip()
        username = str(cfg.get("username") or "").strip()
        password = str(cfg.get("password") or "").strip()
        if not api_url or not username or not password:
            raise ValueError(f"Panel '{key}': api_url, username and password are required")
        verify_ssl: Optional[bool] = None
        if "verify_ssl" in cfg:
            verify_ssl = bool(cfg["verify_ssl"])

        config_address = str(cfg.get("config_address") or "").strip() or None
        config_port: Optional[int] = None
        if cfg.get("config_port") not in (None, "", 0):
            try:
                config_port = int(cfg["config_port"])
            except (TypeError, ValueError):
                config_port = None

        panels[normalize_sub_base(key)] = PanelConfig(
            name=str(cfg.get("name") or key),
            api_url=api_url,
            username=username,
            password=password,
            sub_base=key.rstrip("/"),
            verify_ssl=verify_ssl,
            config_address=config_address,
            config_port=config_port,
            flag=str(cfg.get("flag") or "").strip(),
        )
    return panels


def resolve_panel(panels: Dict[str, PanelConfig], sub_base: str) -> PanelConfig:
    norm = normalize_sub_base(sub_base)
    if norm in panels:
        return panels[norm]

    # Flexible match with/without trailing /sub
    candidates = []
    for k, p in panels.items():
        if norm == k or norm.startswith(k + "/") or k.startswith(norm + "/"):
            candidates.append(p)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise LookupError("ambiguous panel mapping for sub base")

    raise LookupError("no panel configured for this subscription link")
