# Call a contact by name (WhatsApp first, phone fallback)

## Intent

The owner wants to say "call Mom" and have the phone place the call. Today Jarvis can only open a pre-filled WhatsApp/SMS message (`compose_message`); it cannot call, and when a name matches several contacts it silently takes the first one with its first number.

## Goals

- "Call <name>" places a call hands-free by voice.
- Always try a WhatsApp voice call first. If that is not possible for the contact, make a normal phone call. The user does not choose the channel; no "on WhatsApp" / "on phone" phrasing needs to work.
- If the name matches more than one contact, Jarvis says how many and tells them apart by the **last 4 digits** of each number ("two Moms: one ending 4521, one ending 8830, which one?"), and the user answers by voice ("the second one", "ending 8830").
- If nothing matches, or the name was probably misheard, Jarvis says it could not find that name and asks the user to say it again or spell it, then searches again with the answer.

## Non-goals

- Video calls. Voice only.
- Calling by number, redial, call history, or answering/ending calls.
- Calling from Telegram (no voice client). The existing "phone action was not run" reply covers it.
- iOS.
- Changing `compose_message` (messaging keeps today's behaviour, including first-match).

## Anti-goals

- Calling the wrong person. When there is real doubt (several different contacts), ask; never guess.
- The whole address book reaching the server. Only the data listed under "What the server may see" may leave the phone.

## Behaviour

1. A new phone tool `call_contact(contact: str, last4: str | None)` queues a `call_contact` client action, like the other phone tools. `contact` is the name as spoken. `last4` is set only on the second attempt, after the user has chosen.
2. The phone resolves the name against its contacts (exact match, then whole words anywhere in the display name, case-insensitive; never part of a word, so "Dan" and "Smith" both match "Dan Smith" but "Dan" does not match "Jordan" and "Smith" does not match "Smitherson") and collects **every** phone number of each matching contact. If `last4` is given, only numbers ending in those digits are considered.
3. Distinct matches are distinct contacts with different numbers. One contact with several numbers (mobile + home) is one match: try its numbers in contact order for the WhatsApp step below, and use the first for the phone fallback. Two entries that normalise to the same number are one match.
4. **Exactly one match:** try WhatsApp, then phone (see Channels).
5. **More than one match:** do not call. Report the matches back to the server as `{name, last4}` pairs. Jarvis asks which one, speaking the last 4 digits. The user's answer leads to a second `call_contact` with `last4` set to the chosen digits (and the same `contact`).
6. **No match** (or contacts permission denied): report back. Jarvis asks for the name again or spelled out; the answer leads to a fresh `call_contact`. If permission was denied, Jarvis says so instead of asking for a respelling.
7. Jarvis must never say the call "was placed" before the phone has reported it. The phone reports `calling_whatsapp`, `calling_phone`, or an error, and Jarvis's confirmation reflects that.

## Channels

- **WhatsApp:** Android has no public "start a WhatsApp call" intent. The mechanism is the call entry WhatsApp adds to a synced contact in the Android contacts database (a data row whose MIME type is `vnd.android.cursor.item/vnd.com.whatsapp.voip.call`); launching an `ACTION_VIEW` on that row's `content://com.android.contacts/data/<id>` URI starts a WhatsApp voice call. "WhatsApp is not possible" means: WhatsApp is not installed, or the contact has no such row, or the launch throws. The builder must verify this on a real device with WhatsApp installed; if this mechanism does not work on the owner's phone, say so in the plan rather than silently degrading to phone-only.
- **Phone:** `ACTION_CALL` if the `CALL_PHONE` permission is granted (requested at first use; the manifest needs it), otherwise `ACTION_DIAL` which opens the dialer with the number filled in. A denied permission is not an error, it is the dialer fallback.
- A WhatsApp call that opens but then fails inside WhatsApp (callee offline, etc.) is out of the app's sight and out of scope.

## What the server may see

For this feature only, when a name is ambiguous, the phone sends the server the matching contacts' display names and last 4 digits of their numbers (the owner approved this explicitly). Nothing else: no full numbers, no contacts that did not match, no unmatched contact list. When there is exactly one match, nothing about contacts is sent beyond the outcome (`calling_whatsapp` / `calling_phone` / error), and the chosen contact's name is already in the user's own spoken request.

Contact names come from the address book and are untrusted text: they must reach the model as data and never be able to instruct it (the codebase already tracks untrusted reads; follow that pattern).

## Architecture notes (mine, for the builder to refine)

- Today the phone's `run()` result is only a local error string; nothing goes back to the server. This feature needs a phone-to-server frame carrying a structured result of a client action (my guess: a new `action_result` frame on the voice websocket), and the server turning an ambiguous / not-found result into a short agent turn so Jarvis asks the question and hears the answer. Where that turn is injected (graph state vs. a synthetic user message) is the builder's call.
- Keep it stateless: the second `call_contact` carries `contact` + `last4`, so the server remembers nothing between the question and the answer beyond normal conversation history.
- Contact matching and number collection live in `jarvis_app/lib/actions.dart` (`pickContact`) and `jarvis_app/lib/phone.dart`, which today use `flutter_contacts` and take only the first number. The new call path needs all numbers, so it needs its own matcher or an extension of `pickContact`; `compose_message` must keep working unchanged.
- Router prompt and `parse_domains` mention phone tools in `src/jarvis/agent/domains.py`; `test_wiring.py` pins tool names and tags. Update both.

## Constraints

- Android only; existing stack (Flutter app, `android_intent_plus`, `flutter_contacts`, Python server with LangGraph).
- New manifest permission: `CALL_PHONE`. Package visibility for WhatsApp is already declared in the manifest queries.
- Build the app with `--dart-define-from-file=jarvis.env.json`.
- Tests: pure matching logic (unit tests, as `pickContact` has), the new tool's queued action, and the server handling of the phone-result frame. Device behaviour (WhatsApp call launch, dialer) is manual acceptance and goes in `ACCEPTANCE.md`.

## Left to the builder

- Frame name and schema for the phone-to-server result.
- How an ambiguous result becomes an agent turn, and the exact wording Jarvis uses.
- How the user's answer ("the second one") is mapped to `last4`: the model reads the candidates it was just given and fills `last4`.
- How many attempts the "say it again / spell it" loop allows before Jarvis gives up (suggest 2).

## Calls settled after the builder check (mine, not the owner's)

- **Build order:** a throwaway device spike for the WhatsApp call row comes first. If it does not work on the owner's phone, the feature ships as phone-call-only and the plan/handoff says so plainly. This matches the owner's rule (WhatsApp if possible, else phone). No accessibility-service automation or manual-tap deep links.
- **WhatsApp variant:** regular WhatsApp (`com.whatsapp`, already in the manifest queries). Business and second profiles are out of scope; the spike confirms the regular app writes the call row.
- **Bad answer to "which one?":** if the chosen `last4` matches nothing or still several contacts, Jarvis re-asks once, then gives up. Same cap of 2 as the not-found loop.
- **Phone never reports back:** after about 5 seconds Jarvis says it asked the phone to call and could not confirm, matching the existing "you asked the phone" wording. It never claims the call was placed.
- **`CALL_PHONE` prompt:** requested at first call, not earlier. Denied or dismissed means the dialer opens instead, so a system dialog never blocks the call.

## Routing (added after the owner asked how intent is classified)

Intent is classified in `src/jarvis/agent/graph.py`: `KEYWORDS`/`keyword_domain` (a single-domain regex hit skips the LLM), otherwise `ROUTER_PROMPT` (a fast model sees the last 6 messages and returns domains). Only the chosen domain's tools load, so `call_contact` is invisible unless the router picks `phone`. Today the `phone` regex is `alarm|timer|navigat|directions` and the router describes phone as "alarms, timers, navigation, texting", so "call Mom" could land in `chat`, and the follow-up "the second one" has no keywords at all.

Required changes:
- Add a call keyword to the `phone` regex, word-bounded so it does not fire on "calendar", "call me later" or similar. Check it does not make an otherwise single-domain request match two domains (e.g. "call the dentist and add it to my calendar" must still route to both).
- Add calling to the router's `phone` description and an example ("call Mom" -> phone).
- When Jarvis has just asked "which one?" or "say the name again" for a call, the user's next reply must route to `phone`, either via the router prompt seeing that question in its 6-message window or by remembering the last domain. The builder picks; add a test that "the second one" and "ending 8830" after a which-one question reach `phone`.
- Update the `phone` domain prompt in `src/jarvis/agent/domains.py` to list `call_contact` and the ask-then-retry behaviour; `test_wiring.py` pins tool names and tags.

## Sub-category: forcing the tool (added after the first device try)

On the first device try the model replied "I asked the phone to call ..." without calling `call_contact` (no tool row in the audit log), so the phone never received an instruction. Classification now has two levels: domain `phone`, then sub-category `call` when the message starts with "call" (optionally after "please", "hey Jarvis", "could you"). For that sub-category the first agent step runs with `tool_choice` set to `call_contact`; the model only fills in `contact`. Not forced: Telegram, messages that do not start with "call" ("set a timer then call mom", "did Mom call"), the "Phone result" follow-up turns, and the user's answer to "which one?" (model-driven; revisit if it skips the tool too).
