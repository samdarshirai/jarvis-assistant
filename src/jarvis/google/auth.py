import json
from typing import Protocol

from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]
PROVIDER = "google"


class ReauthRequired(Exception):
    """Stored Google authorization is missing, expired or revoked."""


class TokenStore(Protocol):
    def get(self, provider: str) -> bytes | None: ...
    def put(self, provider: str, blob: bytes) -> None: ...


class PgTokenStore:
    def __init__(self, pool):
        self.pool = pool

    def get(self, provider):
        with self.pool.connection() as c:
            row = c.execute("SELECT blob FROM oauth_tokens WHERE provider = %s", (provider,)).fetchone()
        return bytes(row[0]) if row else None

    def put(self, provider, blob):
        with self.pool.connection() as c:
            c.execute(
                "INSERT INTO oauth_tokens (provider, blob) VALUES (%s, %s)"
                " ON CONFLICT (provider) DO UPDATE SET blob = EXCLUDED.blob",
                (provider, blob),
            )


def save_credentials(store: TokenStore, key: str, creds) -> None:
    store.put(PROVIDER, Fernet(key).encrypt(creds.to_json().encode()))


def load_credentials(store: TokenStore, key: str):
    blob = store.get(PROVIDER)
    if blob is None:
        raise ReauthRequired("No Google authorization stored.")
    info = json.loads(Fernet(key).decrypt(blob))
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise ReauthRequired("Google authorization expired or revoked.") from e
        save_credentials(store, key, creds)
    return creds


def build_service(name: str, version: str, store: TokenStore, key: str):
    return build(name, version, credentials=load_credentials(store, key), cache_discovery=False)


def main() -> None:
    """One-time consent on localhost: python -m jarvis.google.auth"""
    from google_auth_oauthlib.flow import InstalledAppFlow

    from jarvis.config import get_settings
    from jarvis.db import init_schema, make_pool

    s = get_settings()
    pool = make_pool(s.database_url)
    init_schema(pool)
    flow = InstalledAppFlow.from_client_secrets_file(s.google_client_secrets, SCOPES)
    creds = flow.run_local_server(port=0)
    save_credentials(PgTokenStore(pool), s.fernet_key, creds)
    print("Google authorization stored.")


if __name__ == "__main__":
    main()
