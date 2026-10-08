# Jarvis Dashboard and Dark-Glass Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Pixel app opens to a dark, glassy home dashboard (brief, today's calendar, pending tasks, unread mail, notes, next alarm) with the voice controls in a pinned bottom bar.

**Architecture:** A new read-only `GET /dashboard` endpoint (`src/jarvis/dashboard.py`) gathers the sources concurrently, each failing alone, and is authorised with the same device token as `/voice`. The Flutter app adds a theme, a dashboard model/controller/fetcher, card widgets and a Kotlin next-alarm channel; `SessionScreen` becomes the home with a `VoiceBar` at the bottom.

**Tech Stack:** FastAPI, existing Google clients, pytest (asyncio auto mode); Flutter/Dart (no new packages, `dart:io` HttpClient), Kotlin `AlarmManager`.

**Spec:** `docs/superpowers/specs/2026-10-08-dashboard-design.md`

## Global Constraints

- Read-only dashboard. No editing or completing items from the app.
- Auth: `Authorization: Bearer <device token>` verified with `Devices.verify`, same as `/voice`. Bad or missing token is 401.
- `/dashboard` returns 200 unless auth fails (401) or the service is not built yet (503). A failing source is `null` in the JSON, never a 500.
- No LLM call in `/dashboard`.
- No new Flutter packages and no `pubspec.yaml` change. Android/Pixel only. Dark theme only.
- Visual work (theme, cards, voice bar) MUST use the `frontend-design` skill, inside the spec's direction: near-black navy gradient, translucent rounded cards with hairline border, one accent hue per card, Material 3 dark, no infinite animations.
- Existing behaviour stays: confirm card text and third-party warning (`after reading third-party content (email or web)`), Confirm/Cancel buttons, info icon opening the capabilities screen, `Phase` labels, `AppHost` wiring.
- **The owner is fixing the wake word in parallel. Do NOT touch** `jarvis_app/lib/wake.dart`, `jarvis_app/lib/main.dart`, `jarvis_app/assets/`, `jarvis_app/pubspec.yaml`, `jarvis_app/pubspec.lock`, `JarvisRecognitionService.kt`, `JarvisSessionService.kt`, `BootReceiver.kt`, `.gitignore`.
- Workspace: a git worktree off `main` (superpowers:using-git-worktrees). Python: the editable install points at the main checkout, so in the worktree run tests as `PYTHONPATH=src /Users/ronalisenapati/Ronali/jarvis/.venv/bin/python -m pytest ...` (written `$PY -m pytest` below) and first confirm `PYTHONPATH=src $PY -c "import jarvis; print(jarvis.__file__)"` prints a path inside the worktree. Flutter commands run in `jarvis_app/` (run `flutter pub get` once there).
- Spec refinements made by this plan (apply in Task 1): notes show title and update time only (the store's `recent()` has no body preview); unread fetches 6 messages, shows 5, `more` is true when a 6th exists.

## Review Focus

- Gmail message with missing `From`/`Subject` header (`header()` returns None): payload gives `""`, app shows "Unknown sender". Pinned in Task 1 and Task 4.
- Event with null/empty title, all-day event (date only, no time): "(no title)", "All day". Task 1 and Task 6.
- A Google call that hangs: endpoint still answers within the source timeout with that source `null`. Task 1.
- Malformed or partial JSON from the server (wrong types, missing sections, bad items): app parses what it can, never throws into the UI. Task 4.
- Small phone (360x640) with every section, long subject lines and an open confirm card: no overflow exception, voice bar stays reachable. Task 7.

---

### Task 1: Backend dashboard service

**Files:**
- Create: `src/jarvis/dashboard.py`
- Create: `tests/test_dashboard.py`
- Modify: `src/jarvis/proactive/brief.py` (`render_facts` gets `mail_label`)
- Modify: `tests/test_proactive_brief.py` (one test)
- Modify: `docs/superpowers/specs/2026-10-08-dashboard-design.md` (two refinement lines)

**Interfaces:**
- Consumes: `CalendarClient.list_events(start, end)`, `TasksClient.list_tasks()`, `GmailClient.search_emails(query, limit)`, `NoteStore.recent(limit)`, `Devices.verify(token) -> int | None`, `render_facts(facts, tz)`, `MAX_SPEAK_CHARS`, `ReauthRequired`, `now_local`.
- Produces: `DashboardService(devices, calendar, tasks, gmail, notes, tz, source_timeout=12.0)` with `async authorized(authorization: str) -> bool` and `async payload(now: datetime | None = None) -> dict`. `render_facts(facts, tz, mail_label="Important unread email")`.
  Payload shape:
  `{"events": [{"summary","start","end","location","all_day"}] | None, "tasks": [{"title","due","overdue"}] | None, "unread": {"count","more","items":[{"from","subject"}]} | None, "notes": [{"id","title","updated_at"}] | None, "brief": str, "reauth": bool}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard.py`:

```python
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.dashboard import DashboardService
from jarvis.google.auth import ReauthRequired

TZ = "Europe/Berlin"
NOW = datetime(2026, 10, 5, 14, 0, tzinfo=ZoneInfo(TZ))
EVENTS = [
    {"id": "1", "summary": "Standup", "start": "2026-10-05T09:00:00+02:00", "end": "2026-10-05T09:15:00+02:00", "location": "Office"},
    {"id": "2", "summary": None, "start": "2026-10-05", "end": "2026-10-06", "location": None},
]
TASKS = [
    {"id": "t3", "title": "No date", "due": None, "status": "needsAction"},
    {"id": "t2", "title": "Due today", "due": "2026-10-05", "status": "needsAction"},
    {"id": "t1", "title": "Pay rent", "due": "2026-10-03", "status": "needsAction"},
]
MAIL = [{"id": f"m{i}", "from": f"p{i}@x.com", "subject": f"S{i}"} for i in range(7)]
NOTES = [{"id": 1, "title": "Gym plan", "updated_at": "2026-10-05T08:00:00+00:00"}]


class Cal:
    def __init__(self, events=EVENTS, exc=None, delay=0.0):
        self.events, self.exc, self.delay, self.window = events, exc, delay, None

    def list_events(self, start, end, query=None, limit=50):
        self.window = (start, end)
        if self.delay:
            time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.events


class Tasks:
    def __init__(self, tasks=TASKS):
        self.tasks = tasks

    def list_tasks(self, include_completed=False, tasklist="@default"):
        return self.tasks


class Gmail:
    def __init__(self, mail=MAIL, exc=None):
        self.mail, self.exc, self.call = mail, exc, None

    def search_emails(self, query, limit=10):
        self.call = (query, limit)
        if self.exc:
            raise self.exc
        return self.mail[:limit]


class Notes:
    def recent(self, limit=20):
        return NOTES[:limit]


class Devices:
    def verify(self, token):
        return 1 if token == "good" else None


def svc(cal=None, tasks=None, gmail=None, **kw):
    return DashboardService(Devices(), cal or Cal(), tasks or Tasks(), gmail or Gmail(), Notes(), TZ, **kw)


async def test_payload_shapes_every_section():
    cal, gmail = Cal(), Gmail()
    p = await svc(cal, gmail=gmail).payload(NOW)
    assert p["events"] == [
        {"summary": "Standup", "start": "2026-10-05T09:00:00+02:00", "end": "2026-10-05T09:15:00+02:00", "location": "Office", "all_day": False},
        {"summary": "(no title)", "start": "2026-10-05", "end": "2026-10-06", "location": None, "all_day": True},
    ]
    start, end = cal.window
    assert (start.hour, start.minute) == (0, 0) and (end - start).days == 1 and start.date() == NOW.date()
    assert [(t["title"], t["overdue"]) for t in p["tasks"]] == [("Pay rent", True), ("Due today", False), ("No date", False)]
    assert p["unread"] == {"count": 5, "more": True, "items": [{"from": f"p{i}@x.com", "subject": f"S{i}"} for i in range(5)]}
    assert gmail.call == ("is:unread in:inbox", 6)
    assert p["notes"] == NOTES and p["reauth"] is False
    assert "09:00 Standup" in p["brief"] and "Overdue tasks: Pay rent." in p["brief"] and "Unread email:" in p["brief"]


async def test_unread_without_more_and_missing_headers():
    p = await svc(gmail=Gmail([{"id": "m", "from": None, "subject": None}])).payload(NOW)
    assert p["unread"] == {"count": 1, "more": False, "items": [{"from": "", "subject": ""}]}


async def test_empty_everything():
    p = await svc(Cal([]), Tasks([]), Gmail([])).payload(NOW)
    assert p["events"] == [] and p["tasks"] == [] and p["unread"] == {"count": 0, "more": False, "items": []}
    assert "no events" in p["brief"]


async def test_a_failing_source_is_null_and_the_rest_survive():
    p = await svc(Cal(exc=RuntimeError("boom"))).payload(NOW)
    assert p["events"] is None and p["tasks"] and p["unread"] and p["notes"]
    assert "Calendar: unavailable" in p["brief"] and p["reauth"] is False


async def test_reauth_sets_flag_and_nulls_the_source():
    p = await svc(gmail=Gmail(exc=ReauthRequired("x"))).payload(NOW)
    assert p["unread"] is None and p["reauth"] is True and p["events"]


async def test_a_hung_source_times_out_to_null():
    p = await svc(Cal(delay=0.5), source_timeout=0.05).payload(NOW)
    assert p["events"] is None and p["tasks"]


async def test_authorized_needs_a_known_bearer_token():
    s = svc()
    assert await s.authorized("Bearer good") is True
    assert await s.authorized("bearer good") is True
    for bad in ("Bearer nope", "Bearer ", "good", ""):
        assert await s.authorized(bad) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/test_dashboard.py -q`
Expected: FAIL / collection error `ModuleNotFoundError: No module named 'jarvis.dashboard'`.

- [ ] **Step 3: Add the `mail_label` parameter to `render_facts`**

In `src/jarvis/proactive/brief.py` change the signature and the three mail lines:

```python
def render_facts(facts: dict, tz: str, mail_label: str = "Important unread email") -> str:
```
and replace the three literals `"Important unread email: unavailable."`, `"Important unread email: " + ...`, `"Important unread email: none."` with `f"{mail_label}: unavailable."`, `f"{mail_label}: " + ...`, `f"{mail_label}: none."` (keep the surrounding logic unchanged).

Append to `tests/test_proactive_brief.py`:

```python
def test_render_facts_mail_label_is_overridable():
    text = render_facts({"events": [], "overdue": [], "mail": MAIL}, TZ, mail_label="Unread email")
    assert "Unread email: boss@corp.com: Contract" in text and "Important" not in text
```

- [ ] **Step 4: Write `src/jarvis/dashboard.py`**

```python
import asyncio
import logging
from datetime import datetime, timedelta

from jarvis.google.auth import ReauthRequired
from jarvis.proactive.brief import render_facts
from jarvis.timeutil import now_local
from jarvis.voice.protocol import MAX_SPEAK_CHARS

log = logging.getLogger(__name__)
MAX_TASKS = 20
UNREAD_SHOWN = 5
NOTES_SHOWN = 5


class DashboardService:
    """Read-only snapshot for the app home screen. Every source fails alone (None) so one outage never blanks the screen."""

    def __init__(self, devices, calendar, tasks, gmail, notes, tz: str, source_timeout: float = 12.0):
        self.devices, self.calendar, self.tasks, self.gmail, self.notes = devices, calendar, tasks, gmail, notes
        self.tz, self.source_timeout = tz, source_timeout

    async def authorized(self, authorization: str) -> bool:
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        return bool(token) and await asyncio.to_thread(self.devices.verify, token) is not None

    async def payload(self, now: datetime | None = None) -> dict:
        now = now or now_local(self.tz)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        reauth = False

        async def get(fn, *args):
            nonlocal reauth
            try:
                # ponytail: a timed-out call keeps its worker thread until Google returns; fine for one owner
                return await asyncio.wait_for(asyncio.to_thread(fn, *args), self.source_timeout)
            except ReauthRequired:
                reauth = True
            except Exception:
                log.exception("dashboard source failed")
            return None

        events, all_tasks, mail, notes = await asyncio.gather(
            get(self.calendar.list_events, day, day + timedelta(days=1)),
            get(self.tasks.list_tasks),
            get(self.gmail.search_emails, "is:unread in:inbox", UNREAD_SHOWN + 1),
            get(self.notes.recent, NOTES_SHOWN))

        today = now.date().isoformat()
        overdue = None if all_tasks is None else [t for t in all_tasks if t["due"] and t["due"] < today]
        shown = None if mail is None else mail[:UNREAD_SHOWN]
        brief = render_facts({"events": events, "overdue": overdue, "mail": shown}, self.tz, mail_label="Unread email")
        return {
            "events": None if events is None else [
                {"summary": e.get("summary") or "(no title)", "start": e["start"], "end": e["end"],
                 "location": e.get("location"), "all_day": len(e["start"] or "") == 10} for e in events],
            "tasks": None if all_tasks is None else [
                {"title": t["title"], "due": t["due"], "overdue": bool(t["due"] and t["due"] < today)}
                for t in sorted(all_tasks, key=lambda t: (t["due"] is None, t["due"] or ""))][:MAX_TASKS],
            "unread": None if mail is None else {
                "count": len(shown), "more": len(mail) > UNREAD_SHOWN,
                "items": [{"from": m.get("from") or "", "subject": m.get("subject") or ""} for m in shown]},
            "notes": notes,
            "brief": brief[:MAX_SPEAK_CHARS],
            "reauth": reauth,
        }
```

- [ ] **Step 5: Update the spec's two lines**

In `docs/superpowers/specs/2026-10-08-dashboard-design.md` replace the `unread` bullet's last sentence with: "`count` is at most 5 and `more` is true when a 6th unread message exists (the app shows "5+")." and the `notes` bullet with: "`notes`: 5 most recent (`NoteStore.recent`): id, title, updated_at (no body preview)."

- [ ] **Step 6: Run to verify it passes**

Run: `$PY -m pytest tests/test_dashboard.py tests/test_proactive_brief.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/jarvis/dashboard.py src/jarvis/proactive/brief.py tests/test_dashboard.py tests/test_proactive_brief.py docs/superpowers/specs/2026-10-08-dashboard-design.md
git commit -m "feat(dashboard): read-only dashboard service with per-source failure and timeout

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `/dashboard` route and lifespan wiring

**Files:**
- Modify: `src/jarvis/main.py`
- Modify: `tests/test_main.py`

**Interfaces:**
- Consumes: `DashboardService.authorized(str) -> bool`, `DashboardService.payload() -> dict` (Task 1).
- Produces: `GET /dashboard` (503 without service, 401 bad token, 200 JSON payload); `app.state.dashboard`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_main.py`:

```python
class _Dash:
    def __init__(self, ok=True):
        self.ok, self.seen = ok, None

    async def authorized(self, header):
        self.seen = header
        return self.ok

    async def payload(self):
        return {"events": [], "reauth": False}


def test_dashboard_is_503_until_the_service_is_built():
    assert TestClient(create_app(with_lifespan=False)).get("/dashboard").status_code == 503


def test_dashboard_rejects_a_bad_token():
    app = create_app(with_lifespan=False)
    app.state.dashboard = _Dash(ok=False)
    assert TestClient(app).get("/dashboard", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert TestClient(app).get("/dashboard").status_code == 401


def test_dashboard_returns_the_payload_for_a_paired_device():
    app = create_app(with_lifespan=False)
    dash = app.state.dashboard = _Dash()
    r = TestClient(app).get("/dashboard", headers={"Authorization": "Bearer good"})
    assert r.status_code == 200 and r.json() == {"events": [], "reauth": False}
    assert dash.seen == "Bearer good"
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/test_main.py -q -k dashboard`
Expected: FAIL (404 instead of 503).

- [ ] **Step 3: Implement**

In `src/jarvis/main.py`: change the fastapi import to `from fastapi import FastAPI, Request, WebSocket`, add `from fastapi.responses import JSONResponse` and `from jarvis.dashboard import DashboardService`.

In `lifespan`, directly after `app.state.voice = voice` add:

```python
            app.state.dashboard = DashboardService(devices, calendar, tasks_client, gmail, NoteStore(pool), s.timezone)
```
and in the `finally` teardown directly after `app.state.voice = None  # ...` line add `app.state.dashboard = None`.

In `create_app`, after the `/health` route add:

```python
    @app.get("/dashboard")
    async def dashboard(request: Request):
        svc = getattr(app.state, "dashboard", None)
        if svc is None:  # lifespan has not built it yet
            return JSONResponse({"error": "unavailable"}, status_code=503)
        if not await svc.authorized(request.headers.get("authorization", "")):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await svc.payload()
```

- [ ] **Step 4: Run to verify it passes**

Run: `$PY -m pytest tests/test_main.py tests/test_dashboard.py -q`
Expected: PASS (including the existing lifespan tests; if one fails because it counts `app.state` attributes, fix the test fixture, not the route).

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/main.py tests/test_main.py
git commit -m "feat(dashboard): GET /dashboard behind the device token

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Dark glass theme

**Files:**
- Create: `jarvis_app/lib/theme.dart`
- Create: `jarvis_app/test/theme_test.dart`

**Interfaces:**
- Produces: `jarvisTheme() -> ThemeData`; accent constants `accentBrief, accentCal, accentTasks, accentMail, accentNotes, accentAlarm` (`Color`); `GlassBackground({required Widget child})`; `GlassCard({required String title, required IconData icon, required Color accent, required Widget child, Widget? trailing})`. Muted-text colour `mutedText`.

Invoke the `frontend-design` skill before writing `theme.dart`; the code below is the functional baseline and the minimum API, refine the look within the spec's direction.

- [ ] **Step 1: Write the failing test**

`jarvis_app/test/theme_test.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/theme.dart';

void main() {
  test('theme is dark Material 3', () {
    final t = jarvisTheme();
    expect(t.brightness, Brightness.dark);
    expect(t.useMaterial3, isTrue);
  });

  testWidgets('GlassCard shows title, trailing and child over the background', (t) async {
    await t.pumpWidget(MaterialApp(
        theme: jarvisTheme(),
        home: const Scaffold(
            body: GlassBackground(
                child: GlassCard(
                    title: 'Tasks', icon: Icons.check, accent: accentTasks, trailing: Text('3'), child: Text('body'))))));
    expect(find.text('Tasks'), findsOneWidget);
    expect(find.text('3'), findsOneWidget);
    expect(find.text('body'), findsOneWidget);
  });
}
```

- [ ] **Step 2: Run to verify it fails**

Run (in `jarvis_app/`): `flutter test test/theme_test.dart`
Expected: FAIL, `Target of URI doesn't exist: 'package:jarvis_app/theme.dart'`.

- [ ] **Step 3: Implement `lib/theme.dart`**

```dart
import 'dart:ui';

import 'package:flutter/material.dart';

const bgTop = Color(0xFF0B1020), bgBottom = Color(0xFF05070D);
const mutedText = Color(0xFF9AA4B8);
const accentBrief = Color(0xFF8B7CFF),
    accentCal = Color(0xFF4FC3F7),
    accentTasks = Color(0xFF7CE0A3),
    accentMail = Color(0xFFFFB86B),
    accentNotes = Color(0xFFF48FB1),
    accentAlarm = Color(0xFFFFD54F);

ThemeData jarvisTheme() => ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorScheme: ColorScheme.fromSeed(seedColor: accentBrief, brightness: Brightness.dark),
      scaffoldBackgroundColor: bgBottom,
      appBarTheme: const AppBarTheme(backgroundColor: Colors.transparent, elevation: 0, scrolledUnderElevation: 0),
    );

/// Gradient backdrop with a soft glow; wrap each screen body in it.
class GlassBackground extends StatelessWidget {
  const GlassBackground({super.key, required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) => DecoratedBox(
        decoration: const BoxDecoration(
            gradient: LinearGradient(begin: Alignment.topCenter, end: Alignment.bottomCenter, colors: [bgTop, bgBottom])),
        child: Stack(children: [
          Positioned(
            top: -120,
            right: -80,
            child: IgnorePointer(
              child: Container(
                width: 320,
                height: 320,
                decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: RadialGradient(colors: [accentBrief.withValues(alpha: 0.25), Colors.transparent])),
              ),
            ),
          ),
          Positioned.fill(child: child),
        ]),
      );
}

/// Translucent rounded card with a hairline border and an accent-coloured header.
class GlassCard extends StatelessWidget {
  const GlassCard(
      {super.key, required this.title, required this.icon, required this.accent, required this.child, this.trailing});
  final String title;
  final IconData icon;
  final Color accent;
  final Widget child;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => ClipRRect(
        borderRadius: BorderRadius.circular(24),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: 0.06),
              borderRadius: BorderRadius.circular(24),
              border: Border.all(color: Colors.white.withValues(alpha: 0.12)),
            ),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Container(
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(shape: BoxShape.circle, color: accent.withValues(alpha: 0.18)),
                  child: Icon(icon, size: 16, color: accent),
                ),
                const SizedBox(width: 10),
                Expanded(child: Text(title, style: Theme.of(context).textTheme.titleSmall?.copyWith(color: accent))),
                ?trailing,
              ]),
              const SizedBox(height: 12),
              child,
            ]),
          ),
        ),
      );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `flutter test test/theme_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis_app/lib/theme.dart jarvis_app/test/theme_test.dart
git commit -m "feat(app): dark glass theme, background and GlassCard

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Dashboard model, fetcher and controller

**Files:**
- Create: `jarvis_app/lib/dashboard.dart`
- Create: `jarvis_app/test/dash_fixture.dart`
- Create: `jarvis_app/test/dashboard_test.dart`
- Modify: `jarvis_app/lib/config.dart`
- Modify: `jarvis_app/test/config_test.dart`

**Interfaces:**
- Consumes: `Config(url, token)` from `config.dart`.
- Produces:
  - `Config.dashboardUri` (`Uri`, `https://host/dashboard`; `http` scheme stays `http`).
  - Models: `EventItem({summary, start (DateTime?), location, allDay})`, `TaskItem({title, due (String?), overdue})`, `MailItem({sender, subject})`, `UnreadMail({count, more, items})`, `NoteItem({title, updatedAt})`, `DashboardData({events, tasks, unread, notes, brief, reauth})` with nullable sections, `DashboardData.fromJson(Map<String, dynamic>)`.
  - `String senderName(String from)`.
  - `Future<DashboardData> fetchDashboard(Config c)` (throws on non-200 / bad body).
  - `DashboardController({required Future<DashboardData> Function() fetch, Future<DateTime?> Function()? alarm})` extends `ChangeNotifier`: `DashboardData? data`, `DateTime? nextAlarm`, `bool offline`, `bool loading`, `Future<void> refresh()` (no-op while loading; keeps last good data on failure; safe after `dispose`).
  - Test fixture `sampleJson`, `sampleDashboard()`.

- [ ] **Step 1: Write fixture and failing tests**

`jarvis_app/test/dash_fixture.dart`:

```dart
import 'package:jarvis_app/dashboard.dart';

const sampleJson = <String, dynamic>{
  'events': [
    {'summary': 'Standup', 'start': '2026-10-05T09:00:00', 'end': '2026-10-05T09:15:00', 'location': 'Office', 'all_day': false},
    {'summary': 'Holiday', 'start': '2026-10-05', 'end': '2026-10-06', 'location': null, 'all_day': true},
  ],
  'tasks': [
    {'title': 'Pay rent', 'due': '2026-10-03', 'overdue': true},
    {'title': 'Buy milk', 'due': null, 'overdue': false},
  ],
  'unread': {
    'count': 5,
    'more': true,
    'items': [
      {'from': 'Boss <boss@corp.com>', 'subject': 'Contract'},
    ],
  },
  'notes': [
    {'id': 1, 'title': 'Gym plan', 'updated_at': '2026-10-05T08:00:00'},
  ],
  'brief': 'Calendar today: 09:00 Standup.',
  'reauth': false,
};

DashboardData sampleDashboard() => DashboardData.fromJson(Map<String, dynamic>.from(sampleJson));
```

`jarvis_app/test/dashboard_test.dart`:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/config.dart';
import 'package:jarvis_app/dashboard.dart';

import 'dash_fixture.dart';

void main() {
  test('fromJson parses every section', () {
    final d = sampleDashboard();
    expect(d.events!.map((e) => e.summary), ['Standup', 'Holiday']);
    expect(d.events![0].start, DateTime(2026, 10, 5, 9));
    expect(d.events![0].allDay, isFalse);
    expect(d.events![1].allDay, isTrue);
    expect(d.tasks!.first.overdue, isTrue);
    expect(d.tasks!.last.due, isNull);
    expect(d.unread!.count, 5);
    expect(d.unread!.more, isTrue);
    expect(d.unread!.items.single.sender, 'Boss');
    expect(d.notes!.single.title, 'Gym plan');
    expect(d.brief, startsWith('Calendar today'));
    expect(d.reauth, isFalse);
  });

  test('null or missing sections stay null, malformed items are skipped, nothing throws', () {
    final d = DashboardData.fromJson({
      'events': null,
      'tasks': [
        {'title': 7},
        'junk',
        {'title': 'ok'},
      ],
      'unread': 'nope',
      'brief': 5,
      'reauth': 'yes',
    });
    expect(d.events, isNull);
    expect(d.tasks!.map((t) => t.title), ['', 'ok']);
    expect(d.unread, isNull);
    expect(d.notes, isNull);
    expect(d.brief, isNull);
    expect(d.reauth, isFalse);
  });

  test('senderName handles display names, bare addresses and empties', () {
    expect(senderName('Boss <boss@corp.com>'), 'Boss');
    expect(senderName('"Ann, B" <a@b.com>'), 'Ann, B');
    expect(senderName('<a@b.com>'), 'a@b.com');
    expect(senderName('a@b.com'), 'a@b.com');
    expect(senderName(''), 'Unknown sender');
  });

  test('Config.dashboardUri follows the url scheme', () {
    expect(const Config('https://x.test/', 't').dashboardUri.toString(), 'https://x.test/dashboard');
    expect(const Config('x.test', 't').dashboardUri.toString(), 'https://x.test/dashboard');
    expect(const Config('http://10.0.2.2:8000', 't').dashboardUri.toString(), 'http://10.0.2.2:8000/dashboard');
  });

  group('DashboardController', () {
    test('refresh stores data and the alarm; failure keeps the last good data and flags offline', () async {
      var fail = false;
      final c = DashboardController(
          fetch: () async => fail ? throw Exception('down') : sampleDashboard(), alarm: () async => DateTime(2026, 10, 6, 6, 30));
      await c.refresh();
      expect(c.data, isNotNull);
      expect(c.nextAlarm, DateTime(2026, 10, 6, 6, 30));
      expect(c.offline, isFalse);
      fail = true;
      await c.refresh();
      expect(c.data, isNotNull);
      expect(c.offline, isTrue);
      expect(c.loading, isFalse);
    });

    test('a throwing alarm channel just means no alarm', () async {
      final c = DashboardController(fetch: () async => sampleDashboard(), alarm: () async => throw Exception('no channel'));
      await c.refresh();
      expect(c.nextAlarm, isNull);
      expect(c.data, isNotNull);
    });

    test('overlapping refresh calls fetch once', () async {
      var calls = 0;
      final c = DashboardController(fetch: () async {
        calls++;
        return sampleDashboard();
      });
      await Future.wait([c.refresh(), c.refresh()]);
      expect(calls, 1);
    });

    test('a refresh finishing after dispose does not throw', () async {
      final c = DashboardController(fetch: () async => sampleDashboard());
      final f = c.refresh();
      c.dispose();
      await f;
    });
  });
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `flutter test test/dashboard_test.dart`
Expected: FAIL, missing `dashboard.dart` / `dashboardUri`.

- [ ] **Step 3: Update `config.dart`**

Replace the body of `Config` after the fields with:

```dart
  Uri _uri(String path, {required bool ws}) {
    var u = url.trim().replaceAll(RegExp(r'/+$'), '');
    if (!u.contains('://')) u = 'https://$u';
    final p = Uri.parse(u);
    final secure = p.scheme != 'http';
    return p.replace(scheme: ws ? (secure ? 'wss' : 'ws') : (secure ? 'https' : 'http'), path: path);
  }

  Uri get voiceUri => _uri('/voice', ws: true);
  Uri get dashboardUri => _uri('/dashboard', ws: false);
```
(delete the old `voiceUri` getter; `config_test.dart` must still pass unchanged, and `dashboardUri` is covered by the new test above.)

- [ ] **Step 4: Write `lib/dashboard.dart`**

```dart
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import 'config.dart';

String? _s(Object? v) => v is String ? v : null;

List<T>? _list<T>(Object? v, T Function(Map<String, dynamic>) f) =>
    v is! List ? null : [for (final e in v) if (e is Map<String, dynamic>) f(e)];

/// "Boss <boss@corp.com>" -> "Boss"; a bare address stays as is.
String senderName(String from) {
  final i = from.indexOf('<');
  var n = (i > 0 ? from.substring(0, i) : from).replaceAll('"', '').trim();
  if (n.isEmpty) n = from.replaceAll(RegExp(r'[<>]'), '').trim();
  return n.isEmpty ? 'Unknown sender' : n;
}

class EventItem {
  const EventItem({required this.summary, this.start, this.location, required this.allDay});
  final String summary;
  final DateTime? start;
  final String? location;
  final bool allDay;

  static EventItem from(Map<String, dynamic> j) {
    final s = _s(j['start']);
    return EventItem(
        summary: _s(j['summary']) ?? '(no title)',
        start: s == null ? null : DateTime.tryParse(s)?.toLocal(),
        location: _s(j['location']),
        allDay: j['all_day'] == true);
  }
}

class TaskItem {
  const TaskItem({required this.title, this.due, required this.overdue});
  final String title;
  final String? due;
  final bool overdue;
  static TaskItem from(Map<String, dynamic> j) =>
      TaskItem(title: _s(j['title']) ?? '', due: _s(j['due']), overdue: j['overdue'] == true);
}

class MailItem {
  const MailItem({required this.sender, required this.subject});
  final String sender, subject;
  static MailItem from(Map<String, dynamic> j) =>
      MailItem(sender: senderName(_s(j['from']) ?? ''), subject: _s(j['subject']) ?? '');
}

class UnreadMail {
  const UnreadMail({required this.count, required this.more, required this.items});
  final int count;
  final bool more;
  final List<MailItem> items;
}

class NoteItem {
  const NoteItem({required this.title, this.updatedAt});
  final String title;
  final DateTime? updatedAt;
  static NoteItem from(Map<String, dynamic> j) {
    final u = _s(j['updated_at']);
    return NoteItem(title: _s(j['title']) ?? '', updatedAt: u == null ? null : DateTime.tryParse(u)?.toLocal());
  }
}

class DashboardData {
  const DashboardData({this.events, this.tasks, this.unread, this.notes, this.brief, this.reauth = false});
  final List<EventItem>? events;
  final List<TaskItem>? tasks;
  final UnreadMail? unread;
  final List<NoteItem>? notes;
  final String? brief;
  final bool reauth;

  factory DashboardData.fromJson(Map<String, dynamic> j) {
    final u = j['unread'];
    return DashboardData(
      events: _list(j['events'], EventItem.from),
      tasks: _list(j['tasks'], TaskItem.from),
      unread: u is Map<String, dynamic>
          ? UnreadMail(
              count: u['count'] is int ? u['count'] as int : 0,
              more: u['more'] == true,
              items: _list(u['items'], MailItem.from) ?? const [])
          : null,
      notes: _list(j['notes'], NoteItem.from),
      brief: _s(j['brief']),
      reauth: j['reauth'] == true,
    );
  }
}

Future<DashboardData> fetchDashboard(Config c) async {
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
  try {
    final req = await client.getUrl(c.dashboardUri);
    req.headers.set('Authorization', 'Bearer ${c.token}');
    final res = await req.close().timeout(const Duration(seconds: 20));
    if (res.statusCode != 200) {
      await res.drain<void>();
      throw HttpException('dashboard HTTP ${res.statusCode}');
    }
    final j = jsonDecode(await res.transform(utf8.decoder).join());
    if (j is! Map<String, dynamic>) throw const FormatException('dashboard body');
    return DashboardData.fromJson(j);
  } finally {
    client.close(force: true);
  }
}

class DashboardController extends ChangeNotifier {
  DashboardController({required this.fetch, this.alarm});
  final Future<DashboardData> Function() fetch;
  final Future<DateTime?> Function()? alarm;

  DashboardData? data; // last good snapshot; kept when a refresh fails
  DateTime? nextAlarm;
  bool offline = false, loading = false;
  bool _disposed = false;

  @override
  void notifyListeners() {
    if (!_disposed) super.notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }

  Future<void> refresh() async {
    if (loading) return;
    loading = true;
    notifyListeners();
    try {
      data = await fetch();
      offline = false;
    } catch (_) {
      offline = true;
    }
    try {
      nextAlarm = await alarm?.call();
    } catch (_) {
      nextAlarm = null;
    }
    loading = false;
    notifyListeners();
  }
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `flutter test test/dashboard_test.dart test/config_test.dart`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add jarvis_app/lib/dashboard.dart jarvis_app/lib/config.dart jarvis_app/test/dash_fixture.dart jarvis_app/test/dashboard_test.dart jarvis_app/test/config_test.dart
git commit -m "feat(app): dashboard model, fetcher and controller

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Next-alarm channel (Dart and Kotlin)

**Files:**
- Create: `jarvis_app/lib/alarm.dart`
- Create: `jarvis_app/test/alarm_test.dart`
- Modify: `jarvis_app/android/app/src/main/kotlin/com/jarvis/jarvis_app/MainActivity.kt`

**Interfaces:**
- Produces: `Future<DateTime?> nextAlarm()` (null when no alarm or on any channel error); MethodChannel `jarvis/alarm`, method `next` returns epoch milliseconds (`Long`) or null.

- [ ] **Step 1: Write the failing test**

`jarvis_app/test/alarm_test.dart`:

```dart
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/alarm.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const ch = MethodChannel('jarvis/alarm');
  void mock(Future<Object?> Function(MethodCall) h) =>
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(ch, h);
  tearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(ch, null));

  test('returns the alarm time from epoch milliseconds', () async {
    final at = DateTime(2026, 10, 6, 6, 30);
    mock((c) async => c.method == 'next' ? at.millisecondsSinceEpoch : null);
    expect(await nextAlarm(), at);
  });

  test('null means no alarm', () async {
    mock((_) async => null);
    expect(await nextAlarm(), isNull);
  });

  test('a channel error means no alarm', () async {
    mock((_) async => throw PlatformException(code: 'x'));
    expect(await nextAlarm(), isNull);
  });
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `flutter test test/alarm_test.dart`
Expected: FAIL, missing `alarm.dart`.

- [ ] **Step 3: Implement**

`jarvis_app/lib/alarm.dart`:

```dart
import 'package:flutter/services.dart';

const _channel = MethodChannel('jarvis/alarm');

/// The phone's next alarm (any app's), or null when none is set or it cannot be read.
Future<DateTime?> nextAlarm() async {
  try {
    final ms = await _channel.invokeMethod<int>('next');
    return ms == null ? null : DateTime.fromMillisecondsSinceEpoch(ms);
  } catch (_) {
    return null;
  }
}
```

In `MainActivity.kt` add imports `import android.app.AlarmManager` and `import android.content.Context`, and at the end of `configureFlutterEngine` (after the existing `channel = ...` block) add:

```kotlin
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "jarvis/alarm").setMethodCallHandler { call, result ->
            if (call.method == "next") {
                val am = getSystemService(Context.ALARM_SERVICE) as AlarmManager
                result.success(am.nextAlarmClock?.triggerTime)
            } else result.notImplemented()
        }
```

- [ ] **Step 4: Run to verify it passes**

Run: `flutter test test/alarm_test.dart`
Expected: PASS. Then optionally compile the Kotlin: `flutter build apk --debug` (skip and say so in the report if the in-flight wake work breaks the build for unrelated reasons).

- [ ] **Step 5: Commit**

```bash
git add jarvis_app/lib/alarm.dart jarvis_app/test/alarm_test.dart jarvis_app/android/app/src/main/kotlin/com/jarvis/jarvis_app/MainActivity.kt
git commit -m "feat(app): next-alarm channel via AlarmManager

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Dashboard cards

**Files:**
- Create: `jarvis_app/lib/dashboard_cards.dart`
- Create: `jarvis_app/test/dashboard_cards_test.dart`

**Interfaces:**
- Consumes: `DashboardController`, `DashboardData` and item types (Task 4), `GlassCard`/accents (Task 3).
- Produces: `DashboardSections({required DashboardController controller, required void Function(String text) onPlayBrief, DateTime Function() now = DateTime.now})` (a non-scrolling `Column`: greeting header, offline chip, re-consent banner, Brief, Calendar, Tasks, Unread email, Notes, Next alarm cards; spinner while the first load runs; "Couldn't reach Jarvis" + `Retry` button when the first load failed); helpers `greeting(DateTime)`, `dateLine(DateTime)`, `hhmm(DateTime)`, `dayLabel(DateTime at, DateTime now)`.
- Exact UI strings the tests pin: `Morning brief`, `Play`, `Calendar`, `All day`, `Tasks`, `Overdue`, `Unread email`, `5+ unread`, `Notes`, `Next alarm`, `No alarm set`, `Unavailable`, `No events today`, `No pending tasks`, `No unread email`, `No notes yet`, `Offline`, `Google needs re-consent`, `Couldn't reach Jarvis`, `Retry`.

Invoke `frontend-design` for the look; keep the strings and structure.

- [ ] **Step 1: Write the failing tests**

`jarvis_app/test/dashboard_cards_test.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_app/dashboard.dart';
import 'package:jarvis_app/dashboard_cards.dart';
import 'package:jarvis_app/theme.dart';

import 'dash_fixture.dart';

final _now = DateTime(2026, 10, 5, 8, 0);

Future<DashboardController> _loaded(WidgetTester t,
    {DashboardData? data, DateTime? alarm, bool fail = false, void Function(String)? onPlay}) async {
  final c = DashboardController(
      fetch: () async => fail ? throw Exception('down') : (data ?? sampleDashboard()), alarm: () async => alarm);
  await c.refresh();
  await t.pumpWidget(MaterialApp(
      theme: jarvisTheme(),
      home: Scaffold(
          body: SingleChildScrollView(
              child: DashboardSections(controller: c, onPlayBrief: onPlay ?? (_) {}, now: () => _now)))));
  return c;
}

void main() {
  test('helpers', () {
    expect(greeting(DateTime(2026, 1, 1, 8)), 'Good morning');
    expect(greeting(DateTime(2026, 1, 1, 13)), 'Good afternoon');
    expect(greeting(DateTime(2026, 1, 1, 20)), 'Good evening');
    expect(dateLine(DateTime(2026, 10, 5)), 'Monday, 5 October');
    expect(hhmm(DateTime(2026, 1, 1, 6, 5)), '06:05');
    expect(dayLabel(DateTime(2026, 10, 5, 23), DateTime(2026, 10, 5, 8)), 'Today');
    expect(dayLabel(DateTime(2026, 10, 6, 6), DateTime(2026, 10, 5, 8)), 'Tomorrow');
    expect(dayLabel(DateTime(2026, 10, 9, 6), DateTime(2026, 10, 5, 8)), 'Friday');
  });

  testWidgets('shows every section with its data', (t) async {
    await _loaded(t, alarm: DateTime(2026, 10, 6, 6, 30));
    expect(find.text('Good morning'), findsOneWidget);
    expect(find.text('Monday, 5 October'), findsOneWidget);
    expect(find.text('Morning brief'), findsOneWidget);
    expect(find.textContaining('09:00 Standup'), findsOneWidget);
    expect(find.text('Standup'), findsOneWidget);
    expect(find.text('09:00'), findsOneWidget);
    expect(find.text('All day'), findsOneWidget);
    expect(find.text('Pay rent'), findsOneWidget);
    expect(find.text('Overdue'), findsOneWidget);
    expect(find.text('5+ unread'), findsOneWidget);
    expect(find.text('Boss'), findsOneWidget);
    expect(find.text('Contract'), findsOneWidget);
    expect(find.text('Gym plan'), findsOneWidget);
    expect(find.text('06:30 · Tomorrow'), findsOneWidget);
    expect(find.text('Offline'), findsNothing);
  });

  testWidgets('a null section says Unavailable and the others still render', (t) async {
    await _loaded(t, data: const DashboardData(tasks: [TaskItem(title: 'Pay rent', overdue: false)]));
    expect(find.text('Pay rent'), findsOneWidget);
    expect(find.text('Unavailable'), findsAtLeastNWidgets(3)); // events, mail, notes (+ brief)
  });

  testWidgets('empty lists and no alarm show friendly text', (t) async {
    await _loaded(t, data: const DashboardData(events: [], tasks: [], unread: UnreadMail(count: 0, more: false, items: []), notes: [], brief: 'x'));
    for (final s in ['No events today', 'No pending tasks', 'No unread email', 'No notes yet', 'No alarm set']) {
      expect(find.text(s), findsOneWidget);
    }
  });

  testWidgets('a long mail subject and event title do not overflow a narrow phone', (t) async {
    t.view.physicalSize = const Size(360 * 3, 640 * 3);
    t.view.devicePixelRatio = 3;
    addTearDown(t.view.reset);
    final long = 'x' * 300;
    await _loaded(t, data: DashboardData.fromJson({
      'events': [{'summary': long, 'start': '2026-10-05T09:00:00', 'location': long}],
      'unread': {'count': 1, 'more': false, 'items': [{'from': long, 'subject': long}]},
      'brief': long,
    }));
    expect(t.takeException(), isNull);
  });

  testWidgets('offline with cached data keeps the cards and shows the chip', (t) async {
    var fail = false;
    final c = DashboardController(fetch: () async => fail ? throw Exception('down') : sampleDashboard());
    await c.refresh();
    await t.pumpWidget(MaterialApp(
        theme: jarvisTheme(),
        home: Scaffold(
            body: SingleChildScrollView(child: DashboardSections(controller: c, onPlayBrief: (_) {}, now: () => _now)))));
    fail = true;
    await c.refresh();
    await t.pump();
    expect(find.text('Offline'), findsOneWidget);
    expect(find.text('Standup'), findsOneWidget);
  });

  testWidgets('first load failing shows Retry, which refetches', (t) async {
    var calls = 0;
    final c = DashboardController(fetch: () async {
      calls++;
      throw Exception('down');
    });
    await c.refresh();
    await t.pumpWidget(MaterialApp(
        theme: jarvisTheme(),
        home: Scaffold(body: DashboardSections(controller: c, onPlayBrief: (_) {}, now: () => _now))));
    expect(find.text("Couldn't reach Jarvis"), findsOneWidget);
    await t.tap(find.text('Retry'));
    await t.pump();
    expect(calls, 2);
  });

  testWidgets('re-consent banner and Play button', (t) async {
    String? played;
    await _loaded(t,
        data: DashboardData.fromJson({...sampleJson, 'reauth': true}), onPlay: (s) => played = s);
    expect(find.textContaining('Google needs re-consent'), findsOneWidget);
    await t.tap(find.text('Play'));
    expect(played, 'Calendar today: 09:00 Standup.');
  });
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `flutter test test/dashboard_cards_test.dart`
Expected: FAIL, missing `dashboard_cards.dart`.

- [ ] **Step 3: Implement `lib/dashboard_cards.dart`**

```dart
import 'package:flutter/material.dart';

import 'dashboard.dart';
import 'theme.dart';

const _weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const _months = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

String greeting(DateTime n) => n.hour < 12 ? 'Good morning' : (n.hour < 18 ? 'Good afternoon' : 'Good evening');
String dateLine(DateTime n) => '${_weekdays[n.weekday - 1]}, ${n.day} ${_months[n.month - 1]}';
String hhmm(DateTime d) => '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';

String dayLabel(DateTime at, DateTime now) {
  final d = DateTime.utc(at.year, at.month, at.day).difference(DateTime.utc(now.year, now.month, now.day)).inDays;
  return d == 0 ? 'Today' : (d == 1 ? 'Tomorrow' : _weekdays[at.weekday - 1]);
}

Widget _muted(BuildContext context, String t) =>
    Text(t, style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: mutedText));

