# Conversations: one thread per conversation (design)

Date: 2026-11-03. Project 1 of 4 (2: direct-statement fact capture, 3: conversation-end extractor, 4: candidate review screen + badge). Projects 2-4 get their own specs.

## Why

Today every channel (voice, in-app chat, Telegram) shares one permanent LangGraph thread per user (`thread_id(user_id)` = `user:{id}`), never trimmed. Consequences:

- `router()` in `src/jarvis/agent/graph.py` computes `followup = any(AIMessage in state["messages"][:-1])`. After the very first reply ever, `followup` is always true, so the keyword shortcut (`keyword_domain`) never fires and every turn goes through the fast router LLM with the last 40 messages of unrelated history.
- Reported bug: "set an alarm" gets a spoken "setting an alarm" but no alarm. Working theory (unconfirmed until the `DEBUG router` log lines are read from Railway): the router returns `chat` (no tools) because of old context, so the model only talks. If the theory fails, this project is still wanted but the alarm bug needs its own fix.
- Old messages steer new requests; tokens and latency are wasted on irrelevant history.

## Goals

- A conversation is a first-class object. The model sees only the current conversation's messages.
- Voice: one voice session = one conversation, from wake word (websocket connect) until the mic stops listening, for any reason (user stop, `bye`, silence timeout, disconnect).
- Chat: conversations survive; the user can start a new one at any time ("New chat"), see a list of past conversations, and resume one with its full context.
- Chat and Telegram: after 30 minutes without a message, the next message starts a new conversation automatically; the old one stays in history, resumable.
- The keyword shortcut works again on the first message of every conversation.
- Existing history is kept: the current single thread becomes one old conversation.

## Non-goals

- No change to what the domain agents or the router prompt say, except that they receive only the current conversation. (A smaller router window is a separate, optional follow-up.)
- No fact extraction here (project 3), no candidate review UI (project 4).
- No Telegram history browser. Telegram gets the idle rule plus a `/new` command.
- No deletion/archive UI for conversations in v1.
- No cross-conversation search.

## Anti-goals (failure even if it "works")

- Pending confirmation cards (LangGraph interrupts) lost or resumed in the wrong conversation.
- Per-user data deletion (`users.py`) missing the new threads: `DELETE ... WHERE thread_id = %s` matches one exact id today and would silently leave every conversation thread behind.
- Voice, chat and Telegram corrupting each other's state through the shared per-user lock or the shared checkpoint thread.
- Memory/graph facts, notes or calendar no longer reaching the model (those come from the graph block and situation block, not history; they must be unaffected).

## Decisions (the user's)

- Voice session boundary = wake word to mic stop, regardless of cause.
- Chat: 30-minute idle gap starts a new conversation automatically (also when the app is reopened after a long gap); manual "New chat" at any time; a list of old conversations; resuming an old one continues it.
- Voice conversations appear in the same history list and may be resumed in chat.
- Telegram: idle rule + `/new`; no browsing UI.
- Order of work: this project first; it is also the prerequisite for the extractor, which runs when a conversation closes.

## Design

### Data

New table `conversation` (migration alongside the existing schema files):
`id` (uuid or serial), `user_id`, `channel` (`voice` | `chat` | `telegram`), `title` (null until set; first user message trimmed to ~60 chars), `created_at`, `last_at`, `closed_at` (null while open).

LangGraph `thread_id` for a conversation = `user:{user_id}:c:{conversation_id}`. The checkpointer does the isolation; no message filtering code.

Legacy: the existing thread `user:{user_id}` becomes one `conversation` row (channel `chat`, `closed_at` set, title "Earlier conversation") so it is listed and resumable. The first migration run creates it only for users who already have checkpoints.

### Which conversation a turn belongs to

- **Voice:** `VoiceSession` (`src/jarvis/voice/ws.py`) creates a conversation at connect and passes its thread id in the config instead of `thread_config(uid, voice=True)`. It sets `closed_at` on teardown (every exit path). A reconnect is a new conversation.
- **Chat (`appapi.py` `/chat`, `/chat/confirm`):** the server keeps, per user, the active chat conversation id. On each message: if there is none, or `now - last_at > 30 min`, or the active one is a voice conversation that is closed, create a new one; else reuse. Update `last_at` on every message. New endpoints: `POST /conversations` (start new chat; returns the new conversation), `GET /conversations` (list, newest first: id, title, channel, last_at, open/closed), `POST /conversations/{id}/resume` (make it the active chat conversation; returns recent messages for display), `GET /conversations/{id}` (messages for display).
- **Telegram:** same idle rule on its own active conversation; `/new` forces a new one. `TelegramChannel.cfg` is currently fixed at construction (`thread_config(owner_user_id)`); it must be resolved per message.

