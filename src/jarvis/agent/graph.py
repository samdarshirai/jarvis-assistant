import asyncio
import json
import logging
import re
import time
from typing import Annotated, Literal, TypedDict

from googleapiclient.errors import HttpError
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

from jarvis.agent.domains import DOMAINS
from jarvis.google.auth import ReauthRequired
from jarvis.timeutil import now_local

log = logging.getLogger(__name__)

ROUTER_PROMPT = (
    "Classify the user's latest request. Reply with ONLY a comma-separated list, in the order the work "
    "must happen, chosen from: calendar, tasks, gmail, phone, chat. Use 'chat' alone when no calendar, task, "
    "email or phone work is needed. calendar = anything about the user's schedule, day, agenda, plans, "
    "availability or what is on or coming up; tasks = to-dos and deadlines; gmail = mail; phone = alarms, "
    "timers, navigation, texting. Examples: 'what does my day look like' -> calendar; 'am I free Friday' -> "
    "calendar; 'what do I have to do' -> tasks; 'add that booking email to my calendar' -> gmail, calendar; "
    "'set an alarm for 6 and put gym at 7 in my calendar' -> calendar, phone."
)
HISTORY = 40


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    domains: list[str]
    idx: int
    approved: bool
    client_actions: list[dict]
    read_untrusted: bool


KEYWORDS = {
    "calendar": r"calendar|event|meeting|appointment|free slot|schedule|agenda|my day|am i free",
    "tasks": r"\btasks?\b|to-?do",
    "gmail": r"e-?mail|inbox|gmail|draft",
    "phone": r"alarm|timer|navigat|directions",
}


def keyword_domain(text: str) -> list[str] | None:
    """Skip the router LLM call when exactly one domain's keywords appear; anything unclear goes to the router."""
    hits = [d for d, rx in KEYWORDS.items() if re.search(rx, text, re.IGNORECASE)]
    return hits if len(hits) == 1 else None


def parse_domains(text: str) -> list[str]:
    found: list[str] = []
    for tok in re.split(r"[,\s]+", text.lower()):
        if tok in DOMAINS and tok not in found:
            found.append(tok)
    if len(found) > 1 and "chat" in found:
        found.remove("chat")
    return found or ["chat"]


def window(messages: list, n: int = HISTORY) -> list:
    """Last n messages, trimmed so the slice never starts on an orphan tool result."""
    tail = messages[-n:]
    for i, m in enumerate(tail):
        if isinstance(m, HumanMessage):
            return tail[i:]
    while tail and isinstance(tail[0], ToolMessage):
        tail = tail[1:]
    return tail


def repair_tool_gaps(messages: list) -> list:
    """Return a copy where every AI tool call has a ToolMessage answer; state is never modified."""
    out: list = []
    i = 0
    while i < len(messages):
        m = messages[i]
        out.append(m)
        i += 1
        if isinstance(m, AIMessage) and m.tool_calls:
            answered = set()
            while i < len(messages) and isinstance(messages[i], ToolMessage):
                answered.add(messages[i].tool_call_id)
                out.append(messages[i])
                i += 1
            out.extend(ToolMessage("Not executed (superseded or interrupted).", tool_call_id=c["id"])
                       for c in m.tool_calls if c["id"] not in answered)
    return out


UNTRUSTED_TAGS = ("untrusted_email", "untrusted_web")


def wrap_untrusted(text: str, tag: str = "untrusted_email") -> str:
    """Mark third-party text as data; rewrite any closing wrapper tag so the content cannot end it early."""
    safe = re.sub(r"</\s*(untrusted_(?:email|web))", r"&lt;/\1", text, flags=re.IGNORECASE)
    return f"<{tag}>{safe}</{tag}>"


def untrusted_in_window(messages: list) -> bool:
    """True while any wrapped third-party result (email or web) is still inside the history the model sees."""
    opens = tuple(f"<{t}>" for t in UNTRUSTED_TAGS)
    return any(isinstance(m, ToolMessage) and isinstance(m.content, str) and m.content.startswith(opens)
               for m in window(messages))


def turn_replies(messages: list) -> list[str]:
    out: list[str] = []
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            break
        if isinstance(m, AIMessage) and not m.tool_calls and isinstance(m.content, str) and m.content:
            out.append(m.content)
    return out[::-1]


