# LangSmith for Jarvis: a beginner's guide

A learning doc. It explains what LangSmith is, why Jarvis needs it, where it hooks into this codebase, and how to set it up and grow into it.

---

## 1. What is LangSmith?

LangSmith is a web dashboard (smith.langchain.com) that records everything your AI code does, so you can look at it afterwards. The recording is called **tracing**.

Vocabulary:

| Term | Meaning |
|------|---------|
| **Trace** | One full request, start to finish. Example: you ask Jarvis "what's on my calendar?" and everything that happens until the reply is one trace. |
| **Run / span** | One step inside a trace: a graph node, an LLM call, a tool call. Traces are trees of runs. |
| **Project** | A folder of traces. Ours is called `jarvis`. |
| **Tags / metadata** | Labels you attach to a trace (`telegram`, `voice`) so you can filter later. |
| **Dataset** | A saved set of example inputs/outputs, used for testing. |
| **Evaluator / experiment** | A scoring function, and one scored run of your agent over a dataset. |

## 2. Why do we need it?

Jarvis is an agent: a LangGraph router decides which tool to use, calls LLMs through OpenRouter, reads mail, and searches memory. When it gives a bad answer, plain logs can't tell you:

- Which model was called, with exactly what prompt?
- Did the router pick the wrong tool or did the tool return junk?
- Which step was slow? How many tokens (money) did it use?
- Did my prompt change make things better or worse?

LangSmith answers all of these by showing the whole tree of steps with inputs, outputs, timing, and cost. This is called **observability**.

## 3. Where does it plug into Jarvis?

Jarvis uses `langgraph` and `langchain-openai` (see `pyproject.toml`). LangSmith is built into that stack, so **tracing works with environment variables only, with no code changes**. LangChain/LangGraph checks the variables and sends traces automatically.

Places in the code that matter later (for tags and extra spans):

| File | What happens there |
|------|--------------------|
| `src/jarvis/appapi.py:238` | Flutter app calls `graph.ainvoke` |
| `src/jarvis/channels/telegram.py:111` | Telegram calls `graph.ainvoke` |
| `src/jarvis/voice/ws.py:190, 198, 240` | Voice path calls `graph.ainvoke` (including resume after approval) |
| `src/jarvis/agent/graph.py` | The router (`ROUTER_PROMPT`) and the main reply LLM call |
| `src/jarvis/proactive/brief.py`, `mailwatch.py` | Background jobs calling the LLM directly, without the graph |
| `src/jarvis/kg/block.py` | Memory/knowledge-graph retrieval (a plain function, so not auto-traced) |

## 4. How: setup

### 4.1 Account and key
1. Sign up at smith.langchain.com.
2. Settings, then API Keys, then create a key (starts with `lsv2_`).
3. Note your region. The EU site uses `eu.smith.langchain.com`.

### 4.2 Install
```bash
source .venv/bin/activate
pip install -U langsmith
```
It usually comes with `langchain-core` already. Add `"langsmith>=0.3"` to `pyproject.toml` dependencies so it is explicit.

### 4.3 Environment variables
In `.env` (and `.env.example`, with a fake key):
```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=jarvis
LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com   # EU accounts only; remove otherwise
```
Do **not** use the `JARVIS_` prefix. LangSmith reads `LANGSMITH_*` exactly.

**Important gotcha:** `src/jarvis/config.py` loads `.env` with pydantic-settings, which reads the file into the `Settings` object but does **not** put the values into the process environment. LangSmith only looks at the real environment. So:

- Docker/Railway: works, because `env_file: .env` in `docker-compose.prod.yml` injects them.
- Local `uvicorn`: either run `set -a; source .env; set +a` first, or add `load_dotenv()` (package `python-dotenv`) at the top of `main.py`.

### 4.4 Verify
1. Start the backend and send one message.
2. Open LangSmith, then Tracing, then project `jarvis`.
3. You should see a trace. Click into it to see the nested steps.

