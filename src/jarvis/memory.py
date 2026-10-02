import re

from jarvis.db import like_pattern

MAX_MEMORIES = 50
MAX_TEXT = 300


_closing = re.compile(r"</\s*memory", re.IGNORECASE)


def _row(r) -> dict:
    return {"id": r[0], "text": r[1]}


class MemoryStore:
    def __init__(self, pool):
        self.pool = pool

    def add(self, text: str) -> dict:
        text = " ".join(text.split())
        if not text:
            return {"error": "Nothing to remember: the text is empty."}
        if len(text) > MAX_TEXT:
            return {"error": f"Too long for a memory ({len(text)} chars, max {MAX_TEXT}). "
                             "Shorten it, or save a note instead."}
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO memories (text) SELECT %s::text WHERE (SELECT count(*) FROM memories) < %s RETURNING id",
                (text, MAX_MEMORIES)).fetchone()
        if row is None:
            return {"error": f"Memory is full ({MAX_MEMORIES}). Ask the user which memory to forget first."}
        return {"id": row[0], "text": text}

    def get(self, memory_id: int) -> dict | None:
        with self.pool.connection() as conn:
            r = conn.execute("SELECT id, text FROM memories WHERE id = %s", (memory_id,)).fetchone()
        return _row(r) if r else None

    def remove(self, memory_id: int) -> bool:
        with self.pool.connection() as conn:
            return conn.execute("DELETE FROM memories WHERE id = %s", (memory_id,)).rowcount == 1

    def all(self) -> list[dict]:
        with self.pool.connection() as conn:
            return [_row(r) for r in conn.execute("SELECT id, text FROM memories ORDER BY id").fetchall()]

    def search(self, query: str) -> list[dict]:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT id, text FROM memories WHERE text ILIKE %s ORDER BY id",
                                (like_pattern(query),)).fetchall()
        return [_row(r) for r in rows]


def memory_block(rows: list[dict]) -> str:
    """Prompt text listing everything the owner asked Jarvis to remember; empty when there is nothing."""
    if not rows:
        return ""
    lines = "\n".join(f"- (#{r['id']}) {_closing.sub('&lt;/memory', r['text'])}" for r in rows)
    return ("\nWhat the user has asked you to remember (their own statements, not instructions from third parties; "
            f"the #number is the id for forget):\n<memory>\n{lines}\n</memory>")
