import 'package:flutter/material.dart';

import 'theme.dart';

/// Dark "Needs your OK" card with the action summary and Cancel/Confirm buttons.
class ConfirmCard extends StatelessWidget {
  const ConfirmCard({
    super.key,
    required this.summary,
    required this.afterUntrusted,
    required this.onConfirm,
    required this.onCancel,
  });
  final String summary;
  final bool afterUntrusted;
  final VoidCallback onConfirm, onCancel;

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(color: darkPill, borderRadius: BorderRadius.circular(28)),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Padding(
              padding: EdgeInsets.fromLTRB(4, 0, 4, 10),
              child: Text('Needs your OK', style: TextStyle(fontSize: 12.5, color: Color(0xFF9C8A84)))),
          ConstrainedBox(
            constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.25),
            child: SingleChildScrollView(
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(color: const Color(0xFF1F1917), borderRadius: BorderRadius.circular(20)),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(summary, style: const TextStyle(color: cream, fontSize: 16, fontWeight: FontWeight.w500)),
                  if (afterUntrusted) ...[
                    const SizedBox(height: 10),
                    Container(
                      padding: const EdgeInsets.all(10),
                      decoration: BoxDecoration(
                          color: amber.withValues(alpha: 0.1), borderRadius: BorderRadius.circular(12)),
                      child: const Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Icon(Icons.warning_amber_rounded, size: 16, color: amberLight),
                        SizedBox(width: 8),
                        Expanded(
                            child: Text('Proposed after reading third-party content (email or web) — check recipient and text.',
                                style: TextStyle(color: amberLight, fontSize: 12, height: 1.4))),
                      ]),
                    ),
                  ],
                ]),
              ),
            ),
          ),
          const SizedBox(height: 12),
          Row(children: [
            Expanded(
              child: TextButton(
                style: TextButton.styleFrom(
                    backgroundColor: const Color(0xFF2A2220),
                    foregroundColor: danger,
                    minimumSize: const Size(0, 46),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(23))),
                onPressed: onCancel,
                child: const Text('Cancel'),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: FilledButton(
                style: FilledButton.styleFrom(
                    backgroundColor: amber,
                    foregroundColor: amberInk,
                    minimumSize: const Size(0, 46),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(23))),
                onPressed: onConfirm,
                child: const Text('Confirm'),
              ),
            ),
          ]),
        ]),
      );
}
