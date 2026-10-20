from jarvis.db import like_pattern

MAX_TITLE = 200
MAX_BODY = 20_000

_FTS = ("SELECT n.id, n.title, left(n.body, 120) FROM notes n, websearch_to_tsquery('english', %s) AS q "
        "WHERE n.user_id = %s AND n.tsv @@ q ORDER BY ts_rank(n.tsv, q) DESC, n.id DESC LIMIT %s")
_LIKE = ("SELECT id, title, left(body, 120) FROM notes WHERE user_id = %s AND (title ILIKE %s OR body ILIKE %s) "
         "ORDER BY updated_at DESC, id DESC LIMIT %s")


class NoteStore:
    def __init__(self, pool, user_id: int):
        self.pool, self.user_id = pool, user_id

    def create(self, title: str, body: str = "") -> dict:
        title, body = " ".join(title.split()), body.strip()
        if not title:
            return {"error": "A note needs a title."}
        if len(title) > MAX_TITLE:
            return {"error": f"Title too long (max {MAX_TITLE} chars)."}
        if len(body) > MAX_BODY:
            return {"error": f"Note too long (max {MAX_BODY} chars)."}
        with self.pool.connection() as conn:
            row = conn.execute("INSERT INTO notes (user_id, title, body) VALUES (%s, %s, %s) RETURNING id",
                               (self.user_id, title, body)).fetchone()
        return {"id": row[0], "title": title}

    def get(self, note_id: int) -> dict | None:
        with self.pool.connection() as conn:
            r = conn.execute("SELECT id, title, body, updated_at FROM notes WHERE id = %s AND user_id = %s",
                             (note_id, self.user_id)).fetchone()
        return {"id": r[0], "title": r[1], "body": r[2], "updated_at": r[3].isoformat()} if r else None

    def append(self, note_id: int, text: str) -> dict:
        text = text.strip()
        if not text:
            return {"error": "Nothing to add."}
        note = self.get(note_id)
        if note is None:
            return {"error": f"No note with id {note_id}."}
        if len(note["body"]) + len(text) + 1 > MAX_BODY:
            return {"error": "That would make the note too long."}
        with self.pool.connection() as conn:
            conn.execute("UPDATE notes SET body = CASE WHEN body = '' THEN %s::text ELSE body || E'\\n' || %s::text END, "
                         "updated_at = now() WHERE id = %s AND user_id = %s", (text, text, note_id, self.user_id))
        return {"id": note_id, "title": note["title"]}

    def delete(self, note_id: int) -> bool:
        with self.pool.connection() as conn:
            return conn.execute("DELETE FROM notes WHERE id = %s AND user_id = %s", (note_id, self.user_id)).rowcount == 1

    def recent(self, limit: int = 20) -> list[dict]:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT id, title, updated_at, left(body, 120) FROM notes WHERE user_id = %s"
                                " ORDER BY updated_at DESC, id DESC LIMIT %s", (self.user_id, limit)).fetchall()
        return [{"id": r[0], "title": r[1], "updated_at": r[2].isoformat(), "snippet": r[3]} for r in rows]

    def search(self, query: str, limit: int = 5) -> list[dict]:
        q = query.strip()
        if not q:
            return []
        with self.pool.connection() as conn:
            rows = conn.execute(_FTS, (q, self.user_id, limit)).fetchall()
            if not rows:  # stopword-only or partial-word queries: plain substring match
                p = like_pattern(q)
                rows = conn.execute(_LIKE, (self.user_id, p, p, limit)).fetchall()
        return [{"id": r[0], "title": r[1], "snippet": r[2]} for r in rows]
