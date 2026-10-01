from jarvis.voice.devices import Devices, hash_token
from jarvis.voice.token import run


def test_create_then_verify_roundtrip(pool):
    d = Devices(pool)
    did, token = d.create()
    assert d.verify(token) == did
    assert d.verify(token + "x") is None
    assert d.verify("") is None


def test_only_the_hash_is_stored(pool):
    d = Devices(pool)
    _, token = d.create()
    with pool.connection() as c:
        rows = c.execute("SELECT token_hash FROM devices").fetchall()
    assert rows == [(hash_token(token),)] and token not in rows[0][0]


def test_verify_updates_last_seen(pool):
    d = Devices(pool)
    did, token = d.create()
    d.verify(token)
    with pool.connection() as c:
        assert c.execute("SELECT last_seen FROM devices WHERE id=%s", (did,)).fetchone()[0] is not None


def test_fcm_token_stored_and_listed(pool):
    d = Devices(pool)
    did, _ = d.create()
    assert d.fcm_tokens() == []
    d.set_fcm(did, "fcm-abc")
    d.set_fcm(did, "fcm-def")  # a refreshed token replaces the old one
    assert d.fcm_tokens() == ["fcm-def"]


def test_revoke(pool):
    d = Devices(pool)
    did, token = d.create()
    assert d.revoke(did) is True
    assert d.verify(token) is None
    assert d.revoke(did) is False


def test_cli_creates_and_revokes(pool):
    d = Devices(pool)
    out = run([], d)
    token = out.splitlines()[-1]
    did = d.verify(token)
    assert did is not None and f"Device {did}" in out
    assert run(["--revoke", str(did)], d) == f"Revoked device {did}."
    assert run(["--revoke", str(did)], d) == f"No device {did}."