**No traces? Check in order:**
1. Is `LANGSMITH_TRACING=true` really visible to the process (`os.getenv`)?
2. Is the API key valid and the endpoint region correct?
3. Short scripts may exit before traces upload; call `langsmith.Client().flush()`. A long-running server does not need this.

## 5. Going further

### 5.1 Tags and metadata

**What they are.** Both are labels you attach to a trace. There is nothing to create in the LangSmith UI first. You write them in your code, and they appear on the trace automatically.

| | Tags | Metadata |
|---|---|---|
| Shape | list of strings: `["telegram", "proactive"]` | dict of key/value: `{"channel": "telegram", "user_id": 7}` |
| Use for | simple categories | values you want to filter or group by |
| In the UI | filter chips | key/value filters, and shown on the trace detail |

**Why.** Right now every trace looks the same. With labels you can ask "show me only Telegram traces" or "which channel is slowest?".

**How they get written.** Every LangChain/LangGraph call (`ainvoke`, `astream`, ...) accepts a second argument called the *config*, which is a plain dict. LangSmith looks for three optional keys in it:

```python
config = {
    "tags": ["telegram"],              # list of strings
    "metadata": {"channel": "telegram"},  # dict
    "run_name": "jarvis-turn",         # title shown in the trace list
}
await graph.ainvoke(graph_input, config)
```

Jarvis already passes a config (it holds the `thread_id`), so you **add these keys to the dict you already pass**. Don't replace it, or you'll lose `thread_id` and the conversation memory breaks.

**Where, with real code from this repo.**

Config is built by `thread_config()` in `src/jarvis/tenant.py:36`. Do not change that function: `tests/test_tenant.py:37-38` asserts its exact output, so those tests would fail. Add the keys at the call sites instead.

1. **Telegram**, `channels/telegram.py:111`. This line already merges extra keys into the config, so add yours in the same place:
```python
# before
return await graph.ainvoke(graph_input, {**self.cfg, "configurable": {**self.cfg["configurable"], "channel": "telegram"}})

# after
return await graph.ainvoke(graph_input, {
    **self.cfg,
    "configurable": {**self.cfg["configurable"], "channel": "telegram"},
    "tags": ["telegram"],
    "metadata": {"channel": "telegram"},
    "run_name": "jarvis-turn",
})
```

2. **Flutter app**, `appapi.py:236-238`:
```python
# before
cfg = thread_config(rt.user.id)
result = await rt.graph.ainvoke(graph_input, cfg)

# after
cfg = {**thread_config(rt.user.id), "tags": ["app"], "metadata": {"channel": "app"}, "run_name": "jarvis-turn"}
result = await rt.graph.ainvoke(graph_input, cfg)
```
Keep the original `thread_config(...)` calls at `appapi.py:256` and `265`. Those only read state and do not run the graph, so they need no tags.

3. **Voice**, `voice/ws.py`. `self.cfg` is set once at line 64 and used by three `ainvoke` calls (lines 190, 198, 240). The easiest edit is to add the keys once in `__init__`:
```python
self.cfg = {**cfg, "tags": ["voice"], "metadata": {"channel": "voice"}, "run_name": "jarvis-turn"}
```
Line 64 is where `self.cfg = cfg` lives. All three calls, including the two `Command(resume=...)` ones, then pick up the labels. If you add them only to the first call, resumed traces will be untagged.

4. **Background jobs** (`proactive/brief.py:117`, `mailwatch.py:234` and `245`). These call the LLM directly, with no graph, so the config goes as the `config=` keyword:
```python
msg = await llm.get("fast").ainvoke(
    [SystemMessage(SYSTEM), HumanMessage(wrap_untrusted(text))],
    config={"tags": ["proactive"], "run_name": "daily_brief"},
)
```

**Checking it worked.** Send a message, open the trace in LangSmith, and look at the trace detail for your tag and metadata. In the trace list, use **Filter** and choose Tag or Metadata, then pick your value. The metadata dict also shows a `thread_id` entry, because LangGraph adds its own metadata. That is expected.

