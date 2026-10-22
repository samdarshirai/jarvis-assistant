import 'package:flutter/services.dart';

class GateState {
  const GateState({required this.charging, required this.androidAuto});
  final bool charging, androidAuto;

  factory GateState._fromMap(Object? m) {
    final map = (m as Map?) ?? const {};
    return GateState(
      charging: map['charging'] == true,
      androidAuto: map['androidAuto'] == true,
    );
  }
}

class JarvisGate {
  static const _method = MethodChannel('jarvis_gate');
  static const _events = EventChannel('jarvis_gate/changes');

  static Future<GateState> state() async =>
      GateState._fromMap(await _method.invokeMethod<Object?>('state'));

  static Stream<GateState> get changes =>
      _events.receiveBroadcastStream().map(GateState._fromMap);
}
