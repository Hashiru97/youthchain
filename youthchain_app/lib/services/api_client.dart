import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

/// Result of a cache-aware GET (see ApiClient.getWithCache) — BL-37.
class CachedResult {
  final String body;
  final bool fromCache;
  final DateTime? cachedAt;

  CachedResult({required this.body, required this.fromCache, this.cachedAt});
}

/// Thrown by getWithCache when the network call failed AND there is no
/// cached copy to fall back to (e.g. the very first launch, offline, on a
/// screen that's never successfully loaded before).
class NoCachedDataException implements Exception {
  final String message;
  NoCachedDataException(this.message);
  @override
  String toString() => message;
}

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

  static const String _defaultBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://127.0.0.1:5000',
  );

  /// Mutable (not const) so tests can point it at a real local server
  /// instance instead of mocking the network layer — see
  /// test/api_client_cache_test.dart.
  static String baseUrl = _defaultBaseUrl;

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

  /// Real gap found via a full-codebase review: nothing in this app ever
  /// inspected a 401 response on an authenticated call -- the access
  /// token has a 12h lifetime, the backend can now revoke one early (see
  /// POST /logout's blocklist), and a screen that just kept silently
  /// showing "network error" forever with no way back to login was the
  /// actual observed behavior. Set once in main.dart to navigate to
  /// LoginScreen (clearing the stack) whenever this fires. Left null in
  /// tests/no-UI contexts, where there's nothing to navigate.
  static void Function()? onSessionExpired;

  /// Runs after every request this class makes. Only treats a 401 as a
  /// real session expiry when a token was actually attached to THIS
  /// specific request -- a 401 from e.g. a bad-password /login attempt
  /// (auth: false, no Authorization header) is a normal login failure,
  /// not an expired session, and must not force a redirect back to the
  /// login screen the user is already looking at.
  Future<void> _handleAuthResponse(int statusCode, Map<String, String> headers) async {
    if (statusCode == 401 && headers.containsKey('Authorization')) {
      await clearSession();
      onSessionExpired?.call();
    }
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
    if (_cachedToken != null) return _cachedToken;
    try {
      _cachedToken = await _storage.read(key: _tokenKey);
    } catch (_) {
      // Secure storage's platform channel can be unavailable in some
      // contexts (e.g. a plain `dart test` harness with no platform
      // bindings) — degrade to "no token" rather than let this bubble up
      // and be mistaken for a network failure by callers like
      // getWithCache(), which would otherwise mask every request as
      // "offline".
      return null;
    }
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

  /// Overridable for tests (see test/api_client_cache_test.dart), so
  /// network success/failure can be simulated deterministically with
  /// package:http's MockClient instead of depending on real connectivity —
  /// flutter_test's TestWidgetsFlutterBinding fakes all real HTTP calls as
  /// a 400 with no body by design, so a genuinely "live" test isn't
  /// possible without this seam. Normally null: real requests use the
  /// top-level http.get from package:http.
  static http.Client? testClient;

  Future<http.Response> get(String path) async {
    final headers = await _headers();
    final client = testClient;
    final res = client != null
        ? await client.get(uri(path), headers: headers).timeout(timeout)
        : await http.get(uri(path), headers: headers).timeout(timeout);
    await _handleAuthResponse(res.statusCode, headers);
    return res;
  }

  // ---------------- Offline read caching (BL-37) ----------------
  //
  // Scope, stated explicitly rather than left implicit: this covers
  // read-through caching only — previously-loaded jobs, applications, and
  // credentials remain viewable with no connection. It does NOT queue
  // writes (job applications, credential uploads) made while offline for
  // later replay; those involve file uploads, and a correct offline queue
  // for binary attachments is a meaningfully bigger feature that deserves
  // its own pass rather than a half-built version bundled in here. Callers
  // that write data should still just fail clearly when offline, which
  // they already do.
  static const _cachePrefix = 'yc_cache_';
  static const _cacheTimePrefix = 'yc_cache_time_';

  String _cacheKeyFor(String path) =>
      _cachePrefix + path.replaceAll(RegExp(r'[^A-Za-z0-9]'), '_');

  /// Tries the network first; on success, caches the response body for this
  /// path and returns it. On any network failure, falls back to the last
  /// successfully cached response for this exact path (including query
  /// string, so a search/filtered request has its own cache entry). Throws
  /// [NoCachedDataException] if neither is available.
  Future<CachedResult> getWithCache(String path) async {
    try {
      final res = await get(path);
      if (res.statusCode == 200) {
        await _saveCache(path, res.body);
        return CachedResult(body: res.body, fromCache: false);
      }
      // Non-200 with a real server response (e.g. 401/403) is not a
      // connectivity problem — don't mask it behind stale cached data.
      return CachedResult(body: res.body, fromCache: false);
    } catch (_) {
      final cached = await _loadCache(path);
      if (cached != null) {
        return cached;
      }
      throw NoCachedDataException(
        'No network connection and no previously loaded data for this screen.',
      );
    }
  }

  Future<void> _saveCache(String path, String body) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_cacheKeyFor(path), body);
      await prefs.setString(
        _cacheTimePrefix + _cacheKeyFor(path),
        DateTime.now().toIso8601String(),
      );
    } catch (_) {
      // Caching is a best-effort convenience — never let a cache write
      // failure take down an otherwise-successful network response.
    }
  }

  Future<CachedResult?> _loadCache(String path) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final body = prefs.getString(_cacheKeyFor(path));
      if (body == null) return null;
      final timeRaw = prefs.getString(_cacheTimePrefix + _cacheKeyFor(path));
      final cachedAt = timeRaw != null ? DateTime.tryParse(timeRaw) : null;
      return CachedResult(body: body, fromCache: true, cachedAt: cachedAt);
    } catch (_) {
      return null;
    }
  }

  /// [auth] is false only for the handful of endpoints callable before a
  /// session exists (register, login, OTP request/verify).
  ///
  /// Real gap found via a live audit, not assumed: unlike get() above,
  /// this never checked [testClient] — every POST call (report
  /// submission, profile save, job application status changes, etc.) was
  /// architecturally untestable in a widget test, not just untested,
  /// since flutter_test's TestWidgetsFlutterBinding fakes any real HTTP
  /// call as a bare 400 with no body. Fixed to mirror get()'s exact
  /// pattern.
  Future<http.Response> postJson(
    String path,
    Map<String, dynamic> body, {
    bool auth = true,
  }) async {
    final headers = auth
        ? await _headers(jsonBody: true)
        : {'Content-Type': 'application/json'};
    final client = testClient;
    final res = client != null
        ? await client
            .post(uri(path), headers: headers, body: json.encode(body))
            .timeout(timeout)
        : await http
            .post(uri(path), headers: headers, body: json.encode(body))
            .timeout(timeout);
    await _handleAuthResponse(res.statusCode, headers);
    return res;
  }

  /// Mirrors postJson's testClient-aware pattern above. Currently only
  /// used by PushNotificationService to PUT /api/push_token — a body-
  /// carrying idempotent "set this value" call, which is what PUT means,
  /// as opposed to postJson's POST semantics.
  Future<http.Response> putJson(
    String path,
    Map<String, dynamic> body, {
    bool auth = true,
  }) async {
    final headers = auth
        ? await _headers(jsonBody: true)
        : {'Content-Type': 'application/json'};
    final client = testClient;
    final res = client != null
        ? await client
            .put(uri(path), headers: headers, body: json.encode(body))
            .timeout(timeout)
        : await http
            .put(uri(path), headers: headers, body: json.encode(body))
            .timeout(timeout);
    await _handleAuthResponse(res.statusCode, headers);
    return res;
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

  /// Sends a multipart request, respecting [testClient] when set.
  ///
  /// `BaseRequest.send()` (what `req.send()` calls directly) always spins up
  /// its own throwaway `http.Client()` internally — it does not go through
  /// this class at all, so it silently ignores [testClient]. That's fine for
  /// a real device/emulator run, but makes any code path built around a
  /// bare `req.send()` untestable under `flutter_test` (which fakes all real
  /// HTTP as a bodyless 400 — see the BL-37 offline-cache tests for the same
  /// constraint hit and fixed the same way). Route multipart sends through
  /// this helper wherever they need to be testable.
  Future<http.StreamedResponse> sendMultipart(http.MultipartRequest request) async {
    final client = testClient;
    final res = client != null ? await client.send(request) : await request.send();
    await _handleAuthResponse(res.statusCode, request.headers);
    return res;
  }
}
