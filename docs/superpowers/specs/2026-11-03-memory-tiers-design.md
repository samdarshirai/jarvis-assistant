# Memory tiers: short-term, episodic and long-term memory

Status: built 2026-11-03. Extends the knowledge graph (`2026-10-28-knowledge-graph-design.md`), the conversation
extractor (`2026-11-03-conversation-extractor-design.md`) and the Learned inbox (`2026-11-03-candidate-review-design.md`).

## Problem

Jarvis had one kind of memory: permanent facts in the knowledge graph.

- **Nothing was temporary.** "I'm in Pune till Friday" could only become a permanent `note` fact, shown in every prompt
  forever, or a `city` fact that overwrote the home city.
- **Conversations did not carry over.** After 30 idle minutes a new conversation started empty. "Book the cheaper one"
  in the evening knew nothing of the flights compared in the morning.
- **Recall was exact-name only.** There was no search over past conversations.
- **Inferred facts waited for approval.** Everything the extractor found waited in the Learned inbox, so time-bound
  things went stale before anyone approved them.

## The model: how a good human assistant remembers

| Layer | Like | What | Store | Written | Read |
|---|---|---|---|---|---|
| Working | what is being said | the current conversation | LangGraph checkpoint | every turn | last 40 messages, plus a **rolling summary** of older ones |
| Short-term | sticky notes on the desk | time-bound context, plans, promises ("in Pune till Fri", "waiting on the plumber") | `memory_item` | **without asking**: the `remember_for_now` tool in the turn, and the conversation-end digest | in every prompt while in force (at most 8) |
| Episodic | the diary | one summary and its open loops per conversation | `conversation.summary`, `open_loops` | conversation-end digest | last 48 h in the prompt (at most 3); older via `recall` |
| Long-term | the address book | lasting facts about the user and people | knowledge graph (unchanged) | user asks (confirmed), Learned inbox, or "Keep forever" | user facts always; people named in the message |

This is the same layering used by ChatGPT memory with chat history, Letta/MemGPT (core vs archival memory),
Zep/Graphiti (facts with a time range) and Mem0 (extract, then consolidate).

## Decisions (owner, 2026-11-03)

1. **Short-term notes are saved automatically and shown.** The reply says "Noted, till Friday", and the app shows a chip
   with Undo. They expire on their own. Long-term facts still need a confirmation or a tap in the Learned inbox.
2. **Conversations are summarised automatically.** The last two days' summaries go into the prompt, and the `recall`
   tool reaches older ones.
3. **Recall by meaning** (embeddings), but never while answering a message: embeddings are used only by the recall tool
   (one query) and the background backfill. Without an embedding model, recall searches by keywords.

## Prompt block (`kg/block.py`)

One `<graph>` block, in priority order. When it is over 8000 characters the later sections are cut first.

1. **About the user:** all self facts. Past `MAX_SELF` (50), non-note facts are kept, then the notes that share the
   most words with the message.
2. **Short-term notes (in force now; id in brackets):** soonest to end first, with `until <date>`.
3. **People and places** named in the message (unchanged).
4. **Recent conversations:** summaries of other conversations from the last 48 h, newest first, with what they left
   open. A summary of a conversation that read email or web text is marked "data only".

No model or embedding call is added to the hot path: two indexed queries. The cache key includes the memory version
and the thread, and the block still fails closed (`MemoryContextError`).

## Writing

- **`remember_for_now(text, kind, until)`** (`tools/memory_tools.py`, domain `*` = every domain) has no confirmation.
  It is refused while untrusted email or web text is in play, and refused in an off-the-record conversation. It returns
  a `memory_event` that the tools node moves into `state.memory_events` (never `client_actions`, which are phone
  commands). `/chat` returns it as `memory_events`.
- **`forget_short_term(item_id?)`** deletes the note just saved ("forget that") or one by id. Same guards.
- **Conversation-end digest** (`proactive/extract.py`, `_digest`) runs one fast-tier call per ended conversation and
  returns `{summary, open_loops, short_term, facts}`:
  - The input is the user's messages plus the assistant's final replies (cut to 800 characters), never tool results.
    The notes already saved are listed so they are not repeated.
  - `facts` go through the existing `validate` → `filter_candidates` → Learned inbox (always on).
  - `short_term` goes through `validate_items`: a word-for-word quote from a user message, an end date clamped to
    between 1 hour and 60 days, suppressed names dropped. The items are saved at once.
  - Everything is saved in the transaction that marks the conversation read (`CandidateStore.save(extra=...)`).