Widget _one(String t, {TextStyle? style, int lines = 1}) =>
    Text(t, maxLines: lines, overflow: TextOverflow.ellipsis, style: style);

class DashboardSections extends StatelessWidget {
  const DashboardSections({super.key, required this.controller, required this.onPlayBrief, this.now = DateTime.now});
  final DashboardController controller;
  final void Function(String text) onPlayBrief;
  final DateTime Function() now;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final d = controller.data, n = now(), th = Theme.of(context).textTheme;
          if (d == null) {
            if (!controller.offline) return const Padding(padding: EdgeInsets.all(48), child: Center(child: CircularProgressIndicator()));
            return Padding(
              padding: const EdgeInsets.all(32),
              child: Column(children: [
                Text("Couldn't reach Jarvis", style: th.titleMedium),
                const SizedBox(height: 12),
                FilledButton.tonal(onPressed: controller.refresh, child: const Text('Retry')),
              ]),
            );
          }
          return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(greeting(n), style: th.headlineMedium),
            _muted(context, dateLine(n)),
            if (controller.offline)
              const Padding(padding: EdgeInsets.only(top: 8), child: Chip(avatar: Icon(Icons.cloud_off, size: 16), label: Text('Offline'))),
            if (d.reauth)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: _muted(context, 'Google needs re-consent — some sections may be missing.'),
              ),
            const SizedBox(height: 16),
            _brief(context, d),
            const SizedBox(height: 12),
            _calendar(context, d.events),
            const SizedBox(height: 12),
            _tasks(context, d.tasks),
            const SizedBox(height: 12),
            _mail(context, d.unread),
            const SizedBox(height: 12),
            _notes(context, d.notes),
            const SizedBox(height: 12),
            _alarm(context, n),
          ]);
        },
      );

  Widget _unavailable(BuildContext context) => _muted(context, 'Unavailable');

  Widget _brief(BuildContext context, DashboardData d) {
    final b = d.brief;
    return GlassCard(
      title: 'Morning brief',
      icon: Icons.wb_sunny_outlined,
      accent: accentBrief,
      trailing: b == null ? null : TextButton.icon(onPressed: () => onPlayBrief(b), icon: const Icon(Icons.play_arrow, size: 18), label: const Text('Play')),
      child: b == null ? _unavailable(context) : _one(b, lines: 6),
    );
  }

  Widget _calendar(BuildContext context, List<EventItem>? events) => GlassCard(
        title: 'Calendar',
        icon: Icons.event,
        accent: accentCal,
        child: events == null
            ? _unavailable(context)
            : events.isEmpty
                ? _muted(context, 'No events today')
                : Column(children: [
                    for (final e in events)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          SizedBox(
                              width: 64,
                              child: Text(e.allDay || e.start == null ? 'All day' : hhmm(e.start!),
                                  style: const TextStyle(fontWeight: FontWeight.w600, color: accentCal))),
                          Expanded(
                              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            _one(e.summary),
                            if (e.location != null) _one(e.location!, style: const TextStyle(color: mutedText, fontSize: 12)),
                          ])),
                        ]),
                      ),
                  ]),
      );

  Widget _tasks(BuildContext context, List<TaskItem>? tasks) => GlassCard(
        title: 'Tasks',
        icon: Icons.check_circle_outline,
        accent: accentTasks,
        trailing: tasks == null || tasks.isEmpty ? null : Text('${tasks.length}', style: const TextStyle(color: accentTasks)),
        child: tasks == null
            ? _unavailable(context)
            : tasks.isEmpty
                ? _muted(context, 'No pending tasks')
                : Column(children: [
                    for (final t in tasks)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Row(children: [
                          Expanded(child: _one(t.title)),
                          if (t.overdue)
                            const Text('Overdue', style: TextStyle(color: Colors.redAccent, fontSize: 12))
                          else if (t.due != null)
                            Text(t.due!, style: const TextStyle(color: mutedText, fontSize: 12)),
                        ]),
                      ),
                  ]),
      );

  Widget _mail(BuildContext context, UnreadMail? m) => GlassCard(
        title: 'Unread email',
        icon: Icons.mail_outline,
        accent: accentMail,
        trailing: m == null || m.count == 0 ? null : Text('${m.count}${m.more ? '+' : ''} unread', style: const TextStyle(color: accentMail)),
        child: m == null
            ? _unavailable(context)
            : m.items.isEmpty
                ? _muted(context, 'No unread email')
                : Column(children: [
                    for (final i in m.items)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          _one(i.sender, style: const TextStyle(fontWeight: FontWeight.w600)),
                          _one(i.subject, style: const TextStyle(color: mutedText)),
                        ]),
                      ),
                  ]),
      );

  Widget _notes(BuildContext context, List<NoteItem>? notes) => GlassCard(
        title: 'Notes',
        icon: Icons.sticky_note_2_outlined,
        accent: accentNotes,
        child: notes == null
            ? _unavailable(context)
            : notes.isEmpty
                ? _muted(context, 'No notes yet')
                : Column(children: [
                    for (final n in notes)
                      Padding(padding: const EdgeInsets.symmetric(vertical: 4), child: Row(children: [Expanded(child: _one(n.title))])),
                  ]),
      );

  Widget _alarm(BuildContext context, DateTime n) {
    final at = controller.nextAlarm;
    return GlassCard(
      title: 'Next alarm',
      icon: Icons.alarm,
      accent: accentAlarm,
      child: at == null ? _muted(context, 'No alarm set') : Text('${hhmm(at)} · ${dayLabel(at, n)}', style: Theme.of(context).textTheme.titleMedium),
    );
  }
}
```

If `Unavailable` count in the "null section" test is off (brief is null there too, plus events, mail, notes = 4), adjust the code, not the assertion (`findsAtLeastNWidgets(3)`).

- [ ] **Step 4: Run to verify it passes**

Run: `flutter test test/dashboard_cards_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis_app/lib/dashboard_cards.dart jarvis_app/test/dashboard_cards_test.dart
git commit -m "feat(app): dashboard cards (brief, calendar, tasks, mail, notes, alarm)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Home screen, voice bar and app wiring