**Rules of thumb.**
- Keep tags few and stable (`telegram`, `voice`, `app`, `proactive`). Metadata can hold more detail.
- Never put raw personal data (message text, email addresses) in either. Use an internal id such as `rt.user.id`.
- Metadata values should be simple types (string, number, bool).
- Inside one trace, child runs inherit the tags and metadata of their parent, so you only set them once at the top.

### 5.2 `@traceable` for plain functions
**What:** makes any function appear as a step in the trace. **Why:** LangChain calls are traced automatically, but ordinary functions (like memory retrieval) show up as blank gaps. **Where:** `kg/block.py`, `web.py`.

```python
from langsmith import traceable

@traceable(name="kg_retrieve", run_type="retriever")
async def retrieve(...): ...
```
Called inside a graph run, it nests under that trace. Called alone (e.g. from a scheduler job), it becomes its own root trace. Only add this when you notice a gap you care about.

### 5.3 Background jobs
**Why:** `brief.py` and `mailwatch.py` call the LLM directly, outside the graph, and run unattended. Without labels they flood the project and eat your free trace quota.
```python
await llm.get("fast").ainvoke(msgs, config={"run_name": "mail_translate", "tags": ["proactive"]})
```
Then filter them out (or in) with the `proactive` tag.

### 5.4 Datasets and evals (the biggest payoff)
**What:** a saved list of example questions plus expected behavior, and a script that scores the agent on them. **Why:** when you edit a prompt (say `ROUTER_PROMPT`), you can *measure* whether it got better instead of guessing. **How:**

1. In the UI, open a good or bad trace and click **Add to dataset**. Collect 10-20.
2. Write a target and an evaluator:
```python
from langsmith import evaluate

def target(inputs: dict) -> dict:
    return {"answer": run_jarvis(inputs["question"])}   # you write run_jarvis

def correct(run, example) -> dict:
    ok = example.outputs["answer"].lower() in run.outputs["answer"].lower()
    return {"key": "contains_expected", "score": int(ok)}

evaluate(target, data="jarvis-regressions", evaluators=[correct],
         experiment_prefix="router-v2")
```
3. Change the prompt, re-run with a new `experiment_prefix`, and compare the two experiments side by side in the UI.

Start with cheap evaluators (string match, "right tool called", "reply under N chars"). Use an LLM-as-judge only when needed. **Stub the Gmail/Calendar tools in evals** so tests never touch real accounts.

### 5.5 Monitoring
In the project, open the **Monitor** tab for latency, error rate, token usage, and cost charts. Filter by tag to compare channels. Under **Automations** you can create rules, such as "add every errored trace to the `jarvis-regressions` dataset", which feeds 5.4 automatically.

### 5.6 Tool usage observability

**The problem.** Open a trace now and you will see a single `tools` step, not one step per tool. The reason is how Jarvis runs tools. Most LangGraph apps use the built-in `ToolNode`, which LangSmith traces per tool. Jarvis has its own `tools` node in `agent/graph.py` (around line 305) that calls the function directly:

```python
# agent/graph.py, inside tools()
kwargs = tool.args_schema(**c["args"]).model_dump(exclude_unset=True)
result = await asyncio.to_thread(tool.fn, **kwargs)
```
(`registry.lc_tools()` in `tools/registry.py` builds LangChain `StructuredTool` objects, but only to describe tools to the model. They are never executed.) LangSmith can only see what runs through LangChain, so tool execution is invisible inside the `tools` step.

**The fix.** Wrap that one call in a LangSmith span. Because every tool goes through this single line, one edit covers all of them (Gmail, calendar, notes, phone, research...).

