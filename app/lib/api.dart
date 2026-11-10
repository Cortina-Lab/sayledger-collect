import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Talks to the SayLedger backend only. The phone never holds a Meta key —
/// just this install's book token.
class Api {
  Api._();
  static final Api instance = Api._();

  /// Hosted demo backend. For local development use the "Server" option on the
  /// first screen (e.g. http://127.0.0.1:8078 with `adb reverse tcp:8078 tcp:8078`).
  static const defaultBase = 'https://cortinainfinity.com/sayledger-collect';
  String base = defaultBase;
  String? token;

  /// Every book (shop) this phone can open; one is active at a time.
  List<BookRef> books = [];

  bool get hasBook => token != null && token!.isNotEmpty;
  BookRef? get activeBook => books.where((b) => b.token == token).firstOrNull;

  Future<void> load() async {
    try {
      final p = await SharedPreferences.getInstance();
      base = p.getString('server') ?? defaultBase;
      final raw = p.getString('books');
      books = raw == null ? [] : (jsonDecode(raw) as List).map((m) => BookRef.fromJson(m as Map<String, dynamic>)).toList();
      token = p.getString('book_token');
      // First version stored a single token only.
      if (token != null && activeBook == null) books.add(BookRef(token!, 'My Shop', 'INR'));
      if (token == null && books.isNotEmpty) token = books.first.token;
    } catch (_) {}
  }

  Future<void> setBase(String url) async {
    base = url.trim().replaceAll(RegExp(r'/+$'), '');
    final p = await SharedPreferences.getInstance();
    await p.setString('server', base);
  }

  Future<void> _persist() async {
    final p = await SharedPreferences.getInstance();
    await p.setString('books', jsonEncode([for (final b in books) b.toJson()]));
    if (token == null) {
      await p.remove('book_token');
    } else {
      await p.setString('book_token', token!);
    }
  }

  Future<void> switchTo(String t) async {
    token = t;
    await _persist();
  }

  /// Keep the book list's name/currency in step with the server.
  Future<void> remember(Map<String, dynamic> shop) async {
    final b = activeBook;
    if (b == null || (b.name == shop['name'] && b.currency == shop['currency'])) return;
    b.name = shop['name'] as String;
    b.currency = shop['currency'] as String;
    await _persist();
  }

  Future<void> _dropActive() async {
    books.removeWhere((b) => b.token == token);
    token = books.isEmpty ? null : books.first.token;
    await _persist();
  }

  Map<String, String> get _auth => {if (hasBook) 'Authorization': 'Bearer $token'};

  Future<http.Response> _raw(Future<http.Response> Function() call, {int seconds = 120}) async {
    try {
      return await call().timeout(Duration(seconds: seconds));
    } catch (_) {
      throw ApiException("Can't reach the SayLedger server. Check your connection or Settings → Server.");
    }
  }

  dynamic _decode(http.Response r) {
    final ct = r.headers['content-type'] ?? '';
    final data = r.bodyBytes.isEmpty || !ct.contains('json') ? null : jsonDecode(utf8.decode(r.bodyBytes));
    if (r.statusCode == 401) throw UnlinkedException();
    if (r.statusCode >= 400) {
      final detail = data is Map && data['detail'] is String ? data['detail'] as String : 'Something went wrong (${r.statusCode})';
      throw ApiException(detail);
    }
    return data;
  }

  Future<dynamic> _send(String method, String path, [Object? body]) async {
    final uri = Uri.parse('$base$path');
    final headers = {'content-type': 'application/json', ..._auth};
    final enc = body == null ? null : jsonEncode(body);
    final r = await _raw(() => switch (method) {
          'POST' => http.post(uri, headers: headers, body: enc),
          'PUT' => http.put(uri, headers: headers, body: enc),
          'PATCH' => http.patch(uri, headers: headers, body: enc),
          'DELETE' => http.delete(uri, headers: headers),
          _ => http.get(uri, headers: headers),
        });
    return _decode(r);
  }