def build_graph(provider, registry, audit, checkpointer, tz: str):
    async def record(*args, **kw) -> None:
        # An audit failure must never break the turn or hide an executed side effect.
        try:
            await asyncio.to_thread(audit.record, *args, **kw)
        except Exception:
            log.exception("audit record failed")

    async def router(state: State) -> dict:
        last = state["messages"][-1]
        quick = keyword_domain(last.content) if isinstance(last, HumanMessage) and isinstance(last.content, str) else None
        if quick:
            return {"domains": quick, "idx": 0, "approved": False, "client_actions": [], "read_untrusted": False}
        resp = await provider.get("fast").ainvoke([SystemMessage(ROUTER_PROMPT), *repair_tool_gaps(window(state["messages"], 6))])
        return {"domains": parse_domains(str(resp.content)), "idx": 0, "approved": False, "client_actions": [],
                "read_untrusted": False}

    async def agent(state: State, config: RunnableConfig) -> dict:
        dom = DOMAINS[state["domains"][state["idx"]]]
        # voice turns trade some tool-calling strength for latency; gmail stays strong (untrusted content)
        voice = (config.get("configurable") or {}).get("voice")
        tier = "fast" if voice and dom.name != "gmail" else dom.tier
        llm = provider.get(tier, registry.lc_tools(dom.name) or None)
        system = SystemMessage(f"{dom.prompt}\nCurrent local time: {now_local(tz).strftime('%A %Y-%m-%d %H:%M %Z (UTC%z)')} ({tz}).")
        reply = await llm.ainvoke([system, *repair_tool_gaps(window(state["messages"]))])
        return {"messages": [reply], "approved": False}

    def after_agent(state: State) -> str:
        return "gate" if state["messages"][-1].tool_calls else "advance"

    async def gate(state: State) -> Command[Literal["tools", "reject"]]:
        calls = state["messages"][-1].tool_calls
        pending = [c for c in calls if registry.needs_confirm(c["name"])]
        if not pending:
            return Command(goto="tools", update={"approved": True})
        if len(pending) < len(calls):  # confirmed text must not change under the card: propose writes alone
            return Command(goto="tools", update={"approved": False})
        actions = []
        for c in pending:
            action = {"tool": c["name"], "args": c["args"]}
            tool = registry.get(c["name"])
            if tool and tool.describe:
                try:  # read-only lookup; the prompt still shows tool/args if it fails
                    kwargs = tool.args_schema(**c["args"]).model_dump(exclude_unset=True)
                    action["summary"] = await asyncio.to_thread(tool.describe, kwargs)
                except Exception:
                    log.exception("describe failed for %s", c["name"])
            actions.append(action)
        payload: dict = {"actions": actions}
        if state.get("read_untrusted") or untrusted_in_window(state["messages"]):
            payload["after_untrusted"] = True
        decision = interrupt(payload)
        if decision is True:
            return Command(goto="tools", update={"approved": True})
        return Command(goto="reject")

    async def reject(state: State) -> dict:
        out = []
        for c in state["messages"][-1].tool_calls:
            await record("tool", c["name"], args=c["args"], confirmation="cancelled")
            out.append(ToolMessage("Cancelled by the user. Do not retry; ask what they want instead.",
                                   tool_call_id=c["id"]))
        return {"messages": out}

    async def tools(state: State) -> dict:
        allowed = {t.name for t in registry.for_domain(state["domains"][state["idx"]])}
        actions = list(state.get("client_actions", []))
        out = []
        ran_untrusted = False
        dones: list[str] = []  # one wrap-up line per clean confirmed write; used only if every call in the step has one
        for c in state["messages"][-1].tool_calls:
            tool = registry.get(c["name"])
            confirm = registry.needs_confirm(c["name"])
            result: object
            t0 = time.monotonic()
            label = "blocked"
            if tool is None or c["name"] not in allowed:
                result = {"error": f"Unknown tool: {c['name']}"}
            elif confirm and not state["approved"]:
                result = {"error": "Not run: actions that need confirmation must be proposed alone, in their own "
                                   "step, after other tools have finished. Propose it again by itself."}
            else:
                label = "approved" if confirm else "not_required"
                try:
                    kwargs = tool.args_schema(**c["args"]).model_dump(exclude_unset=True)
                    result = await asyncio.to_thread(tool.fn, **kwargs)
                except ReauthRequired:
                    result = {"error": "Google authorization expired. Tell the user to run: python -m jarvis.google.auth"}
                except HttpError as e:
                    result = {"error": f"Google API error {e.resp.status}. Tell the user; do not retry writes."}
                except (ValueError, KeyError) as e:  # bad args/dates/tz; includes pydantic ValidationError
                    log.warning("tool %s rejected arguments: %r", c["name"], e)
                    result = {"error": f"Invalid arguments: {e}"}
                except Exception:
                    log.exception("tool %s failed", c["name"])
                    result = {"error": "Unexpected error while running the tool."}
            is_error = isinstance(result, dict) and "error" in result
            content = json.dumps(result, default=str, ensure_ascii=False)
            audit_result = result
            if tool is not None and tool.untrusted and label != "blocked" and not is_error:
                ran_untrusted = True
                audit_result = {"redacted": True, "chars": len(content), "message_id": c["args"].get("message_id")}
                content = wrap_untrusted(content, tool.untrusted_tag)
            await record(
                "tool", c["name"], args=c["args"], result=audit_result,
                confirmation=label,
                latency_ms=int((time.monotonic() - t0) * 1000))
            if isinstance(result, dict) and "client_action" in result:
                actions.append(result["client_action"])
            out.append(ToolMessage(content, tool_call_id=c["id"]))
            if label == "approved" and not is_error and tool.done:
                dones.append(tool.done(result))
        if dones and len(dones) == len(out):  # ponytail: templated reply, no LLM wrap-up; model phrasing only on errors/reads
            out.append(AIMessage(" ".join(dones)))
        return {"messages": out, "client_actions": actions,
                "read_untrusted": bool(state.get("read_untrusted")) or ran_untrusted}

    async def advance(state: State) -> dict:
        return {"idx": state["idx"] + 1}

    def after_advance(state: State) -> str:
        return "agent" if state["idx"] < len(state["domains"]) else END

    g = StateGraph(State)
    for name, fn in [("router", router), ("agent", agent), ("gate", gate), ("reject", reject),
                     ("tools", tools), ("advance", advance)]:
        g.add_node(name, fn)
    g.add_edge(START, "router")
    g.add_edge("router", "agent")
    g.add_conditional_edges("agent", after_agent, ["gate", "advance"])
    g.add_edge("reject", "agent")
    g.add_conditional_edges("tools", lambda s: "advance" if isinstance(s["messages"][-1], AIMessage) else "agent",
                            ["agent", "advance"])
    g.add_conditional_edges("advance", after_advance, ["agent", END])
    return g.compile(checkpointer=checkpointer)
