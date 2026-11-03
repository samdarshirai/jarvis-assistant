import 'package:flutter/foundation.dart';

import 'api.dart';

/// How many things Jarvis learned that wait for the user's decision. Drives the count badge on the Memory pill.
/// A failed refresh keeps the last count, so the badge never flickers or lies while offline.
class LearnedController extends ChangeNotifier {
  LearnedController(this._api);
  final ApiClient _api;
  int _count = 0;
  bool _disposed = false;

  int get count => _count;

  Future<void> refresh() async {
    try {
      final j = await _api.get('/graph/candidates/count');
      set(j['enabled'] == false ? 0 : (j['count'] as num?)?.toInt() ?? 0);
    } catch (_) {}
  }

  /// The Learned section knows the exact number after every decision: no extra request.
  void set(int n) {
    if (_disposed || n == _count) return;
    _count = n < 0 ? 0 : n;
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