  Future<dynamic> _multipart(String path, Map<String, (String, Uint8List, String)> files,
      [Map<String, String> fields = const {}]) async {
    final req = http.MultipartRequest('POST', Uri.parse('$base$path'))..headers.addAll(_auth);
    req.fields.addAll(fields);
    for (final f in files.entries) {
      final (name, bytes, mime) = f.value;
      final parts = mime.split('/');
      req.files.add(http.MultipartFile.fromBytes(f.key, bytes,
          filename: name, contentType: MediaType(parts[0], parts.length > 1 ? parts[1] : 'octet-stream')));
    }
    final r = await _raw(() async => http.Response.fromStream(await req.send()), seconds: 180);
    return _decode(r);
  }

  Future<Uint8List> _bytes(String path) async {
    final r = await _raw(() => http.get(Uri.parse('$base$path'), headers: _auth));
    if (r.statusCode >= 400) _decode(r);
    return r.bodyBytes;
  }

  static Map<String, dynamic> _map(dynamic v) => Map<String, dynamic>.from(v as Map);
  static String _q(Map<String, Object?> params) {
    final kept = {for (final e in params.entries) if (e.value != null) e.key: '${e.value}'};
    return kept.isEmpty ? '' : '?${Uri(queryParameters: kept).query}';
  }

  // ── book ──
  Future<void> createBook(String shopName, String currency) async {
    final r = _map(await _send('POST', '/books', {'shop_name': shopName, 'currency': currency}));
    final shop = _map(r['shop']);
    books.add(BookRef(r['token'] as String, shop['name'] as String, shop['currency'] as String));
    token = r['token'] as String;
    await _persist();
  }

  Future<void> deleteBook() async {
    await _send('DELETE', '/books/me');
    await _dropActive();
  }

  /// The server no longer knows the active token: drop it locally.
  Future<void> forgetBook() => _dropActive();

  Future<Map<String, dynamic>> shop() async {
    final s = _map(await _send('GET', '/shop'));
    await remember(s);
    return s;
  }
  Future<void> saveShop(String name, String currency) => _send('PUT', '/shop', {'name': name, 'currency': currency});
  Future<Map<String, dynamic>> summary() async => _map(await _send('GET', '/summary'));

  // ── entries ──
  Future<Map<String, dynamic>> proposeText(String text) async => _map(await _send('POST', '/text', {'text': text}));
  Future<Map<String, dynamic>> proposeVoice(Uint8List wav) async =>
      _map(await _multipart('/voice', {'audio': ('speech.wav', wav, 'audio/wav')}));
  Future<Map<String, dynamic>> confirm(List<Map<String, dynamic>> proposals, String heard, String source) async =>
      _map(await _send('POST', '/entries/confirm', {'proposals': proposals, 'heard': heard, 'source': source}));
  Future<Map<String, dynamic>> entries({String? start, String? end, String? kind, bool includeVoided = false}) async =>
      _map(await _send('GET', '/entries${_q({'start': start, 'end': end, 'kind': kind, 'include_voided': includeVoided ? 'true' : null})}'));
  Future<Map<String, dynamic>> editEntry(int id, Map<String, dynamic> changes) async =>
      _map(await _send('PATCH', '/entries/$id', changes));
  Future<void> voidEntry(int id, String reason) => _send('POST', '/entries/$id/void', {'reason': reason});
  Future<List<Map<String, dynamic>>> audit({int? entryId}) async =>
      (_map(await _send('GET', '/audit${_q({'entry_id': entryId})}'))['events'] as List).cast<Map<String, dynamic>>();

  // ── customers ──
  Future<Map<String, dynamic>> parties() async => _map(await _send('GET', '/parties'));
  Future<Map<String, dynamic>> party(int id) async => _map(await _send('GET', '/parties/$id'));
  Future<Map<String, dynamic>> editParty(int id, Map<String, dynamic> changes) async =>
      _map(await _send('PATCH', '/parties/$id', changes));
  Future<Map<String, dynamic>> statement(int id, {String? start, String? end}) async =>
      _map(await _send('GET', '/parties/$id/statement${_q({'start': start, 'end': end})}'));

