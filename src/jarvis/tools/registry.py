from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel


@dataclass(frozen=True)
class Tool:
    name: str
    domain: str
    description: str
    args_schema: type[BaseModel]
    fn: Callable[..., Any]
    needs_confirm: bool = True  # safe default: a new tool confirms unless marked read-only


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def needs_confirm(self, name: str) -> bool:
        t = self.get(name)
        return True if t is None else t.needs_confirm

    def for_domain(self, domain: str) -> list[Tool]:
        return [t for t in self._tools.values() if t.domain == domain]

    def lc_tools(self, domain: str) -> list[StructuredTool]:
        # Only used to describe tools to the model; execution goes through Tool.fn in the graph.
        return [
            StructuredTool.from_function(func=t.fn, name=t.name, description=t.description,
                                         args_schema=t.args_schema)
            for t in self.for_domain(domain)
        ]
