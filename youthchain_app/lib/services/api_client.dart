import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;

/// Single source of truth for talking to the YouthChain backend.
///
/// Replaces the six independently-hardcoded `_base` constants that used to
/// live in each screen (one per screen) with one configurable value, and
/// centralizes attaching the JWT bearer token issued by /login, /register,
/// and /auth/otp/verify to every authenticated request.
///
/// The backend host defaults to the local dev server. Override at build
/// time for a real deployment with:
///   flutter build apk --dart-define=API_BASE_URL=https://api.youthchain.example
class ApiClient {
  ApiClient._();

  static final ApiClient instance = ApiClient._();

  static const String baseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://127.0.0.1:5000',
  );

  static const Duration timeout = Duration(seconds: 30);

  final FlutterSecureStorage _storage = const FlutterSecureStorage();
  static const _tokenKey = 'yc_access_token';
  static const _userIdKey = 'yc_user_id';
  static const _candidateIdKey = 'yc_candidate_id';

  String? _cachedToken;
  int? _cachedUserId;
  int? _cachedCandidateId;

  /// Persists the session after a successful /register, /login, or
  /// /auth/otp/verify response. This is what makes the app survive a
  /// restart without forcing a re-login every time (previously the app had
  /// no session persistence at all — see Phase 1/3 of the engineering
  /// review).
  Future<void> saveSession({required String token, required int userId}) async {
    _cachedToken = token;
    _cachedUserId = userId;
    await _storage.write(key: _tokenKey, value: token);
    await _storage.write(key: _userIdKey, value: userId.toString());
  }

  Future<void> clearSession() async {
    _cachedToken = null;
    _cachedUserId = null;
    _cachedCandidateId = null;
    await _storage.delete(key: _tokenKey);
    await _storage.delete(key: _userIdKey);
    await _storage.delete(key: _candidateIdKey);
  }

  /// Candidate.id is a distinct primary key from User.id (see backend
  /// Candidate model). Cache it locally once resolved via /api/candidate/me
  /// or captured from a POST /api/candidate response, so screens don't have
  /// to re-resolve it on every navigation.
  Future<void> saveCandidateId(int candidateId) async {
    _cachedCandidateId = candidateId;
    await _storage.write(key: _candidateIdKey, value: candidateId.toString());
  }

  Future<int?> getCandidateId() async {
    if (_cachedCandidateId != null) return _cachedCandidateId;
    final raw = await _storage.read(key: _candidateIdKey);
    _cachedCandidateId = raw != null ? int.tryParse(raw) : null;
    return _cachedCandidateId;
  }

  Future<String?> getToken() async {
    _cachedToken ??= await _storage.read(key: _tokenKey);
    return _cachedToken;
  }

  Future<int?> getUserId() async {
    if (_cachedUserId != null) return _cachedUserId;
    final raw = await _storage.read(key: _userIdKey);
    _cachedUserId = raw != null ? int.tryParse(raw) : null;
    return _cachedUserId;
  }

  Future<bool> hasSession() async {
    return (await getToken()) != null && (await getUserId()) != null;
  }

  /// Returns the caller's Candidate.id, resolving it from the backend via
  /// /api/candidate/me if not already cached locally. Returns null if the
  /// user hasn't created a candidate profile yet (nothing to resolve).
  Future<int?> resolveCandidateId({bool forceRefresh = false}) async {
    if (!forceRefresh) {
      final cached = await getCandidateId();
      if (cached != null) return cached;
    }
    try {
      final res = await get('/api/candidate/me');
      if (res.statusCode != 200) return null;
      final body = json.decode(res.body);
      final candidate = body is Map ? body['candidate'] : null;
      if (candidate is Map && candidate['id'] != null) {
        final id = (candidate['id'] as num).toInt();
        await saveCandidateId(id);
        return id;
      }
    } catch (_) {
      // Network/parse failure — treat as "no candidate profile resolved yet"
      // rather than surfacing an error here; callers already have their own
      // fallbacks for the no-candidate-profile case.
    }
    return null;
  }

  Uri uri(String path) => Uri.parse('$baseUrl$path');

  Future<Map<String, String>> _headers({bool jsonBody = false}) async {
    final token = await getToken();
    return {
      if (jsonBody) 'Content-Type': 'application/json',
      if (token != null) 'Authorization': 'Bearer $token',
    };
  }

  Future<http.Response> get(String path) async {
    return http.get(uri(path), headers: await _headers()).timeout(timeout);
  }

  /// [auth] is false only for the handful of endpoints callable before a
  /// session exists (register, login, OTP request/verify).
  Future<http.Response> postJson(
    String path,
    Map<String, dynamic> body, {
    bool auth = true,
  }) async {
    final headers = auth
        ? await _headers(jsonBody: true)
        : {'Content-Type': 'application/json'};
    return http
        .post(uri(path), headers: headers, body: json.encode(body))
        .timeout(timeout);
  }

  /// Builds a multipart request with the auth header attached; caller adds
  /// fields/files and sends it (kept as a request, not a response, so
  /// callers can add files without this class knowing about file-picker
  /// types).
  Future<http.MultipartRequest> multipartRequest(String path) async {
    final req = http.MultipartRequest('POST', uri(path));
    final token = await getToken();
    if (token != null) req.headers['Authorization'] = 'Bearer $token';
    return req;
  }
}