**Files:**
- Modify: `jarvis_app/lib/ui.dart` (`SessionScreen` rewrite; add `VoiceBar`; `PairingScreen` untouched here)
- Modify: `jarvis_app/lib/app.dart`
- Modify: `jarvis_app/test/ui_test.dart`
- Modify: `jarvis_app/test/app_test.dart`

**Interfaces:**
- Consumes: `SessionController` (unchanged), `DashboardController`, `DashboardSections`, `GlassBackground`, `jarvisTheme`, `fetchDashboard`, `nextAlarm`.
- Produces:
  - `SessionScreen({required SessionController controller, DashboardController? dashboard})`. With `dashboard == null` it renders just the header and voice bar (so old call sites and tests keep working).
  - `VoiceBar({required SessionController controller})`: collapsed = orb + phase label; expanded (when `phase` is not idle/offline, or a card or an error is present) also shows transcripts, error, the untrusted warning, summary and Confirm/Cancel. The orb has tooltip `Talk` (idle/offline) or `Stop` and calls `controller.start()` / `controller.stop()`.
  - `JarvisApp({..., Future<DashboardData> Function(Config)? dashboardFetch, Future<DateTime?> Function()? alarm})`.

Invoke `frontend-design` for the bar and orb look.

- [ ] **Step 1: Update/add the failing tests**

