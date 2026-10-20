from jarvis.users import UserStore
from jarvis.voice.devices import DeviceAuth, Devices, hash_token
from jarvis.voice.token import run


def test_create_then_verify_roundtrip(pool, uids):
    d = Devices(pool)
    did, token = d.create(uids[0])
    assert d.verify(token) == DeviceAuth(did, uids[0])
    assert d.verify(token + "x") is None
    assert d.verify("") is None


def test_verify_returns_user_id(pool, uids):
    d = Devices(pool)
    (da, ta), (db, tb) = d.create(uids[0]), d.create(uids[1])
    assert d.verify(ta) == DeviceAuth(da, uids[0])
    assert d.verify(tb).user_id == uids[1] and d.verify(tb).device_id == db


def test_verify_refuses_disabled_user(pool, uids):
    d, users = Devices(pool), UserStore(pool)
    _, token = d.create(uids[0])
    users.set_disabled(uids[0], True)
    assert d.verify(token) is None
    users.set_disabled(uids[0], False)
    assert d.verify(token) is not None


def test_only_the_hash_is_stored(pool, uids):
    d = Devices(pool)
    _, token = d.create(uids[0])
    with pool.connection() as c:
        rows = c.execute("SELECT token_hash FROM devices").fetchall()
    assert rows == [(hash_token(token),)] and token not in rows[0][0]


def test_verify_updates_last_seen(pool, uids):
    d = Devices(pool)
    did, token = d.create(uids[0])
    d.verify(token)
    with pool.connection() as c:
        assert c.execute("SELECT last_seen FROM devices WHERE id=%s", (did,)).fetchone()[0] is not None


def test_fcm_token_stored_and_listed(pool, uids):
    d = Devices(pool)
    did, _ = d.create(uids[0])
    assert d.fcm_tokens(uids[0]) == []
    d.set_fcm(did, "fcm-abc")
    d.set_fcm(did, "fcm-def")  # a refreshed token replaces the old one
    assert d.fcm_tokens(uids[0]) == ["fcm-def"]


def test_fcm_tokens_only_for_that_user(pool, uids):
    d = Devices(pool)
    da, _ = d.create(uids[0])
    db, _ = d.create(uids[1])
    d.set_fcm(da, "fcm-a")
    d.set_fcm(db, "fcm-b")
    assert (d.fcm_tokens(uids[0]), d.fcm_tokens(uids[1])) == (["fcm-a"], ["fcm-b"])


def test_fcm_token_moves_with_the_phone(pool, uids):
    # the same phone re-signed in (a new device row for the same user): the old row stops getting the pushes twice
    d = Devices(pool)
    old, _ = d.create(uids[0])
    new, _ = d.create(uids[0])
    d.set_fcm(old, "phone-1")
    d.set_fcm(new, "phone-1")
    assert d.fcm_tokens(uids[0]) == ["phone-1"]
    d.set_fcm(new, "phone-1")  # re-saving on the same device keeps it
    assert d.fcm_tokens(uids[0]) == ["phone-1"]


def test_fcm_token_replayed_by_another_user_never_clears_theirs(pool, uids):
    d = Devices(pool)
    da, _ = d.create(uids[0])
    db, _ = d.create(uids[1])
    d.set_fcm(da, "phone-a")
    d.set_fcm(db, "phone-a")  # B replays A's token: B's row gets it, A's registration stays
    assert (d.fcm_tokens(uids[0]), d.fcm_tokens(uids[1])) == (["phone-a"], ["phone-a"])


def test_revoke(pool, uids):
    d = Devices(pool)
    did, token = d.create(uids[0])
    assert d.revoke(did) is True
    assert d.verify(token) is None
    assert d.revoke(did) is False


def test_revoke_user_deletes_all_their_devices(pool, uids):
    d = Devices(pool)
    tokens = [d.create(uids[0])[1] for _ in range(2)]
    _, other = d.create(uids[1])
    assert d.revoke_user(uids[0]) == 2
    assert all(d.verify(t) is None for t in tokens) and d.verify(other) is not None
    assert d.revoke_user(uids[0]) == 0


def test_cli_creates_and_revokes(pool, uids):
    d, users = Devices(pool), UserStore(pool)
    out = run(["--user", "A@Example.com"], d, users)
    token = out.splitlines()[-1]
    auth = d.verify(token)
    assert auth == DeviceAuth(auth.device_id, uids[0]) and f"Device {auth.device_id}" in out
    did = auth.device_id
    assert run(["--user", "a@example.com", "--revoke", str(did)], d, users) == f"Revoked device {did}."
    assert run(["--user", "a@example.com", "--revoke", str(did)], d, users) == f"No device {did}."
    assert run(["--user", "nobody@example.com"], d, users) == "No user nobody@example.com."
