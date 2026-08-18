// Regression coverage for a real gap found via a full-codebase review: no
// screen in this app ever inspected a 401 response on an authenticated
// call — the access token has a 12h lifetime and the backend can revoke
// one early (POST /logout's blocklist), but the app just kept showing
// "network error" forever with no route back to login. ApiClient now
// detects a 401 on any request that actually carried a token and clears
// the session + fires ApiClient.onSessionExpired (wired in main.dart to
// navigate to LoginScreen).

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:youthchain_app/services/api_client.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const secureChannel = MethodChannel(
    'plugins.it_nomads.com/flutter_secure_storage',
  );
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(secureChannel, (call) async => null);

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiClient.baseUrl = 'http://127.0.0.1:5000';
    ApiClient.testClient = null;
    ApiClient.onSessionExpired = null;
  });

  tearDown(() async {
    ApiClient.testClient = null;
    ApiClient.onSessionExpired = null;
    await ApiClient.instance.clearSession();
  });

  test(
    'a 401 on an authenticated call clears the session and fires onSessionExpired',
    () async {
      await ApiClient.instance.saveSession(token: 'fake-token', userId: 1);
      expect(await ApiClient.instance.hasSession(), isTrue);

      var fired = false;
      ApiClient.onSessionExpired = () => fired = true;

      ApiClient.testClient = MockClient((request) async {
        expect(request.headers['Authorization'], equals('Bearer fake-token'));
        return http.Response(
          '{"success": false, "error": "Authentication required"}',
          401,
        );
      });

      await ApiClient.instance.get('/api/notifications');

      expect(fired, isTrue);
      expect(await ApiClient.instance.hasSession(), isFalse);
    },
  );

  test(
    'a 401 with no Authorization header (e.g. a failed login) does not clear '
    'the session or fire onSessionExpired',
    () async {
      var fired = false;
      ApiClient.onSessionExpired = () => fired = true;

      ApiClient.testClient = MockClient((request) async {
        expect(request.headers.containsKey('Authorization'), isFalse);
        return http.Response(
          '{"success": false, "error": "Invalid credentials"}',
          401,
        );
      });

      final res = await ApiClient.instance.postJson(
        '/login',
        {'email': 'someone@test.com', 'password': 'wrong'},
        auth: false,
      );

      expect(res.statusCode, equals(401));
      expect(fired, isFalse);
    },
  );

  test('a 200 on an authenticated call does not fire onSessionExpired', () async {
    await ApiClient.instance.saveSession(token: 'fake-token', userId: 1);

    var fired = false;
    ApiClient.onSessionExpired = () => fired = true;

    ApiClient.testClient = MockClient((request) async {
      return http.Response('{"success": true}', 200);
    });

    await ApiClient.instance.get('/api/notifications');

    expect(fired, isFalse);
    expect(await ApiClient.instance.hasSession(), isTrue);
  });

  test(
    'a 401 on an authenticated multipart request clears the session and fires onSessionExpired',
    () async {
      await ApiClient.instance.saveSession(token: 'fake-token', userId: 1);

      var fired = false;
      ApiClient.onSessionExpired = () => fired = true;

      ApiClient.testClient = MockClient((request) async {
        return http.Response('{"success": false}', 401);
      });

      final req = await ApiClient.instance.multipartRequest('/apply');
      expect(req.headers['Authorization'], equals('Bearer fake-token'));
      await ApiClient.instance.sendMultipart(req);

      expect(fired, isTrue);
      expect(await ApiClient.instance.hasSession(), isFalse);
    },
  );
}