Edit `jarvis_app/test/ui_test.dart`: add imports `package:jarvis_app/dashboard.dart` and `dash_fixture.dart`; keep both existing tests unchanged; add:

```dart
  testWidgets('home shows the dashboard above the voice bar and the orb starts a session', (t) async {
    final r = Rig();
    final d = DashboardController(fetch: () async => sampleDashboard());
    await d.refresh();
    await t.pumpWidget(MaterialApp(home: SessionScreen(controller: r.c, dashboard: d)));
    expect(find.text('Standup'), findsOneWidget);
    expect(find.text('Say "Hey Jarvis"'), findsOneWidget);
    await t.tap(find.byTooltip('Talk'));
    await t.pump();
    expect(r.started, 1);
    expect(find.byTooltip('Stop'), findsOneWidget);
    r.c.stop();
    await t.pump();
  });

  testWidgets('Play on the brief card speaks the brief text through the session', (t) async {
    final r = Rig();
    final d = DashboardController(fetch: () async => sampleDashboard());
    await d.refresh();
    await t.pumpWidget(MaterialApp(home: SessionScreen(controller: r.c, dashboard: d)));
    await t.tap(find.text('Play'));
    await t.pump();
    await t.pump();
    expect(r.socket.sent.where((e) => e.$1 == 'speak').single.$2, {'text': 'Calendar today: 09:00 Standup.'});
    r.c.stop();
    await t.pump();
  });

  testWidgets('small phone with all sections, long text and an open confirm card does not overflow', (t) async {
    t.view.physicalSize = const Size(360 * 3, 640 * 3);
    t.view.devicePixelRatio = 3;
    addTearDown(t.view.reset);
    final r = Rig();
    final d = DashboardController(fetch: () async => sampleDashboard(), alarm: () async => DateTime(2026, 10, 6, 6, 30));
    await d.refresh();
    await t.pumpWidget(MaterialApp(home: SessionScreen(controller: r.c, dashboard: d)));
    await r.c.start();
    r.socket.ctrl.add(ConfirmCardEvent(interruptId: 'i', summary: 'Create ${'Gym ' * 80}', tapOnly: false, afterUntrusted: true));
    r.socket.ctrl.add(TranscriptEvent('user', 'word ' * 120, true));
    await t.pump();
    expect(t.takeException(), isNull);
    expect(find.text('Confirm'), findsOneWidget);
    r.c.stop();
    await t.pump();
  });
```

