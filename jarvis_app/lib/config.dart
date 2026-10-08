class Config {
  const Config(this.url, this.token);
  final String url, token;

  Uri _uri(String path, {required bool ws}) {
    var u = url.trim().replaceAll(RegExp(r'/+$'), '');
    if (!u.contains('://')) u = 'https://$u';
    final p = Uri.parse(u);
    final secure = p.scheme != 'http';
    return p.replace(scheme: ws ? (secure ? 'wss' : 'ws') : (secure ? 'https' : 'http'), path: path);
  }

  Uri get voiceUri => _uri('/voice', ws: true);
  Uri apiUri(String path, [Map<String, String>? query]) => _uri(path, ws: false).replace(queryParameters: query);
  Uri get dashboardUri => _uri('/dashboard', ws: false);
}
