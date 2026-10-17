"""Browser-based Google consent for the remote backend: the app opens start_url(), Google redirects to /google/callback."""
from cryptography.fernet import Fernet, InvalidToken
from google_auth_oauthlib.flow import Flow

from jarvis.google.auth import SCOPES

STATE_TTL = 600  # seconds the consent link stays valid


def make_state(key: str) -> str:
    return Fernet(key).encrypt(b"google-consent").decode()


def state_ok(key: str, state: str) -> bool:
    try:
        return Fernet(key).decrypt(state.encode(), ttl=STATE_TTL) == b"google-consent"
    except (InvalidToken, ValueError):
        return False


def _flow(secrets_path: str, redirect_uri: str) -> Flow:
    # No PKCE verifier: start and callback are separate requests, so a verifier held on the Flow would be lost.
    return Flow.from_client_secrets_file(secrets_path, SCOPES, redirect_uri=redirect_uri, autogenerate_code_verifier=False)


def start_url(secrets_path: str, redirect_uri: str, state: str) -> str:
    url, _ = _flow(secrets_path, redirect_uri).authorization_url(access_type="offline", prompt="consent", state=state)
    return url


def exchange(secrets_path: str, redirect_uri: str, code: str):
    flow = _flow(secrets_path, redirect_uri)
    flow.fetch_token(code=code)
    return flow.credentials