The session start uses the fake `FakeSpeaker`; in the Play test `start(speakText:)` sends `speak` after `hello` once connected, which the pumps above allow (fake connect is immediate). If the second `pump` is not enough, add one more.

Edit `jarvis_app/test/app_test.dart`: add imports `package:jarvis_app/dashboard.dart` and `dash_fixture.dart`; change the third test to pass fakes so no network is touched:

```dart
    await t.pumpWidget(JarvisApp(
        store: const _Store(Config('https://x.test', 'tok')),
        dashboardFetch: (_) async => sampleDashboard(),
        alarm: () async => null));
```
(`JarvisApp` is no longer const there.) Add a fourth test:

```dart
  testWidgets('home loads the dashboard once paired', (t) async {
    await t.pumpWidget(JarvisApp(
        store: const _Store(Config('https://x.test', 'tok')),
        dashboardFetch: (_) async => sampleDashboard(),
        alarm: () async => null));
    await t.pump();
    await t.pump();
    expect(find.text('Standup'), findsOneWidget);
    await t.pumpWidget(const SizedBox());
  });
```

- [ ] **Step 2: Run to verify it fails**

Run: `flutter test test/ui_test.dart test/app_test.dart`
Expected: FAIL (no `dashboard:` parameter / `dashboardFetch`).

