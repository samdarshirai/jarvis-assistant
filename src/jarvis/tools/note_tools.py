from pydantic import BaseModel, Field

from jarvis.tools.registry import Registry, Tool

READ_CHARS = 2000


class CreateNoteArgs(BaseModel):
    title: str
    body: str = ""


class AppendNoteArgs(BaseModel):
    note_id: int
    text: str


class NoteIdArgs(BaseModel):
    note_id: int


class SearchNotesArgs(BaseModel):
    query: str


class ReadNoteArgs(BaseModel):
    note_id: int
    offset: int = Field(default=0, ge=0, description="Start position; use next_offset from the previous part")


class ListNotesArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=30)


def _preview(text: str) -> str:
    return text if len(text) <= 200 else f"{text[:200]}… (+{len(text) - 200} more chars)"


def register_note_tools(registry: Registry, store) -> None:
    def create_note(title, body=""):
        return store.create(title, body)

    def append_note(note_id, text):
        return store.append(note_id, text)

    def delete_note(note_id):
        return {"deleted": note_id} if store.delete(note_id) else {"error": f"No note with id {note_id}."}

    def search_notes(query):
        return store.search(query)

    def read_note(note_id, offset=0):
        n = store.get(note_id)
        if n is None:
            return {"error": f"No note with id {note_id}."}
        chunk = n["body"][offset:offset + READ_CHARS]
        out = {"id": n["id"], "title": n["title"], "body": chunk}
        end = offset + len(chunk)
        if end < len(n["body"]):
            out.update(truncated=True, next_offset=end, total_chars=len(n["body"]))
        return out

    def list_notes(limit=10):
        return store.recent(limit)

    def describe_create(a):
        return f"Save note '{a['title']}'" + (f": {_preview(a['body'])}" if a.get("body") else "")

    def describe_append(a):
        n = store.get(a["note_id"])
        return f"Add to note '{n['title']}': {_preview(a['text'])}" if n else f"Add to note #{a['note_id']}"

    def describe_delete(a):
        n = store.get(a["note_id"])
        return f"Delete note '{n['title']}'" if n else f"Delete note #{a['note_id']}"

    for name, desc, schema, fn, confirm, describe, done in [
        ("create_note", "Save a new note with a title and optional text.", CreateNoteArgs, create_note, True,
         describe_create, lambda r: "Saved the note."),
        ("append_note", "Add text to the end of an existing note.", AppendNoteArgs, append_note, True,
         describe_append, lambda r: "Added it to the note."),
        ("delete_note", "Delete a note.", NoteIdArgs, delete_note, True,
         describe_delete, lambda r: "Deleted the note."),
        ("search_notes", "Search notes by words in the title or text.", SearchNotesArgs, search_notes, False, None, None),
        ("read_note", "Read a note (long notes come in parts; pass offset for the next part).", ReadNoteArgs,
         read_note, False, None, None),
        ("list_notes", "List the most recently changed notes.", ListNotesArgs, list_notes, False, None, None),
    ]:
        registry.add(Tool(name=name, domain="notes", description=desc, args_schema=schema, fn=fn,
                          needs_confirm=confirm, describe=describe, done=done))
