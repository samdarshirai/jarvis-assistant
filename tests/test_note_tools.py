from jarvis.tools.note_tools import READ_CHARS, register_note_tools
from jarvis.tools.registry import Registry


class FakeNotes:
    def __init__(self):
        self.rows, self.n = {}, 0

    def create(self, title, body=""):
        self.n += 1
        self.rows[self.n] = {"id": self.n, "title": title, "body": body, "updated_at": "t"}
        return {"id": self.n, "title": title}

    def get(self, i):
        return self.rows.get(i)

    def append(self, i, text):
        if i not in self.rows:
            return {"error": "No note"}
        self.rows[i]["body"] += "\n" + text
        return {"id": i, "title": self.rows[i]["title"]}

    def delete(self, i):
        return self.rows.pop(i, None) is not None

    def recent(self, limit=20):
        return [{"id": r["id"], "title": r["title"], "updated_at": r["updated_at"]} for r in self.rows.values()][:limit]

    def search(self, q, limit=5):
        return [{"id": r["id"], "title": r["title"], "snippet": r["body"][:120]}
                for r in self.rows.values() if q.lower() in (r["title"] + r["body"]).lower()]


def reg(store=None):
    r = Registry()
    register_note_tools(r, store or FakeNotes())
    return r


def test_gating():
    r = reg()
    names = {t.name for t in r.for_domain("notes")}
    assert names == {"create_note", "append_note", "delete_note", "search_notes", "read_note", "list_notes"}
    assert {t.name for t in r.for_domain("notes") if t.needs_confirm} == {"create_note", "append_note", "delete_note"}


def test_describe_shows_title_and_a_body_preview_so_pasted_web_text_is_visible():
    s = FakeNotes()
    r = reg(s)
    long = "w" * 500
    d = r.get("create_note").describe({"title": "Vacuums", "body": long})
    assert d.startswith("Save note 'Vacuums': ") and d.endswith("… (+300 more chars)") and len(d) < 260
    assert r.get("create_note").describe({"title": "Empty"}) == "Save note 'Empty'"
    n = s.create("Plan", "x")
    assert r.get("append_note").describe({"note_id": n["id"], "text": "more"}) == "Add to note 'Plan': more"
    assert r.get("delete_note").describe({"note_id": n["id"]}) == "Delete note 'Plan'"
    assert r.get("delete_note").describe({"note_id": 99}) == "Delete note #99"


def test_create_search_list_roundtrip():
    r = reg()
    n = r.get("create_note").fn(title="Pricing", body="raise tiers")
    assert n["title"] == "Pricing"
    assert r.get("search_notes").fn(query="tiers")[0]["title"] == "Pricing"
    assert r.get("list_notes").fn()[0]["title"] == "Pricing"
    assert r.get("append_note").fn(note_id=n["id"], text="more")["title"] == "Pricing"
    assert r.get("delete_note").fn(note_id=n["id"]) == {"deleted": n["id"]}
    assert "error" in r.get("delete_note").fn(note_id=n["id"])


def test_read_note_returns_parts_with_a_continuation_offset():
    s = FakeNotes()
    r = reg(s)
    n = s.create("Long", "a" * (READ_CHARS * 2 + 100))
    first = r.get("read_note").fn(note_id=n["id"])
    assert len(first["body"]) == READ_CHARS and first["truncated"] is True
    assert first["next_offset"] == READ_CHARS and first["total_chars"] == READ_CHARS * 2 + 100
    last = r.get("read_note").fn(note_id=n["id"], offset=READ_CHARS * 2)
    assert len(last["body"]) == 100 and "truncated" not in last
    assert "error" in r.get("read_note").fn(note_id=999)


def test_done_lines():
    r = reg()
    assert r.get("create_note").done({"id": 1, "title": "T"}) == "Saved the note."
    assert r.get("append_note").done({"id": 1, "title": "T"}) == "Added it to the note."
    assert r.get("delete_note").done({"deleted": 1}) == "Deleted the note."
