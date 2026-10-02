from jarvis.tools.memory_tools import register_memory_tools
from jarvis.tools.registry import Registry


class FakeMemories:
    def __init__(self):
        self.rows, self.n = {}, 0

    def add(self, text):
        self.n += 1
        self.rows[self.n] = text
        return {"id": self.n, "text": text}

    def get(self, i):
        return {"id": i, "text": self.rows[i]} if i in self.rows else None

    def remove(self, i):
        return self.rows.pop(i, None) is not None

    def all(self):
        return [{"id": i, "text": t} for i, t in self.rows.items()]

    def search(self, q):
        return [r for r in self.all() if q.lower() in r["text"].lower()]


def reg(store=None):
    r = Registry()
    register_memory_tools(r, store or FakeMemories())
    return r


def test_only_remember_and_forget_confirm():
    r = reg()
    assert {t.name for t in r.for_domain("memory")} == {"remember", "forget", "recall"}
    assert {t.name for t in r.for_domain("memory") if t.needs_confirm} == {"remember", "forget"}
    assert not any(t.untrusted for t in r.for_domain("memory"))


def test_describe_shows_the_exact_text():
    s = FakeMemories()
    r = reg(s)
    assert r.get("remember").describe({"text": "Priya is my manager"}) == "Remember: Priya is my manager"
    m = s.add("likes tea")
    assert r.get("forget").describe({"memory_id": m["id"]}) == "Forget: likes tea"
    assert r.get("forget").describe({"memory_id": 99}) == "Forget memory #99"


def test_remember_forget_recall_roundtrip():
    s = FakeMemories()
    r = reg(s)
    m = r.get("remember").fn(text="Priya is my manager")
    assert m == {"id": 1, "text": "Priya is my manager"}
    assert r.get("recall").fn() == [{"id": 1, "text": "Priya is my manager"}]
    assert r.get("recall").fn(query="priya") == [{"id": 1, "text": "Priya is my manager"}]
    assert r.get("recall").fn(query="zzz") == []
    assert r.get("forget").fn(memory_id=1) == {"forgotten": 1}
    assert "error" in r.get("forget").fn(memory_id=1)


def test_done_lines_are_friendly_and_only_for_the_writes():
    r = reg()
    assert r.get("remember").done({"id": 1, "text": "x"}) == "Got it, I'll remember that."
    assert r.get("forget").done({"forgotten": 1}) == "Okay, forgotten."
    assert r.get("recall").done is None
