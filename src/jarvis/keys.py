import asyncio
import ipaddress
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi.responses import JSONResponse

from jarvis.appapi import AppService
from jarvis.google.auth import PROVIDER, PgTokenStore
from jarvis.llm import creds_for, normalize_base_url
from jarvis.llm_check import LLMCheckError, test_models
from jarvis.tenant import auth_runtime
from jarvis.users import SecretStore, UserStore, _put_secret

OPTIONAL = {"deepgram": "deepgram_key", "tavily": "tavily_key"}


def last4(value: str | None) -> str | None:
    """All a response ever shows of a key; None for short values, where 4 characters would give most of it away."""
    return value[-4:] if value and len(value) >= 8 else None


def clean_base_url(raw: str) -> str:
    """normalize_base_url, plus: the host's trailing dot dropped, localhost and non-public IP literals refused.
    What a name resolves to is checked again on every request by the guarded client."""
    url = normalize_base_url(raw)
    parts = urlsplit(url)
    host = parts.hostname.rstrip(".")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if not host or host == "localhost" or host.endswith(".localhost") or (ip and not ip.is_global):
        raise ValueError("The endpoint must be a public host.")
    netloc = f"[{host}]" if ip and ip.version == 6 else host
    return parts._replace(netloc=netloc + (f":{parts.port}" if parts.port else "")).geturl()


def _fail(error: str, model: str | None = None) -> JSONResponse:
    return JSONResponse({"error": error, "model": model}, status_code=422)


class Keys:
    """GET/PUT /me and /settings/*: the caller's own account and keys. Key values are write-only: responses show
    set/not-set and the last 4 characters; nothing is stored until it works; every change rebuilds the runtime."""

    def __init__(self, devices, tenants, check=test_models):
        self.devices, self.tenants, self.check = devices, tenants, check

    async def authenticate(self, authorization: str):
        return await auth_runtime(self.devices, self.tenants, authorization)

    def _store(self) -> SecretStore:
        return SecretStore(self.tenants.pool, self.tenants.s.fernet_key)

    def _me(self, uid: int) -> dict:
        pool = self.tenants.pool
        user, sec = UserStore(pool).get(uid), self._store().all(uid)
        creds = creds_for(sec, self.tenants.s)
        return {"email": user.email, "tz": user.tz,
                "setup": {"google": PgTokenStore(pool, uid).get(PROVIDER) is not None, "llm": creds is not None,
                          "deepgram": bool(sec.get("deepgram_key")), "tavily": bool(sec.get("tavily_key"))},
                "keys": {"llm_base_url": creds and creds.base_url, "llm_key_last4": last4(sec.get("llm_key")),
                         "model_fast": creds and ",".join(creds.fast), "model_strong": creds and ",".join(creds.strong),
                         "deepgram_last4": last4(sec.get("deepgram_key")), "tavily_last4": last4(sec.get("tavily_key"))}}

    async def me(self, rt) -> dict:
        return await asyncio.to_thread(self._me, rt.user.id)

    async def _changed(self, rt, name: str, args: dict) -> dict:
        self.tenants.invalidate(rt.user.id)
        await AppService._audit(rt, name, args, {"ok": True})  # names and the endpoint only, never a key value
        return await self.me(rt)

    async def set_tz(self, rt, tz: str):
        tz = tz.strip()
        try:
            ZoneInfo(tz)
        except Exception:  # not found, or not a zone name at all ("", "../x", a directory)
            return JSONResponse({"error": "bad_timezone"}, status_code=422)
        await asyncio.to_thread(UserStore(self.tenants.pool).set_tz, rt.user.id, tz)
        return await self._changed(rt, "set_timezone", {"tz": tz})

    async def set_llm(self, rt, base_url: str, api_key: str, model_fast: str = "", model_strong: str = ""):
        try:
            url = clean_base_url(base_url)
        except ValueError:
            return _fail("bad_url")
        new = {"llm_base_url": url, "llm_key": api_key, "model_fast": model_fast.strip(), "model_strong": model_strong.strip()}
        creds = creds_for(new, self.tenants.s)
        if not (creds.fast and creds.strong):
            return _fail("no_models")
        try:
            await self.check(creds)  # every model the runtime would use, before anything is stored
        except LLMCheckError as e:
            return _fail(e.code, e.model)
        except httpx.InvalidURL:
            return _fail("bad_url")

        def save():
            with self.tenants.pool.connection() as c:  # one transaction: never half a config
                for name, value in new.items():
                    if value:
                        _put_secret(c, self._store().fernet, rt.user.id, name, value)
                    else:
                        c.execute("DELETE FROM user_secrets WHERE user_id = %s AND name = %s", (rt.user.id, name))

        await asyncio.to_thread(save)
        return await self._changed(rt, "set_llm", {"base_url": url, "fast": creds.fast, "strong": creds.strong})

    async def set_key(self, rt, which: str, api_key: str) -> dict:
        await asyncio.to_thread(self._store().put, rt.user.id, OPTIONAL[which], api_key)
        return await self._changed(rt, f"set_{which}", {})

    async def delete_key(self, rt, which: str) -> dict:
        await asyncio.to_thread(self._store().delete, rt.user.id, OPTIONAL[which])
        return await self._changed(rt, f"delete_{which}", {})
