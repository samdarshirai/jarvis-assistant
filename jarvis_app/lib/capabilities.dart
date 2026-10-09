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

const capabilityIcons = <String, IconData>{
  'Calendar': Icons.calendar_month_rounded,
  'Tasks & shopping': Icons.check_box_rounded,
  'Email': Icons.mail_rounded,
  'Phone': Icons.smartphone_rounded,
  'Memory': Icons.psychology_rounded,
  'Notes': Icons.edit_note_rounded,
  'Research': Icons.travel_explore_rounded,
  'Proactive': Icons.bolt_rounded,
  'Chat': Icons.forum_rounded,
};

class CapabilitiesScreen extends StatelessWidget {
  const CapabilitiesScreen({super.key});

  @override
  Widget build(BuildContext context) => Scaffold(
        extendBodyBehindAppBar: true,
        appBar: AppBar(
          title: Text('What Jarvis can do', style: condensed(26, color: cream)),
          backgroundColor: Colors.transparent,
        ),
        body: CocoaBackground(
          child: ListView(
            padding: EdgeInsets.fromLTRB(16, MediaQuery.paddingOf(context).top + kToolbarHeight + 8, 16, 24),
            children: [
              for (final (title, items) in capabilities) ...[
                _SkillTile(title: title, items: items),
                const SizedBox(height: 10),
              ],
            ],
          ),
        ),
      );
}

class _SkillTile extends StatelessWidget {
  const _SkillTile({required this.title, required this.items});
  final String title;
  final List<String> items;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: darkPill.withValues(alpha: 0.35),
          borderRadius: BorderRadius.circular(24),
          border: Border.all(color: Colors.white.withValues(alpha: 0.06)),
        ),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Container(
            width: 36,
            height: 36,
            decoration: BoxDecoration(color: Colors.white.withValues(alpha: 0.07), borderRadius: BorderRadius.circular(12)),
            child: Icon(capabilityIcons[title] ?? Icons.bolt_rounded, size: 20, color: amber),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: cream)),
              const SizedBox(height: 3),
              Text(items.join(' · '), style: const TextStyle(fontSize: 12.5, height: 1.35, color: mutedText)),
            ]),
          ),
        ]),
      );
}
