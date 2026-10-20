"""Browser-based Google sign-in for the remote backend: the app opens start_url(), Google redirects to /auth/callback."""
import json

from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow

from jarvis.google.auth import SCOPES


def _flow(secrets_path: str, redirect_uri: str) -> Flow:
    # No PKCE verifier: start and callback are separate requests, so a verifier held on the Flow would be lost.
    kw = dict(redirect_uri=redirect_uri, autogenerate_code_verifier=False)
    if secrets_path.lstrip().startswith("{"):  # the JSON itself (Railway has no file/volume), like the FCM setting
        return Flow.from_client_config(json.loads(secrets_path), SCOPES, **kw)
    return Flow.from_client_secrets_file(secrets_path, SCOPES, **kw)


def start_url(secrets_path: str, redirect_uri: str, state: str) -> str:
    url, _ = _flow(secrets_path, redirect_uri).authorization_url(access_type="offline", prompt="consent", state=state)
    return url


def exchange(secrets_path: str, redirect_uri: str, code: str):
    """(credentials, ID-token claims), the ID token verified against this web client."""
    flow = _flow(secrets_path, redirect_uri)
    flow.fetch_token(code=code)
    creds = flow.credentials
    return creds, id_token.verify_oauth2_token(creds.id_token, Request(), flow.client_config["client_id"])
