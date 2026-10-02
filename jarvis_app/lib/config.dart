class Config {
  const Config(this.url, this.token);
  final String url, token;

  Uri get voiceUri {
    var u = url.trim().replaceAll(RegExp(r'/+$'), '');
    if (!u.contains('://')) u = 'https://$u';
    final p = Uri.parse(u);
    return p.replace(scheme: p.scheme == 'http' ? 'ws' : 'wss', path: '/voice');
  }
}
