import argparse
import logging

import httpx

from jarvis.config import get_settings
from jarvis.db import init_schema, make_pool
from jarvis.voice.devices import Devices
from jarvis.voice.protocol import MAX_SPEAK_CHARS

log = logging.getLogger(__name__)
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def fcm_access_token(credentials_path: str) -> tuple[str, str]:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=[FCM_SCOPE])
    creds.refresh(Request())
    return creds.project_id, creds.token


def send_push(project_id: str, access_token: str, tokens: list[str], text: str, client: httpx.Client) -> int:
    """Notification + the full text in data; tapping it opens the app, which asks the server to speak `text`."""
    sent = 0
    for t in tokens:
        r = client.post(f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
                        headers={"Authorization": f"Bearer {access_token}"},
                        json={"message": {"token": t, "notification": {"title": "Jarvis", "body": text[:200]},
                                          "data": {"speak": text}, "android": {"priority": "high"}}})
        if r.is_success:
            sent += 1
        else:
            log.warning("fcm push failed: %s %s", r.status_code, r.text[:200])
    return sent


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m jarvis.voice.push", description="Send a tap-to-play push to the paired phone.")
    p.add_argument("text")
    text = p.parse_args().text
    if len(text) > MAX_SPEAK_CHARS:  # the app asks the server to speak it on tap, and `speak` refuses longer text
        raise SystemExit(f"Text is {len(text)} characters; the limit is {MAX_SPEAK_CHARS}.")
    s = get_settings()
    if not s.fcm_credentials_path:
        raise SystemExit("Set JARVIS_FCM_CREDENTIALS_PATH first.")
    pool = make_pool(s.database_url)
    try:
        init_schema(pool)
        tokens = Devices(pool).fcm_tokens()
    finally:
        pool.close()
    project, access = fcm_access_token(s.fcm_credentials_path)
    with httpx.Client(timeout=10) as client:
        print(f"Delivered to {send_push(project, access, tokens, text, client)} of {len(tokens)} device(s).")


if __name__ == "__main__":
    main()
