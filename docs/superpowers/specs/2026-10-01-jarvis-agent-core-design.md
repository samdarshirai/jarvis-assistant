# Jarvis sub-project 1: agent core, Telegram, Calendar and Tasks

Source: Jarvis PRD (https://claude.ai/artifact/RV9NmxXNr4FJJGE9KZM5Vj). This spec covers the first of five sub-projects.

## Decomposition

1. **Agent core + Telegram + Calendar and Tasks tools, with the confirmation gate (this spec).**
2. Gmail tools.
3. Pixel voice app: wake word, streaming speech.
4. Memory, notes, research.
5. Proactive features: morning brief, alerts.

Each sub-project gets its own spec, plan and build.

## Goal and success criteria

A single-user assistant (Samdarshi) that manages Google Calendar and Google Tasks by Telegram chat. It runs on a laptop first (Telegram long-polling, OAuth on localhost) and moves to a VPS with a webhook later.

Done when:
- Key scenarios 3 and 7 pass, and the calendar and task halves of scenario 1 pass (the alarm half needs the Pixel app, sub-project 3).
- No side-effect action ever runs without an explicit confirmation.
- Every LLM call and tool call is logged with latency, model, tokens, cost.

Covered requirements: FR-1 to FR-6, FR-12, plus the Calendar parts of FR-3. Deferred: FR-7, FR-8, FR-13 and everything outside Calendar and Tasks.

Out of scope: voice, Gmail, memory and notes, proactive jobs, multi-user.

## Architecture

Stack: Python, FastAPI, LangGraph, OpenRouter (via `ChatOpenAI`, base URL `https://openrouter.ai/api/v1`), Postgres 16 (checkpoints, audit log, encrypted tokens), `python-telegram-bot`.

### Agent graph (router + sub-agents)

- **Router node** (fast model tier): classifies each message and returns an ordered list of domains: `calendar`, `tasks`, `chat`. A message can need several domains (scenario 1).
- **Sub-agents**: each is a LangGraph subgraph with its own prompt and only its own tools.
  - Calendar: list/search, create, update, delete, freebusy slot finding (FR-4 to FR-6), including recurring events and single occurrences.
  - Tasks: create, complete, list, reschedule with due dates (FR-12).
  - Chat: no tools; answers anything else.
- **Client actions**: a sub-agent may return client actions (for example `set_alarm(06:00)`) in its result. In this sub-project they are passed back in the reply payload and ignored by Telegram; the Pixel app consumes them in sub-project 3.
- **Session**: keyed by user, not channel (FR-1). Checkpointed to Postgres, so history and a pending confirmation survive restarts.

### Confirmation gate

- Lives in the parent graph, not in sub-agents. Sub-agents can only propose side-effect tool calls.
- A single registry tags tools `needs_confirm`. The gate reads the tag, so a new tool cannot skip confirmation by omission. Untagged tools default to `needs_confirm`.
- On a proposed side-effect call the graph pauses via LangGraph `interrupt()`. The channel shows the action and Confirm/Cancel. Approval resumes the graph and runs the call; cancel drops it.
- Enforced in graph code, not in prompts, so a prompt injection cannot trigger a write.
- Side-effect tools in this sub-project: create, update, delete event; create, complete, reschedule task. Read-only tools: list, search, freebusy, list tasks.

### Telegram layer

- Long-polling inside the FastAPI process. A later webhook is a one-adapter change.
- Every update is checked against the owner chat ID first; anything else is dropped silently and logged.
- Text goes into the graph; replies go back as Telegram messages. Confirmations are inline keyboards; a button tap resumes the graph by thread ID.
- A stale button tap (action already resolved or expired) gets an "already handled" reply.

### Google tools and auth

- One thin wrapper module per API: Calendar and Tasks.
- OAuth: a one-time CLI command runs consent on localhost. Refresh token stored encrypted at rest in Postgres. OAuth app set to "In production" (tokens expire after 7 days in "Testing").
- Scopes: Calendar and Tasks only. Gmail scopes arrive with sub-project 2 and need re-consent.
- Time handling (FR-3): default zone Europe/Berlin. Relative dates ("next Friday", "tomorrow evening") are resolved against the user's current local time, injected into the prompt and verified in code before any API call.
- Second time zone for slot finding (scenario 7: IST 18:00 to 21:00 and before 09:00 CET) is a parameter of the freebusy tool.

### Model use and cost

- Two tiers via OpenRouter with a fallback list. Fast tier for routing and simple commands, strong tier for multi-step reasoning.
- Requests set `data_collection: "deny"` and `require_parameters: true`.
- Budget: under 20 EUR per month for LLM. Router calls and sub-agent calls both count.
- Model choice is settled by evaluating against the key scenarios (PRD open question); this spec does not pick specific models.

### Error handling

- Google API failure: tool returns a structured error; the agent tells the user plainly. Writes are never retried silently.
- LLM failure: walk the fallback list, then reply "can't reach my brain, try again".
- Expired or revoked Google token: Telegram message with re-auth instructions.
- Audit log: every tool call with arguments, result and confirmation status, kept 90 days. Conversation transcripts kept 30 days.

## Testing

- Unit tests for tool wrappers against recorded Google responses.
- Graph tests with a fake LLM, including the gate invariant: a side-effect tool never executes without an approval, across all sub-agents and for any tool added to the registry.
- Router tests: multi-domain messages return domains in order.
- Manual acceptance pass over scenarios 3, 7 and the calendar/task half of 1.

## Open items

- Which OpenRouter models fill the fast and strong tiers (decided by scenario evaluation during build).
- Hosting after laptop phase (VPS vs home server) is decided before the webhook move, not now.
