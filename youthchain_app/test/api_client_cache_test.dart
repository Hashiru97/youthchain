// Integration test for BL-37 (offline read caching).
//
// flutter_test's TestWidgetsFlutterBinding fakes every real HTTP call as a
// 400 with an empty body (by design, to stop tests from making accidental
// network calls) — so genuine network success/failure is simulated with
// package:http's MockClient via ApiClient.testClient instead. The caching
// logic itself (SharedPreferences read/write, fallback-on-failure) is real,
// not mocked — only the HTTP transport is faked.

import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:youthchain_app/services/api_client.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // flutter_secure_storage talks to a real platform channel that doesn't
  // exist in the widget-test harness — ApiClient.get() calls getToken()
  // internally on every request. Mocked to return null (no token), which
  // getToken() now also tolerates gracefully even without this mock (see
  // the try/catch added there) — mocked here anyway for a clean signal.
  const secureChannel = MethodChannel(
    'plugins.it_nomads.com/flutter_secure_storage',
  );
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(secureChannel, (call) async => null);

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiClient.baseUrl = 'http://127.0.0.1:5000';
    ApiClient.testClient = null;
  });

  tearDown(() {
    ApiClient.testClient = null;
  });

  test('getWithCache returns fresh data and caches it on success', () async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode([
        {"id": 1, "title": "Solar Tech"},
      ]), 200);
    });

    final result = await ApiClient.instance.getWithCache('/jobs');
    expect(result.fromCache, isFalse);
    expect(result.body, contains('Solar Tech'));
  });

  test(
    'getWithCache falls back to the cached copy when the network call fails',
    () async {
      // First call succeeds and primes the cache.
      ApiClient.testClient = MockClient((request) async {
        return http.Response(jsonEncode([
          {"id": 1, "title": "Solar Tech"},
        ]), 200);
      });
      final first = await ApiClient.instance.getWithCache('/jobs');
      expect(first.fromCache, isFalse);

      // Second call: a real connection failure, thrown by the client.
      ApiClient.testClient = MockClient((request) async {
        throw const SocketExceptionStub();
      });

      final second = await ApiClient.instance.getWithCache('/jobs');
      expect(second.fromCache, isTrue);
      expect(second.body, equals(first.body));
      expect(second.cachedAt, isNotNull);
    },
  );

  test(
    'getWithCache throws NoCachedDataException when offline with nothing cached',
    () async {
      ApiClient.testClient = MockClient((request) async {
        throw const SocketExceptionStub();
      });

      expect(
        () => ApiClient.instance.getWithCache('/jobs'),
        throwsA(isA<NoCachedDataException>()),
      );
    },
  );

  test('different paths (e.g. filtered searches) cache independently', () async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/jobs' && request.url.query.contains('q=solar')) {
        return http.Response(jsonEncode([
          {"id": 1, "title": "Solar Tech"},
        ]), 200);
      }
      return http.Response(jsonEncode([
        {"id": 2, "title": "ICT Trainer"},
      ]), 200);
    });

    final filtered = await ApiClient.instance.getWithCache('/jobs?q=solar');
    final plain = await ApiClient.instance.getWithCache('/jobs');
    expect(filtered.body, contains('Solar Tech'));
    expect(plain.body, contains('ICT Trainer'));

    // Now go offline — each cached entry should return its own data, not
    // a mixed/overwritten one.
    ApiClient.testClient = MockClient((request) async {
      throw const SocketExceptionStub();
    });
    final filteredCached = await ApiClient.instance.getWithCache('/jobs?q=solar');
    final plainCached = await ApiClient.instance.getWithCache('/jobs');
    expect(filteredCached.body, contains('Solar Tech'));
    expect(plainCached.body, contains('ICT Trainer'));
  });

  // Regression coverage for a real cross-account data leak found via a full
  // security review: getWithCache used to key its SharedPreferences cache
  // by URL path alone, with no user-id component, and clearSession() never
  // touched that cache at all. On a shared/family device, User A's cached
  // profile/saved-jobs/saved-searches response would survive their logout
  // and could be shown to User B if their network happened to be down when
  // the same screen loaded right after login. Fixed by scoping cache keys
  // per logged-in user and wiping all yc_cache_* entries in clearSession().

  test(
    'two different accounts on the same device do not see each other\'s cached response for the same path',
    () async {
      // User A (id 1) loads /api/candidate/me successfully.
      await ApiClient.instance.saveSession(token: 'token-a', userId: 1);
      ApiClient.testClient = MockClient((request) async {
        return http.Response(jsonEncode({"candidate": {"name": "Alice"}}), 200);
      });
      final aResult = await ApiClient.instance.getWithCache('/api/candidate/me');
      expect(aResult.body, contains('Alice'));

      // User A logs out; User B (id 2) logs in on the same device.
      await ApiClient.instance.clearSession();
      await ApiClient.instance.saveSession(token: 'token-b', userId: 2);

      // User B's network is down right when their profile screen loads --
      // getWithCache falls back to "cache". It must NOT see Alice's data.
      ApiClient.testClient = MockClient((request) async {
        throw const SocketExceptionStub();
      });
      expect(
        () => ApiClient.instance.getWithCache('/api/candidate/me'),
        throwsA(isA<NoCachedDataException>()),
        reason: 'User B has no cached entry of their own yet -- falling back to '
            'User A\'s cached response instead of throwing would be the leak.',
      );
    },
  );

  test(
    'clearSession wipes every cached response, not just the session tokens',
    () async {
      await ApiClient.instance.saveSession(token: 'token-a', userId: 1);
      ApiClient.testClient = MockClient((request) async {
        return http.Response(jsonEncode({"candidate": {"name": "Alice"}}), 200);
      });
      await ApiClient.instance.getWithCache('/api/candidate/me');

      await ApiClient.instance.clearSession();

      final prefs = await SharedPreferences.getInstance();
      final leftoverCacheKeys = prefs.getKeys().where((k) => k.startsWith('yc_cache_'));
      expect(
        leftoverCacheKeys,
        isEmpty,
        reason: 'logout must not leave any cached response readable afterwards',
      );
    },
  );
}

/// A minimal stand-in for a real network failure (e.g. SocketException),
/// without depending on dart:io's SocketException constructor shape.
class SocketExceptionStub implements Exception {
  const SocketExceptionStub();
  @override
  String toString() => 'SocketExceptionStub: connection refused';
}