1. Add the import at the top of `agent/graph.py`:
```python
from langsmith import trace
```
2. Replace the `try:` body so the call runs inside a span. `trace` gives the span a name, a type, inputs and metadata:
```python
# before
kwargs = tool.args_schema(**c["args"]).model_dump(exclude_unset=True)
result = await asyncio.to_thread(tool.fn, **kwargs)

# after
kwargs = tool.args_schema(**c["args"]).model_dump(exclude_unset=True)
async with trace(
    name=c["name"],                       # span title = the tool name, e.g. "create_event"
    run_type="tool",                      # makes the UI show it as a tool call
    inputs=kwargs,                        # what the tool was called with
    tags=["tool", tool.domain],           # filter by domain: gmail, calendar, phone...
    metadata={"confirmation": label},     # approved / not_required
) as span:
    result = await asyncio.to_thread(tool.fn, **kwargs)
    span.add_outputs({"redacted": True} if tool.untrusted else {"result": result})
    span.add_metadata({"is_error": isinstance(result, dict) and "error" in result})
```
The span nests under the `tools` node automatically. `asyncio.to_thread` copies the current context, so the parent link survives.

**Three details that matter:**
- **Untrusted output must not be uploaded.** Tools flagged `untrusted=True` return email or web text. Your audit log already redacts these (see `audit_result` a few lines below). The `{"redacted": True}` line above does the same for LangSmith. Without it, raw email bodies would go to LangSmith. Check `inputs=kwargs` for the same concern: any tool whose arguments contain private text (e.g. an email body being sent) should get redacted inputs too.
- **Errors do not raise.** Jarvis catches exceptions and returns `{"error": ...}`, so the span would look green even for failures. The `is_error` metadata flag is how you find them. Filter by `metadata is_error = true`.
- **Skipped calls get no span.** Calls that are blocked, cancelled or over a research limit never reach this line, so they produce no tool span. They are still recorded in your own audit log through `record(...)`. LangSmith shows what ran, and the audit log shows what was decided.

**What you get in LangSmith.**
- Each trace now shows the tool calls as children of the `tools` step, with inputs, outputs and duration.
- Filter by tag `tool` for all tool spans, or by `gmail`, `calendar` and so on.
- In the Monitor tab, group by run name to see which tools are slowest or fail most.
- A bad tool call can be saved to a dataset (5.4) to test whether the model picks the right tool and arguments.

**How this relates to your existing audit log.** `record("tool", ...)` already writes tool name, args, result, confirmation and latency to your own database. LangSmith does not replace it. The audit log is your permanent, private record. LangSmith is the visual debugging view of one request: why the model picked the tool, what came back, and what it did next.

**The decision step is also worth a look.** The `gate` node (confirmation prompts) and the `interrupt()` pause already show up as graph nodes in the trace. Add the tag `needs_confirm` to the metadata if you want to filter "traces where the user was asked to approve something".

## 6. Privacy and cost (read before production)

- **Privacy:** tracing uploads full prompts and outputs to LangSmith's servers. Jarvis handles emails, calendar entries, and personal memories. Fine for dev. For prod, either sample, or set `LANGSMITH_HIDE_INPUTS=true` and `LANGSMITH_HIDE_OUTPUTS=true` (coarse, but safe).
- **Existing callbacks:** `AuditCallback` (`llm.py`) and `WatchedLLM` (`proactive/scheduler.py`) are separate LangChain callbacks. They run alongside tracing without conflict; read them first so you don't duplicate what they already record.
- **Cost:** the free tier has a monthly trace cap. Scheduled jobs burn it fastest, so tag them (5.3) and consider turning tracing off for them.

## 7. Suggested learning order

1. Get the first trace showing (section 4).
2. Add tags (5.1) and label background jobs (5.3).
3. Watch the Monitor tab for a day (5.5).
4. Save bad traces into a dataset and write your first eval (5.4).
5. Add `@traceable` only when a trace has a gap you want filled (5.2).

## 8. Links

- Docs: https://docs.smith.langchain.com
- Tracing a LangGraph app: search "LangSmith LangGraph tracing" in the docs
- Evaluation quickstart: search "LangSmith evaluation quickstart" in the docs