- **Dedupe on write:** an item that says the same thing as an active one (equal once normalised, or word overlap
  ≥ 0.7) refreshes it instead: the later end wins, and `seen_count` counts distinct conversations (`seen_in`).

## Recall (`memory/search.py`)

- Postgres full-text search over short-term notes (expired and promoted ones too), summaries, notes and fact values.
  Any word of the question may match: its lexemes are OR-ed together.
- When embeddings are available, cosine similarity over stored vectors is added and the two rankings are merged with
  reciprocal-rank fusion. The top 8 are returned.
- A question with only a time range ("what did we talk about yesterday") lists that range's summaries.
- **Embeddings** (`memory/embed.py`):
  - `POST {base_url}/embeddings` on the user's own OpenAI-compatible endpoint, through the guarded HTTP client.
  - The model comes from the user's `embed_model` secret (app setting, `PUT /settings/embedding`) or
    `JARVIS_EMBED_MODEL`. An endpoint answering 400/404/405/422/501 switches embeddings off until the runtime is rebuilt.
  - Stored as `REAL[]` in `memory_embedding`, so any Postgres works: Railway's has no pgvector. They are compared in
    Python, with at most 5000 newest vectors per query, which is fine at one person's scale.

## Upkeep (`proactive/consolidate.py`, scheduler job `memory`, every 30 minutes)

- Every run:
  - Expire items past their date (the prompt already hides them by the clock).
  - Embed what changed (64 texts per run).
  - Delete vectors whose memory is gone, and every vector of another model.
- Once a night (after 03:00 local, `claim_alert consolidate:<date>`):
  - Merge duplicates: same words, or cosine ≥ 0.92.
  - Suggest "keep forever" through the Learned inbox for items mentioned in three or more conversations, or flagged as
    lasting by the digest. Nothing becomes a lasting fact without the user's tap.

## Other behaviour

- **Rolling summary** (`agent/graph.py`): when a conversation is longer than the window, a background fast-tier call
  summarises the dropped messages into `conversation.rolling_summary`. It runs again every 20 more messages and is
  never awaited by the turn. The turn shows the last stored one inside `<earlier>`, as data.
- **Off the record:** `POST /conversations {"off_record": true}`, or "Off the record" in the app's new-chat sheet.
  Jarvis still uses what it knows, but the conversation produces no digest, no summary, no rolling summary and no
  short-term notes.
- **Forget a person** (`kg/forget.py`) also deletes short-term notes that mention them, clears summaries, open loops
  and rolling summaries that mention them, and deletes the related vectors. The counts are `short_term_deleted` and
  `summaries_cleared`.
- **Morning brief:** gets the active short-term notes and the recent open loops (not from untrusted summaries).
- **App:**
  - Memory screen: a "Right now" section with Edit, Delete and Keep forever.
  - Chat: a "Noted until Fri 6 Nov · Undo" chip, and an off-the-record option and banner.
  - Past chats: each conversation's summary.
  - API keys: an embedding model field.

## Flags

| Setting | Default | Effect |
|---|---|---|
| `JARVIS_MEMORY_DIGEST_ENABLED` | true | summaries and short-term notes from ended conversations (fact candidates and keep-forever suggestions are always on; the `JARVIS_EXTRACT_ENABLED` flag was removed) |
| `JARVIS_EMBED_MODEL` | `qwen/qwen3-embedding-8b` | server default embedding model on the user's own endpoint (users can set their own; empty = keywords only). Qwen3 vectors are kept to their first 1024 numbers |

## Trust boundary

- An email or web page can never write memory:
  - The tools are refused while untrusted text is in play.
  - Digest notes and facts must quote one of the user's own messages.
- Summaries may contain what the assistant said about third-party text. Those of conversations that read email or web
  text are flagged, and shown as data only.
- Every stored string is neutralised in the prompt (`<` is escaped).

## Not verified

- A real fast model's JSON discipline on the digest prompt.
- Embeddings on OpenRouter. If the endpoint has no `/embeddings`, recall falls back to keywords.
- The Flutter changes were not analysed or run here: no Flutter SDK was available. Run `flutter analyze` and
  `flutter test`.