- [ ] **Step 3: Rewrite `SessionScreen` and add `VoiceBar` in `lib/ui.dart`**

Add imports `dashboard.dart`, `dashboard_cards.dart`, `theme.dart`. Replace the `SessionScreen` class with:

```dart
class SessionScreen extends StatelessWidget {
  const SessionScreen({super.key, required this.controller, this.dashboard});
  final SessionController controller;
  final DashboardController? dashboard;

  @override
  Widget build(BuildContext context) => Scaffold(
        body: GlassBackground(
          child: SafeArea(
            child: Column(children: [
              Expanded(
                child: RefreshIndicator(
                  onRefresh: () async => dashboard?.refresh(),
                  child: ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsets.fromLTRB(20, 8, 20, 16),
                    children: [
                      Align(
                        alignment: Alignment.centerRight,
                        child: IconButton(
                          icon: const Icon(Icons.info_outline),
                          tooltip: 'What Jarvis can do',
                          onPressed: () => Navigator.of(context)
                              .push(MaterialPageRoute(builder: (_) => const CapabilitiesScreen())),
                        ),
                      ),
                      if (dashboard != null)
                        DashboardSections(
                            controller: dashboard!, onPlayBrief: (text) => controller.start(speakText: text)),
                    ],
                  ),
                ),
              ),
              VoiceBar(controller: controller),
            ]),
          ),
        ),
      );
}

class VoiceBar extends StatelessWidget {
  const VoiceBar({super.key, required this.controller});
  final SessionController controller;

  static const _labels = {
    Phase.idle: 'Say "Hey Jarvis"',
    Phase.connecting: 'Connecting…',
    Phase.listening: 'Listening…',
    Phase.thinking: 'Thinking…',
    Phase.speaking: 'Speaking…',
    Phase.offline: 'Jarvis is offline',
  };

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final c = controller.card;
          final inactive = controller.phase == Phase.idle || controller.phase == Phase.offline;
          final expanded = !inactive || c != null || controller.error != null;
          final glow = switch (controller.phase) {
            Phase.listening => accentCal,
            Phase.thinking => accentBrief,
            Phase.speaking => accentTasks,
            Phase.offline => Colors.redAccent,
            _ => accentBrief,
          };
          return ClipRRect(
            borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 20, sigmaY: 20),
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.fromLTRB(20, 14, 20, 14),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.08),
                  border: Border(top: BorderSide(color: Colors.white.withValues(alpha: 0.14))),
                ),
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  if (expanded)
                    ConstrainedBox(
                      constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.4),
                      child: SingleChildScrollView(
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          if (controller.userText.isNotEmpty) Text(controller.userText),
                          if (controller.jarvisText.isNotEmpty)
                            Text(controller.jarvisText, style: const TextStyle(fontWeight: FontWeight.bold)),
                          if (controller.error != null)
                            Text(controller.error!, style: const TextStyle(color: Colors.redAccent)),
                          if (c != null) ...[
                            const SizedBox(height: 8),
                            if (c.afterUntrusted)
                              const Text('⚠ Proposed after reading third-party content (email or web) — check recipient and text.'),
                            Text(c.summary),
                            const SizedBox(height: 12),
                            Row(children: [
                              FilledButton(onPressed: () => controller.confirm(true), child: const Text('Confirm')),
                              const SizedBox(width: 12),
                              OutlinedButton(onPressed: () => controller.confirm(false), child: const Text('Cancel')),
                            ]),
                          ],
                          const SizedBox(height: 12),
                        ]),
                      ),
                    ),
                  Row(children: [
                    Tooltip(
                      message: inactive ? 'Talk' : 'Stop',
                      child: GestureDetector(
                        onTap: inactive ? () => controller.start() : controller.stop,
                        child: AnimatedContainer(
                          duration: const Duration(milliseconds: 250),
                          width: 60,
                          height: 60,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            gradient: LinearGradient(colors: [glow, glow.withValues(alpha: 0.5)]),
                            boxShadow: [BoxShadow(color: glow.withValues(alpha: inactive ? 0.25 : 0.6), blurRadius: inactive ? 12 : 28)],
                          ),
                          child: Icon(inactive ? Icons.mic : Icons.stop, color: Colors.white),
                        ),
                      ),
                    ),
                    const SizedBox(width: 16),
                    Expanded(child: Text(_labels[controller.phase]!, style: Theme.of(context).textTheme.titleMedium)),
                  ]),
                ]),
              ),
            ),
          );
        },
      );
}
```
Add `import 'dart:ui';` at the top of `ui.dart`. Delete the old `_labels` on `SessionScreen`.

