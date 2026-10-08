import 'package:flutter/material.dart';

import 'theme.dart';

// User-facing list of everything Jarvis can do. Update when adding a tool in src/jarvis/tools/ or a proactive job.
const capabilities = <(String, List<String>)>[
  ('Calendar', ['List events', 'Find free slots', 'Create, update, delete events (asks to confirm)']),
  ('Tasks & shopping', ['List, create, complete, reschedule tasks', 'Add, list, complete shopping items']),
  ('Email', ['Search and read email', 'Find contacts', 'Draft, edit and send email (asks to confirm)']),
  ('Phone', ['Set alarms', 'Set timers', 'Start navigation', 'Compose messages']),
  ('Memory', ['Remember things', 'Forget things', 'Recall what you told me']),
  ('Notes', ['Create, append, delete notes', 'Search, read, list notes']),
  ('Research', ['Search the web', 'Read web pages']),
  ('Proactive', [
    'Morning brief on weekdays',
    'Watches mail and adds calendar events from it, with Undo',
    'Alerts on conflicts and when to leave',
  ]),
  ('Chat', ['General conversation']),
];

class CapabilitiesScreen extends StatelessWidget {
  const CapabilitiesScreen({super.key});

  @override
  Widget build(BuildContext context) => Scaffold(
        extendBodyBehindAppBar: true,
        appBar: AppBar(title: const Text('What Jarvis can do')),
        body: GlassBackground(
          child: ListView(
            padding: EdgeInsets.fromLTRB(16, MediaQuery.paddingOf(context).top + kToolbarHeight + 8, 16, 24),
            children: [
              for (final (title, items) in capabilities) ...[
                GlassCard(
                  title: title,
                  icon: Icons.bolt,
                  accent: accentBrief,
                  child: Column(children: [for (final i in items) Align(alignment: Alignment.centerLeft, child: Text(i))]),
                ),
                const SizedBox(height: 12),
              ],
            ],
          ),
        ),
      );
}
