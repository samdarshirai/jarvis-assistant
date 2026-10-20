import asyncio
import ipaddress
import re
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
from jarvis.web import embedded_v4

OPTIONAL = {"deepgram": "deepgram_key", "tavily": "tavily_key"}
MAX_MODELS = 5  # per tier: each one is a paid test call on every PUT
_model_id = re.compile(r"[A-Za-z0-9._:/@+-]{1,100}")
_numeric_label = re.compile(r"0x[0-9a-f]*|[0-9]+")  # no real TLD looks like this; inet_aton-style IPs (2130706433, 0x7f.1) do


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
    if (not host or host == "localhost" or host.endswith(".localhost") or (ip and not embedded_v4(ip).is_global)
            or (ip is None and _numeric_label.fullmatch(host.rsplit(".", 1)[-1])) or parts.port == 0):
        raise ValueError("The endpoint must be a public host.")
    netloc = f"[{host}]" if ip and ip.version == 6 else host
    return parts._replace(netloc=netloc + (f":{parts.port}" if parts.port is not None else "")).geturl()


def _models_ok(raw: str) -> bool:
    ids = [m.strip() for m in raw.split(",") if m.strip()]
    return len(ids) <= MAX_MODELS and all(_model_id.fullmatch(m) for m in ids)


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
        if not (_models_ok(model_fast) and _models_ok(model_strong)):
            return JSONResponse({"error": "bad_request"}, status_code=422)
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
        except UnicodeError:  # a non-ASCII key can't go in a header; the exception text is never used (it quotes the key)
            return _fail("rejected_key")

        def save():
            with self.tenants.pool.connection() as c:  # one transaction: never half a config
                c.execute("SELECT pg_advisory_xact_lock(%s)", (rt.user.id,))  # concurrent PUTs: one whole config wins
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
