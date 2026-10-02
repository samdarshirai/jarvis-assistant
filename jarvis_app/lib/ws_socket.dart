import 'dart:typed_data';

import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'config.dart';
import 'protocol.dart';
import 'session.dart';

class WsVoiceSocket implements VoiceSocket {
  WsVoiceSocket._(this._ch);
  final WebSocketChannel _ch;

  static Future<WsVoiceSocket> open(Config c) async {
    final ch = IOWebSocketChannel.connect(c.voiceUri,
        headers: {'Authorization': 'Bearer ${c.token}'}, connectTimeout: const Duration(seconds: 5));
    await ch.ready; // throws when the backend is down or the token is refused
    return WsVoiceSocket._(ch);
  }

  @override
  Stream<ServerEvent> get events =>
      _ch.stream.map(decodeServer).where((e) => e != null).cast<ServerEvent>();
  @override
  void sendAudio(Uint8List pcm) => _ch.sink.add(pcm);
  @override
  void sendJson(String type, [Map<String, dynamic> fields = const {}]) => _ch.sink.add(encode(type, fields));
  @override
  Future<void> close() => _ch.sink.close();
}