  Future<Map<String, dynamic>> collections() async => _map(await _send('GET', '/collections'));

  // ── reports / files ──
  Future<Map<String, dynamic>> report(String start, String end) async =>
      _map(await _send('GET', '/report${_q({'start': start, 'end': end})}'));
  Future<Uint8List> exportCsv({String? start, String? end}) => _bytes('/export.csv${_q({'start': start, 'end': end})}');
  Future<Uint8List> backup() => _bytes('/backup');
  Future<Map<String, dynamic>> restore(Uint8List file) async =>
      _map(await _multipart('/restore', {'file': ('book.sayledger', file, 'application/octet-stream')}));

  // ── AI ──
  Future<Map<String, dynamic>> askText(String q) async => _map(await _send('POST', '/ask', {'text': q}));
  Future<Map<String, dynamic>> askVoice(Uint8List wav) async =>
      _map(await _multipart('/ask', {'audio': ('question.wav', wav, 'audio/wav')}));
  Future<Map<String, dynamic>> readLedgerPhoto(Uint8List image, String mime) async =>
      _map(await _multipart('/ledger-photo', {'image': ('page.${mime.split('/').last}', image, mime)}));
  Future<Map<String, dynamic>> stockCount(Uint8List image, String mime, String item) async =>
      _map(await _multipart('/stock-count', {'image': ('shelf.${mime.split('/').last}', image, mime)}, {'item': item}));
  Future<Map<String, dynamic>> reminder(int partyId, String language) async =>
      _map(await _send('POST', '/reminder-draft', {'party_id': partyId, 'language': language}));

  // ── Collect (PayPal) — the phone never holds a PayPal secret ──
  static List<Map<String, dynamic>> _list(dynamic v) => (v as List).map((e) => _map(e)).toList();
  Future<Map<String, dynamic>> collectStatus() async => _map(await _send('GET', '/collect/status'));
  Future<List<Map<String, dynamic>>> paymentRequests({int? partyId}) async =>
      _list(await _send('GET', '/collect/requests${_q({'party_id': partyId})}'));
  Future<int> collectSync() async => (_map(await _send('POST', '/collect/sync'))['new_items'] as int);
  Future<List<Map<String, dynamic>>> inbox({String status = 'open'}) async =>
      _list(await _send('GET', '/inbox${_q({'status': status})}'));
  Future<Map<String, dynamic>> inboxConfirm(int id, {int? partyId}) async =>
      _map(await _send('POST', '/inbox/$id/confirm', partyId == null ? null : {'party_id': partyId}));
  Future<void> inboxDismiss(int id) => _send('POST', '/inbox/$id/dismiss');
  Future<Map<String, dynamic>> collectPlan(String text) async => _map(await _send('POST', '/collect/plan', {'text': text}));
  Future<Map<String, dynamic>> collectPlanVoice(Uint8List wav) async =>
      _map(await _multipart('/collect/plan-voice', {'audio': ('command.wav', wav, 'audio/wav')}));

  /// The shopkeeper confirmed these cards: the server creates the PayPal links and writes the messages.
  Future<List<Map<String, dynamic>>> collectSend(List<Map<String, dynamic>> cards) async =>
      _list(_map(await _send('POST', '/collect/send', {'cards': cards}))['sent']);
}

class BookRef {
  BookRef(this.token, this.name, this.currency);
  factory BookRef.fromJson(Map<String, dynamic> m) => BookRef(m['token'] as String, m['name'] as String, m['currency'] as String);
  final String token;
  String name, currency;
  Map<String, dynamic> toJson() => {'token': token, 'name': name, 'currency': currency};
}

class ApiException implements Exception {
  ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// The server no longer knows this install's token (book erased or server reset).
class UnlinkedException extends ApiException {
  UnlinkedException() : super("This device isn't linked to a book any more.");
}
