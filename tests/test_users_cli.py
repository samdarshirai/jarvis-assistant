from types import SimpleNamespace

from jarvis.users import SecretStore, UserStore, run
from jarvis.voice.devices import Devices

KEY = "ZmFrZWZha2VmYWtlZmFrZWZha2VmYWtlZmFrZWZha2U="
S = SimpleNamespace(owner_email="", timezone="Europe/Berlin", fernet_key=KEY, openrouter_api_key="",
                    models_fast="", models_strong="", deepgram_api_key="", tavily_api_key="")


def test_add_and_list(pool):
    assert "Added x@example.com" in run(["add", "X@Example.com", "--tz", "Asia/Kolkata"], pool, S)
    u = UserStore(pool).by_email("x@example.com")
    assert u.tz == "Asia/Kolkata"
    SecretStore(pool, KEY).put(u.id, "llm_key", "sk-supersecret")
    out = run(["list"], pool, S)
    assert "x@example.com" in out and "llm_key=yes" in out and "sk-supersecret" not in out
    assert run(["add", "bad-tz@example.com", "--tz", "Nope/Zone"], pool, S).startswith("Error")


def test_add_over_cap_errors(pool, monkeypatch):
    monkeypatch.setattr("jarvis.users.MAX_USERS", 1)
    run(["add", "a@example.com"], pool, S)
    UserStore(pool).set_disabled(UserStore(pool).by_email("a@example.com").id, True)  # disabled still counts
    assert "limit" in run(["add", "b@example.com"], pool, S)


def test_add_owner_twice_errors(pool):
    assert "owner" in run(["add", "o@example.com", "--owner"], pool, S)
    assert UserStore(pool).by_email("o@example.com").is_owner
    assert "already exists" in run(["add", "p@example.com", "--owner"], pool, S)


def test_disable_revokes_devices(pool):
    run(["add", "a@example.com"], pool, S)
    run(["add", "o@example.com", "--owner"], pool, S)
    u, o = UserStore(pool).by_email("a@example.com"), UserStore(pool).by_email("o@example.com")
    _, tok = Devices(pool).create(u.id)
    assert "revoked 1" in run(["disable", "A@example.com"], pool, S)
    assert UserStore(pool).get(u.id).disabled and Devices(pool).verify(tok) is None
    assert "owner" in run(["disable", "o@example.com"], pool, S) and not UserStore(pool).get(o.id).disabled
    run(["enable", "a@example.com"], pool, S)
    assert not UserStore(pool).get(u.id).disabled


def test_delete_requires_yes_and_reports_counts(pool):
    run(["add", "a@example.com"], pool, S)
    u = UserStore(pool).by_email("a@example.com")
    Devices(pool).create(u.id)
    out = run(["delete", "a@example.com"], pool, S)
    assert "devices: 1" in out and "rerun with --yes" in out
    assert UserStore(pool).get(u.id) is not None
    assert "Deleted" in run(["delete", "A@example.com", "--yes"], pool, S)
    assert UserStore(pool).get(u.id) is None


def test_delete_removes_checkpoint_thread(pool):
    with pool.connection() as c:
        c.execute("CREATE TABLE IF NOT EXISTS checkpoints (thread_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL)")
        c.execute("DELETE FROM checkpoints")
    try:
        run(["add", "a@example.com"], pool, S)
        u = UserStore(pool).by_email("a@example.com")
        with pool.connection() as c:
            c.execute("INSERT INTO checkpoints VALUES (%s, 'c1'), ('user:999', 'c1')", (f"user:{u.id}",))
        assert "checkpoints: 1" in run(["delete", "a@example.com"], pool, S)
        run(["delete", "a@example.com", "--yes"], pool, S)
        with pool.connection() as c:
            assert c.execute("SELECT thread_id FROM checkpoints").fetchall() == [("user:999",)]
    finally:
        with pool.connection() as c:
            c.execute("DROP TABLE checkpoints")