### Pending confirmations

An interrupt belongs to the thread (conversation) that raised it. Resuming happens only in that conversation. Starting a new conversation while a card is pending leaves the old card pending in the old conversation; the old conversation shows the card again if resumed. A card is never answered from a different conversation. (Today the "owner thread" check in `appapi.py`/`ws.py` compares the interrupt id against the single thread; it must compare against the conversation the card was offered in.)

### Locking

The per-user lock (`tenants.lock(uid)`) shared by Telegram, voice and chat stays per user in v1. Conversations are isolated by thread id, but side effects (Google writes) are still serialised per user.

### Router and agents

No code change in `graph.py` is required for the core fix: with a per-conversation thread, `state["messages"]` is the conversation, so `followup` is false on the first turn and `keyword_domain` fires again. `HISTORY = 40` stays as a cap.

### Flutter app

- Chat screen: a "New chat" action and a history entry point (list of conversations with title, channel icon, relative time). Opening the chat screen shows the active conversation (or an empty new one after the idle gap).
- Resume: tapping a conversation loads its messages and continues it.
- Voice screen: no change, the conversation is implicit.

### Cleanup / safety

- `users.py` deletion and counts (`thread_id(user_id)` exact matches, lines ~110, 172, 232) must cover all `user:{id}:c:%` threads (use `LIKE` on the prefix) and the new `conversation` rows.
- Audit and checkpoints stay as is.

## Constraints

- Postgres checkpointer (`self.saver`) is shared; no change to it.
- Must not log message text or exception text (see `tests/test_voice_robust.py`).
- Multi-user: every query scoped by `user_id`; isolation tests (`tests/test_isolation.py`) must keep passing and cover conversations.

## Left to the builder

- Id type for conversation (uuid vs bigserial), exact endpoint payload shapes, how "active chat conversation" is stored (column on `conversation` vs small table), title generation details, where Telegram resolves its config.
- Whether the 30-minute value is a setting or a constant (constant is fine; one place).

## Verification

- Test: a new conversation after idle gap has an empty message history; the first message "set an alarm for 6" is routed by `keyword_domain` (no router LLM call).
- Test: two conversations for one user do not see each other's messages; a pending interrupt in A is not resumable from B.
- Test: voice connect creates a conversation, disconnect (each path) closes it.
- Test: user data deletion removes all conversation threads.
- Device: say "set an alarm for 6am" by voice; the alarm appears in the Clock app.

## Calls made after the builder check (the author's calls, not the user's: check these)

- **Thread plumbing:** each entry point (voice connect, `/chat`, `/chat/confirm`, Telegram message) resolves its conversation once and builds the graph config with an explicit `conversation_id` (`thread_config(uid, conversation_id, voice=...)`). `thread_config(uid)` without a conversation id is removed, so a missed call site fails loudly instead of using the old thread. The proactive/brief paths and the dashboard do not read conversations and are unchanged.
- **Confirmations:** the client sends `conversation_id` with `/chat` and `/chat/confirm`. The server answers a card only in that conversation and refuses if the card belongs to another.
- **Legacy migration:** `conversation` stores its `thread_id` column. The legacy conversation keeps the old `user:{id}` thread id; no checkpoint rows are renamed. User deletion and counts cover both the stored thread ids and the exact legacy id. It runs after the existing `owner` to `user:{id}` rename, only for users that have rows in the checkpoint table, and skips safely when that table does not exist yet.
- **Resume / display payload:** user and assistant text only, oldest first, plus the pending confirmation card if any (re-offered). Tool calls and results are not shown. An open voice conversation is readable (messages so far) but not resumable until closed.
- **`closed_at` for chat and Telegram:** set lazily when the next conversation for that channel starts, or on "New chat" or `/new`. Consumers (project 3) treat "open and idle for more than 30 minutes" as closed too.
- **Model tier:** chosen by the channel of the turn (a voice turn uses the voice tier), not by the conversation's original channel.
- **Titles:** set from the first user message for every channel, voice included.
- **Telegram:** the owner is the only Telegram user today; the idle rule is for that one chat.

## Decided by the user after the builder check

- Telegram has its own active conversation, separate from in-app chat (own 30-minute rule and `/new`). A Telegram conversation can still be resumed from the app's history list.
- Typing in chat while a voice conversation is open starts a separate chat conversation; it never joins the voice one.
