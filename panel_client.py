"""
Thin async client for the Spider Panel API.

The panel authenticates via a session cookie: POST /api/login with the
admin password, then the cookie is sent on every subsequent request.
This client logs in lazily and re-logs-in automatically if a request
comes back 401 (e.g. because the session expired).

Endpoints used here are the ones documented in the panel's README. If
your fork of the panel uses slightly different field names for
creating/updating a user, adjust the payload you pass into
create_user() / update_user() — the client itself just forwards it.
"""

from __future__ import annotations

import httpx


class PanelError(Exception):
    """Raised when the panel API returns an error response."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Panel API error {status_code}: {detail}")


def normalize_list(data) -> list:
    """
    Different panel forks return list endpoints in different shapes:
    a plain JSON array, a dict wrapping the array under a key like
    "users"/"items"/"data"/"results", or even a dict keyed by id
    (id -> object). This normalizes all of those into a plain list.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("users", "inbounds", "items", "data", "results", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        # fall back: dict keyed by id -> treat values as the list
        return list(data.values())
    return []


def obj_id(obj, *keys):
    """Get an id from an item that may be a dict or a bare string/int."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] is not None:
                return obj[k]
        return None
    return obj


def obj_label(obj, *keys, default=None):
    """Get a display label from an item that may be a dict or a bare value."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] not in (None, ""):
                return str(obj[k])
        return default if default is not None else str(obj)
    return str(obj)


_CONFIG_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://", "ssr://")


def extract_config_uris(obj) -> list[str]:
    """
    Recursively walk any JSON-decoded structure (dict/list/str) and
    collect every string that looks like a proxy config URI
    (vless://, vmess://, trojan://, ss://...). This makes config
    extraction robust to whatever shape a given panel fork wraps its
    configs in (a "configs" list, a "custom_configs" list, a nested
    "status" object, etc.) — we don't need to know the exact schema,
    just recognize the URIs themselves. Order is preserved and
    duplicates are dropped.
    """
    found: list[str] = []

    def walk(node):
        if isinstance(node, str):
            if node.startswith(_CONFIG_SCHEMES):
                found.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(obj)

    seen = set()
    unique = []
    for uri in found:
        if uri not in seen:
            seen.add(uri)
            unique.append(uri)
    return unique


class PanelClient:
    def __init__(self, base_url: str, admin_password: str, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.admin_password = admin_password
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._logged_in = False

    async def close(self):
        await self._client.aclose()

    # ---- auth -----------------------------------------------------

    async def login(self):
        r = await self._client.post(
            "/api/login", json={"password": self.admin_password}
        )
        if r.status_code >= 400:
            raise PanelError(r.status_code, r.text)
        self._logged_in = True

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self._logged_in:
            await self.login()

        r = await self._client.request(method, path, **kwargs)
        if r.status_code == 401:
            # session expired -> log in again and retry once
            await self.login()
            r = await self._client.request(method, path, **kwargs)

        if r.status_code >= 400:
            raise PanelError(r.status_code, r.text)
        return r

    # ---- users ------------------------------------------------------

    async def list_users(self) -> list:
        r = await self._request("GET", "/api/users")
        return normalize_list(r.json())

    async def get_user(self, user_id) -> dict:
        r = await self._request("GET", f"/api/users/{user_id}")
        return r.json()

    async def create_user(self, payload: dict) -> dict:
        r = await self._request("POST", "/api/users", json=payload)
        return r.json()

    async def update_user(self, user_id, payload: dict) -> dict:
        r = await self._request("PATCH", f"/api/users/{user_id}", json=payload)
        return r.json()

    async def delete_user(self, user_id) -> None:
        await self._request("DELETE", f"/api/users/{user_id}")

    async def get_user_config(self, user_id) -> dict:
        r = await self._request("GET", f"/api/users/{user_id}/config")
        return r.json()

    async def get_user_qr(self, user_id) -> bytes:
        r = await self._request("GET", f"/api/users/{user_id}/qr")
        return r.content

    async def get_sub_data(self, identifier: str) -> dict:
        """
        GET /api/sub/{username} — the panel's own data source for the
        per-user subscription page. Returns everything needed to build
        the sub page: main configs, custom-IP configs, status config,
        etc. This is the endpoint to use to get *all* of a user's
        configs (a single /api/users/{id}/config call only returns one).
        """
        r = await self._request("GET", f"/api/sub/{identifier}")
        return r.json()

    def sub_page_url(self, identifier: str) -> str:
        return f"{self.base_url}/sub/{identifier}"

    # ---- inbounds -----------------------------------------------------

    async def list_inbounds(self) -> list:
        r = await self._request("GET", "/api/inbounds")
        return normalize_list(r.json())

    # ---- misc -----------------------------------------------------

    async def server_stats(self) -> dict:
        r = await self._request("GET", "/api/server/stats")
        return r.json()
