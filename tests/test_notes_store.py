import pytest

from jarvis.notes import MAX_BODY, MAX_TITLE, NoteStore


@pytest.fixture
def store(pool):
    return NoteStore(pool)


def test_create_get_roundtrip(store):
    n = store.create("Pricing ideas", "raise the tiers")
    assert n["title"] == "Pricing ideas"
    got = store.get(n["id"])
    assert got["title"] == "Pricing ideas" and got["body"] == "raise the tiers" and got["updated_at"]


def test_create_without_body_and_title_whitespace_collapsed(store):
    n = store.create("  Plan \n B  ")
    assert n["title"] == "Plan B" and store.get(n["id"])["body"] == ""


@pytest.mark.parametrize("title", ["", "  ", "\n"])
def test_create_rejects_empty_title(store, title):
    assert "error" in store.create(title, "body")
    assert store.recent() == []


def test_create_rejects_oversize(store):
    assert "error" in store.create("t" * (MAX_TITLE + 1))
    assert "error" in store.create("t", "b" * (MAX_BODY + 1))
    assert store.recent() == []


def test_append_joins_with_newline_and_handles_empty_body(store):
    a = store.create("A", "first")
    assert store.append(a["id"], "second")["title"] == "A"
    assert store.get(a["id"])["body"] == "first\nsecond"
    b = store.create("B")
    store.append(b["id"], "only")
    assert store.get(b["id"])["body"] == "only"


def test_append_errors_write_nothing(store):
    a = store.create("A", "x" * (MAX_BODY - 5))
    assert "error" in store.append(a["id"], "   ")
    assert "error" in store.append(a["id"], "y" * 10)
    assert "error" in store.append(99999, "z")
    assert store.get(a["id"])["body"] == "x" * (MAX_BODY - 5)


def test_delete(store):
    n = store.create("gone")
    assert store.delete(n["id"]) is True
    assert store.get(n["id"]) is None
    assert store.delete(n["id"]) is False


def test_recent_orders_by_last_update(store):
    a = store.create("old")
    store.create("middle")
    store.append(a["id"], "touched")
    assert [n["title"] for n in store.recent()] == ["old", "middle"]
    assert len(store.recent(limit=1)) == 1


def test_search_stems_and_ranks_denser_note_first(store):
    store.create("Misc", "one meeting mentioned once")
    store.create("Agenda", "meetings meetings meetings about the weekly meeting")
    store.create("Unrelated", "groceries")
    hits = store.search("meeting")
    assert [h["title"] for h in hits] == ["Agenda", "Misc"]
    assert hits[0]["snippet"].startswith("meetings")


def test_search_falls_back_to_substring(store):
    store.create("Quarterly roadmap", "q3 goals")
    assert [h["title"] for h in store.search("roadm")] == ["Quarterly roadmap"]


@pytest.mark.parametrize("q", ["the", "'", "a & b | !c", "\\", "50%", "'; DROP TABLE notes; --", "ünï", "((", ""])
def test_search_never_raises_on_odd_queries(store, q):
    store.create("Note", "some text with 50% and ünï")
    assert isinstance(store.search(q), list)
    assert store.get(store.recent()[0]["id"]) is not None  # table still there