- [ ] **Step 4: Wire `lib/app.dart`**

- Imports: `alarm.dart`, `dashboard.dart`, `theme.dart`.
- `JarvisApp`: add `this.dashboardFetch, this.alarm` to the constructor and `final Future<DashboardData> Function(Config)? dashboardFetch; final Future<DateTime?> Function()? alarm;`.
- `_JarvisAppState`: `class _JarvisAppState extends State<JarvisApp> with WidgetsBindingObserver`, field `DashboardController? _dashboard;`.
- `initState`: first line after `super.initState();` add `WidgetsBinding.instance.addObserver(this);`.
- `dispose`: add `WidgetsBinding.instance.removeObserver(this); _dashboard?.dispose();` before `super.dispose()`.
- Add:
```dart
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _dashboard?.refresh();
  }
```
- `_bind(Config? c)`: before building `_controller`, add
```dart
    _dashboard?.dispose();
    _dashboard = c == null
        ? null
        : (DashboardController(fetch: () => (widget.dashboardFetch ?? fetchDashboard)(c), alarm: widget.alarm ?? nextAlarm)
          ..refresh());
```
  and change `onEnded: widget.onSessionEnded,` to
```dart
            onEnded: () {
              widget.onSessionEnded?.call();
              _dashboard?.refresh(); // a spoken "add task" should show up
            },
```
- `build`: `theme: jarvisTheme(),` and `SessionScreen(controller: _controller!, dashboard: _dashboard)`.

