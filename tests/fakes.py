from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class FakeChat(BaseChatModel):
    script: list[AIMessage]
    i: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self.script[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):
        return self


class FakeProvider:
    def __init__(self, scripts: dict[str, list[AIMessage]]):
        self._models = {tier: FakeChat(script=s) for tier, s in scripts.items()}

    def get(self, tier, tools=None):
        return self._models[tier]


class MemoryAudit:
    def __init__(self):
        self.records: list[dict] = []

    def record(self, kind, name, **kw):
        self.records.append({"kind": kind, "name": name, **kw})
