from pydantic import BaseModel, Field

from jarvis.tools.registry import Registry, Tool


class RememberArgs(BaseModel):
    text: str = Field(description="One short sentence, in the user's words")


class ForgetArgs(BaseModel):
    memory_id: int = Field(description="The #id shown in the memory list")


class RecallArgs(BaseModel):
    query: str | None = Field(default=None, description="Keyword to search for; omit to list everything")


def register_memory_tools(registry: Registry, store) -> None:
    def remember(text):
        return store.add(text)

    def forget(memory_id):
        return {"forgotten": memory_id} if store.remove(memory_id) else {"error": f"No memory with id {memory_id}."}

    def recall(query=None):
        return store.search(query) if query else store.all()

    def describe_remember(a):
        return f"Remember: {a['text']}"

    def describe_forget(a):
        m = store.get(a["memory_id"])
        return f"Forget: {m['text']}" if m else f"Forget memory #{a['memory_id']}"

    for name, desc, schema, fn, confirm, describe, done in [
        ("remember", "Save one short fact the user asked you to remember.", RememberArgs, remember, True,
         describe_remember, lambda r: "Got it, I'll remember that."),
        ("forget", "Remove a remembered fact by its id.", ForgetArgs, forget, True,
         describe_forget, lambda r: "Okay, forgotten."),
        ("recall", "List everything remembered, or search it by keyword.", RecallArgs, recall, False, None, None),
    ]:
        registry.add(Tool(name=name, domain="memory", description=desc, args_schema=schema, fn=fn,
                          needs_confirm=confirm, describe=describe, done=done))