- [ ] **Step 5: Run to verify it passes**

Run: `flutter test` (whole suite) then `flutter analyze`
Expected: all tests PASS (the existing session/wake/push tests untouched), no analyzer errors in files this plan touched. Pre-existing analyzer findings in `wake.dart` are the owner's, ignore.

- [ ] **Step 6: Commit**

```bash
git add jarvis_app/lib/ui.dart jarvis_app/lib/app.dart jarvis_app/test/ui_test.dart jarvis_app/test/app_test.dart
git commit -m "feat(app): dashboard home with pinned glass voice bar

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Theme the other screens, acceptance rows and handoff

**Files:**
- Modify: `jarvis_app/lib/ui.dart` (`PairingScreen`)
- Modify: `jarvis_app/lib/capabilities.dart`
- Modify: `jarvis_app/test/ui_test.dart` (one assertion test for pairing)
- Modify: `ACCEPTANCE.md`
- Modify: `docs/HANDOFF.md`

**Interfaces:**
- Consumes: `GlassBackground`, `GlassCard` (Task 3). Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `jarvis_app/test/ui_test.dart` (add import `package:jarvis_app/theme.dart` and `package:jarvis_app/config.dart`):

```dart
  testWidgets('pairing screen is on the glass background and still saves', (t) async {
    Config? saved;
    await t.pumpWidget(MaterialApp(theme: jarvisTheme(), home: PairingScreen(onSaved: (c) => saved = c)));
    expect(find.byType(GlassBackground), findsOneWidget);
    await t.enterText(find.byType(TextField).first, ' https://x.test ');
    await t.enterText(find.byType(TextField).last, ' tok ');
    await t.tap(find.text('Pair'));
    expect(saved?.url, 'https://x.test');
    expect(saved?.token, 'tok');
  });
```

- [ ] **Step 2: Run to verify it fails**

Run: `flutter test test/ui_test.dart`
Expected: FAIL, no `GlassBackground` in the pairing screen.

- [ ] **Step 3: Implement**

`PairingScreen.build`: wrap the existing `SafeArea` in `GlassBackground(child: ...)` (the `Scaffold`'s `body`), add `const Text('Pair Jarvis')` styled `headlineMedium` above the fields, keep both `TextField`s and the `Pair` button exactly as they are. `CapabilitiesScreen.build`: wrap the `ListView` in `GlassBackground`, set `extendBodyBehindAppBar: true` on the `Scaffold`, and render each section as a `GlassCard(title: title, icon: Icons.bolt, accent: accentBrief, child: Column(children: [for (final i in items) Align(alignment: Alignment.centerLeft, child: Text(i))]))` with 12 px gaps instead of `ExpansionTile`. Keep the title `What Jarvis can do`, the section titles and item texts (the existing capabilities test asserts `Calendar` and `List events`). Add the imports `theme.dart` (and `config.dart` stays in `ui.dart`).

- [ ] **Step 4: Acceptance rows and handoff**

Append to `ACCEPTANCE.md` (same table format, next numbers):

```
| 66 | Open the app (paired) | Dark glass home: greeting and date, Morning brief, Calendar, Tasks, Unread email, Notes, Next alarm cards, voice bar at the bottom |
| 67 | Compare Calendar / Tasks / Unread email / Notes cards with Google Calendar, Tasks, Gmail and "list my notes" | Same items; unread shows up to 5 with "5+ unread" when more; overdue tasks say Overdue |
| 68 | Say "Hey Jarvis, add task buy milk", let the session end | The Tasks card shows it without manual refresh; pull-down also refreshes |
| 69 | Set a 06:30 alarm in the clock app, reopen the app | Next alarm card says "06:30 · Today/Tomorrow"; with no alarm it says "No alarm set" |
| 70 | Airplane mode, reopen the app | Cards keep the last data and an Offline chip shows; first launch offline shows "Couldn't reach Jarvis" with Retry |
| 71 | Tap Play on the Morning brief card | A session starts and reads the brief text aloud |
| 72 | Ask for something that needs confirmation (e.g. "create an event tomorrow at 10") | The voice bar expands with transcript, the Confirm card and the third-party warning when relevant; Confirm and Cancel still work; bar collapses after the session |
| 73 | Break Google access (revoke the token) then refresh | The affected cards say Unavailable and a re-consent note shows; the rest still load |
```

In `docs/HANDOFF.md` add one bullet after the `Sub-project 6:` bullet in the file-map list: `- Dashboard (post-6): \`src/jarvis/dashboard.py\` (\`GET /dashboard\`, device-token auth, per-source failure -> null, no LLM) feeds the app home (\`jarvis_app/lib/dashboard.dart\`, \`dashboard_cards.dart\`, \`theme.dart\`, \`alarm.dart\` next-alarm channel in \`MainActivity.kt\`). Spec: \`docs/superpowers/specs/2026-10-08-dashboard-design.md\`. Prod Caddy must proxy \`/dashboard\` (add it to \`@public\`). Manual acceptance rows 66-73 not run.`

- [ ] **Step 5: Run to verify it passes**

Run: `flutter test` and `PYTHONPATH=src $PY -m pytest -q`
Expected: all PASS (DB-backed tests skip without `TEST_DATABASE_URL`).

- [ ] **Step 6: Commit**

```bash
git add jarvis_app/lib/ui.dart jarvis_app/lib/capabilities.dart jarvis_app/test/ui_test.dart ACCEPTANCE.md docs/HANDOFF.md
git commit -m "feat(app): theme pairing and capabilities screens; acceptance rows and handoff

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Report to the owner (not code)**

Tell the owner: (a) add `/dashboard` to the `@public path` line in their untracked `Caddyfile` before deploying (this plan deliberately does not edit it); (b) the Kotlin alarm channel and all visuals are only verifiable on the Pixel (rows 66-73); (c) merge conflicts with the wake-word work are unlikely because none of the wake files were touched, but `app.dart` and `ui.dart` changed.
