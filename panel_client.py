"""
Thin async client for the Spider Panel API.

Covers the main documented endpoints:
  - Auth (login / change password)
  - Users (CRUD, toggle, reset traffic, config, QR, sub)
  - Inbounds (list, create, update, delete, generate keys)
  - Groups
  - Scanner (saved IPs)
  - Cloudflare Worker
  - Server stats
"""

from __future__ import annotations

import httpx


class PanelError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Panel API error {status_code}: {detail}")


def normalize_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("users", "inbounds", "groups", "items", "data", "results", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        return list(data.values())
    return []


def obj_id(obj, *keys):
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] is not None:
                return obj[k]
        return None
    return obj


def obj_label(obj, *keys, default=None):
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] not in (None, ""):
                return str(obj[k])
        return default if default is not None else str(obj)
    return str(obj)


_CONFIG_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://", "ssr://")


def extract_config_uris(obj) -> list[str]:
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
    def __init__(self, base_url: str, admin_password: str, timeout: float = 25.0):
        self.base_url = base_url.rstrip("/")
        self.admin_password = admin_password
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._logged_in = False

    async def close(self):
        await self._client.aclose()

    # ---- auth -----------------------------------------------------

    async def login(self):
        r = await self._client.post("/api/login", json={"password": self.admin_password})
        if r.status_code >= 400:
            raise PanelError(r.status_code, r.text)
        self._logged_in = True

    async def change_password(self, current: str, new: str) -> dict:
        r = await self._request(
            "POST",
            "/api/change-password",
            json={"current_password": current, "new_password": new},
        )
        return r.json()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self._logged_in:
            await self.login()
        r = await self._client.request(method, path, **kwargs)
        if r.status_code == 401:
            await self.login()
            r = await self._client.request(method, path, **kwargs)
        if r.status_code >= 400:
            raise PanelError(r.status_code, r.text)
        return r

    # ---- users ----------------------------------------------------

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

    async def toggle_user(self, user_id) -> dict:
        r = await self._request("PATCH", f"/api/users/{user_id}/toggle")
        return r.json()

    async def reset_user_traffic(self, user_id) -> dict:
        r = await self._request("PATCH", f"/api/users/{user_id}/reset")
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
        r = await self._request("GET", f"/api/sub/{identifier}")
        return r.json()

    def sub_page_url(self, identifier: str) -> str:
        return f"{self.base_url}/sub/{identifier}"

    # ---- inbounds -------------------------------------------------

    async def list_inbounds(self) -> list:
        r = await self._request("GET", "/api/inbounds")
        return normalize_list(r.json())

    async def create_inbound(self, payload: dict) -> dict:
        r = await self._request("POST", "/api/inbounds", json=payload)
        return r.json()

    async def update_inbound(self, inbound_id, payload: dict) -> dict:
        r = await self._request("PATCH", f"/api/inbounds/{inbound_id}", json=payload)
        return r.json()

    async def delete_inbound(self, inbound_id) -> None:
        await self._request("DELETE", f"/api/inbounds/{inbound_id}")

    async def generate_reality_keys(self, inbound_id) -> dict:
        r = await self._request("POST", f"/api/inbounds/{inbound_id}/generate-reality-keys")
        return r.json()

    async def generate_short_id(self, inbound_id) -> dict:
        r = await self._request("POST", f"/api/inbounds/{inbound_id}/generate-short-id")
        return r.json()

    # ---- groups ---------------------------------------------------

    async def list_groups(self) -> list:
        r = await self._request("GET", "/api/groups")
        return normalize_list(r.json())

    async def create_group(self, payload: dict) -> dict:
        r = await self._request("POST", "/api/groups", json=payload)
        return r.json()

    async def update_group(self, group_id, payload: dict) -> dict:
        r = await self._request("PATCH", f"/api/groups/{group_id}", json=payload)
        return r.json()

    async def delete_group(self, group_id) -> None:
        await self._request("DELETE", f"/api/groups/{group_id}")

    # ---- scanner --------------------------------------------------

    async def scanner_ips(self, ctype: str) -> dict:
        r = await self._request("GET", f"/api/scanner/ips/{ctype}")
        return r.json()

    async def scanner_resolve(self, host: str) -> dict:
        r = await self._request("GET", "/api/scanner/resolve", params={"host": host})
        return r.json()

    # ---- worker ---------------------------------------------------

    async def worker_status(self) -> dict:
        r = await self._request("GET", "/api/worker")
        return r.json()

    async def worker_sync(self) -> dict:
        r = await self._request("POST", "/api/worker/sync")
        return r.json()

    async def worker_sync_source(self) -> dict:
        r = await self._request("POST", "/api/worker/sync-source")
        return r.json()

    async def worker_locations(self) -> dict:
        r = await self._request("GET", "/api/worker/locations")
        return r.json()

    async def worker_disconnect(self) -> dict:
        r = await self._request("DELETE", "/api/worker")
        return r.json()

    # ---- server ---------------------------------------------------

    async def server_stats(self) -> dict:
        r = await self._request("GET", "/api/server/stats")
        return r.json()
