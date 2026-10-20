"""Sign in with Google, then a PKCE-style claim: the app sends sha256(verifier) at start and the verifier at claim,
so only the app that started the sign-in can collect the device token. The token never sits in a URL."""
import base64
import hashlib
import hmac
import secrets
from functools import partial

import psycopg
from cryptography.fernet import Fernet

from jarvis.google import web_auth
from jarvis.google.auth import PgTokenStore, save_credentials
from jarvis.users import SecretStore

TTL = 600  # seconds from start to claim


class BadState(Exception):
    """Unknown, expired or already used sign-in state."""


class Refused(Exception):
    """The Google account may not sign in: not on the list, unverified email, disabled or a different Google account."""


def _young(c, nonce: str, finished: bool):
    done = "IS NOT NULL" if finished else "IS NULL"
    return c.execute(f"SELECT challenge, created_at, device_token_enc FROM auth_attempts WHERE nonce = %s"
                     f" AND device_token_enc {done} AND created_at > now() - make_interval(secs => %s) FOR UPDATE",
                     (nonce, TTL)).fetchone()


class SignIn:
    def __init__(self, pool, users, devices, secrets_key: str, *, client_secrets: str, redirect_uri: str,
                 exchange=None, invalidate=lambda user_id: None):
        self.pool, self.users, self.devices, self.key = pool, users, devices, secrets_key
        self.client_secrets, self.redirect_uri, self.invalidate = client_secrets, redirect_uri, invalidate
        # code -> (credentials, verified ID-token claims); injectable so tests need no network
        self.exchange = exchange or partial(web_auth.exchange, client_secrets, redirect_uri)

    def start(self, challenge: str) -> tuple[str, str]:
        nonce = secrets.token_urlsafe(32)
        with self.pool.connection() as c:
            c.execute("DELETE FROM auth_attempts WHERE created_at <= now() - make_interval(secs => %s)", (TTL,))
            c.execute("INSERT INTO auth_attempts (nonce, challenge) VALUES (%s, %s)", (nonce, challenge))
        return nonce, web_auth.start_url(self.client_secrets, self.redirect_uri, nonce)

    def finish(self, nonce: str, code: str) -> str:
        """Deep link for the app. Raises BadState, Refused, or whatever the Google exchange raised."""
        with self.pool.connection() as c:  # take the attempt out: single use, and a refusal leaves nothing behind
            row = _young(c, nonce, finished=False)
            if row is None:
                raise BadState()
            c.execute("DELETE FROM auth_attempts WHERE nonce = %s", (nonce,))
        challenge, created_at, _ = row
        creds, claims = self.exchange(code)
        user = self.users.by_email(claims.get("email") or "")
        if (user is None or user.disabled or claims.get("email_verified") is not True or not claims.get("sub")
                or user.google_sub not in (None, claims["sub"])):
            raise Refused()
        if user.google_sub is None:
            try:
                self.users.set_sub(user.id, claims["sub"])
            except psycopg.errors.UniqueViolation:  # this Google account already belongs to another user
                raise Refused() from None
        save_credentials(PgTokenStore(self.pool, user.id), self.key, creds)
        _, token = self.devices.create(user.id)
        with self.pool.connection() as c:
            c.execute("INSERT INTO auth_attempts (nonce, challenge, created_at, device_token_enc) VALUES (%s, %s, %s, %s)",
                      (nonce, challenge, created_at, Fernet(self.key).encrypt(token.encode())))
        self.invalidate(user.id)  # new Google credentials: the next get() rebuilds the user's clients
        return f"jarvis://signin?nonce={nonce}"

    def claim(self, nonce: str, verifier: str) -> str | None:
        """The device token, once; None for an unknown, unfinished, expired or used attempt or a wrong verifier."""
        answer = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        with self.pool.connection() as c:
            row = _young(c, nonce, finished=True)
            if row is None or not hmac.compare_digest(row[0], answer):
                return None  # a wrong verifier keeps the attempt: guessing 256 bits is hopeless, burning it is a DoS
            c.execute("DELETE FROM auth_attempts WHERE nonce = %s", (nonce,))
        return Fernet(self.key).decrypt(bytes(row[2])).decode()

    def needs_setup(self, token: str) -> bool:
        """True while the token's user has no LLM key."""
        auth = self.devices.verify(token)
        return auth is None or SecretStore(self.pool, self.key).get(auth.user_id, "llm_key") is None
