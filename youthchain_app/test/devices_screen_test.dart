// Real widget coverage for DevicesScreen -- the mobile counterpart to the
// web portal's /portal/devices (see UserSession in app.py). Sources
// GET /api/devices and POST /api/devices/<id>/revoke.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/l10n/app_localizations.dart';
import 'package:youthchain_app/l10n/kri_material_fallback.dart';
import 'package:youthchain_app/screens/devices_screen.dart';
import 'package:youthchain_app/services/api_client.dart';
import 'package:youthchain_app/theme/app_theme.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const secureChannel = MethodChannel(
    'plugins.it_nomads.com/flutter_secure_storage',
  );
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(secureChannel, (call) async => null);

  setUp(() {
    ApiClient.baseUrl = 'http://127.0.0.1:5000';
    ApiClient.testClient = null;
  });

  tearDown(() {
    ApiClient.testClient = null;
  });

  final twoSessions = jsonEncode({
    "sessions": [
      {
        "id": 1,
        "channel": "app",
        "device_label": "This phone",
        "ip_address": "10.0.0.1",
        "created_at": "2026-08-01T00:00:00",
        "last_seen_at": DateTime.now().toIso8601String(),
        "revoked": false,
        "is_current": true,
      },
      {
        "id": 2,
        "channel": "web",
        "device_label": "Chrome on Windows",
        "ip_address": "10.0.0.2",
        "created_at": "2026-08-01T00:00:00",
        "last_seen_at": "2026-08-01T00:00:00",
        "revoked": false,
        "is_current": false,
      },
    ],
  });

  testWidgets('lists every session and marks the current one', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      expect(request.url.path, '/api/devices');
      return http.Response(twoSessions, 200);
    });

    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.light(),
      localizationsDelegates: appLocalizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: DevicesScreen(),
    ));
    await tester.pumpAndSettle();

    expect(find.text('This phone'), findsOneWidget);
    expect(find.text('Chrome on Windows'), findsOneWidget);
    expect(find.text('This device'), findsOneWidget);
  });

  testWidgets('shows an empty state message when there are no sessions', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode({"sessions": []}), 200);
    });

    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.light(),
      localizationsDelegates: appLocalizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: DevicesScreen(),
    ));
    await tester.pumpAndSettle();

    expect(find.text('No active sessions'), findsOneWidget);
  });

  testWidgets('pulling to refresh from the empty state re-fetches', (tester) async {
    // Regression test: the empty-sessions and error branches previously
    // weren't wrapped in RefreshIndicator at all -- pulling down did
    // nothing (the AppBar's own refresh button was the only way).
    var callCount = 0;
    ApiClient.testClient = MockClient((request) async {
      callCount += 1;
      return http.Response(jsonEncode({"sessions": callCount == 1 ? [] : jsonDecode(twoSessions)["sessions"]}), 200);
    });

    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.light(),
      localizationsDelegates: appLocalizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: DevicesScreen(),
    ));
    await tester.pumpAndSettle();

    expect(find.text('No active sessions'), findsOneWidget);
    expect(callCount, 1);

    await tester.fling(find.byType(RefreshIndicator), const Offset(0, 300), 1000);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();

    expect(callCount, greaterThan(1));
    expect(find.text('This phone'), findsOneWidget);
    expect(find.text('No active sessions'), findsNothing);
  });

  testWidgets('pulling to refresh from the error state re-fetches', (tester) async {
    var callCount = 0;
    ApiClient.testClient = MockClient((request) async {
      callCount += 1;
      if (callCount == 1) return http.Response('', 500);
      return http.Response(twoSessions, 200);
    });

    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.light(),
      localizationsDelegates: appLocalizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: DevicesScreen(),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Something went wrong'), findsOneWidget);
    expect(callCount, 1);

    await tester.fling(find.byType(RefreshIndicator), const Offset(0, 300), 1000);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();

    expect(callCount, greaterThan(1));
    expect(find.text('This phone'), findsOneWidget);
    expect(find.text('Something went wrong'), findsNothing);
  });

  testWidgets(
    'revoking another device posts to the right endpoint and refreshes the list',
    (tester) async {
      int getCount = 0;
      http.Request? revokeRequest;
      ApiClient.testClient = MockClient((request) async {
        if (request.method == 'POST') {
          revokeRequest = request;
          return http.Response(jsonEncode({"success": true}), 200);
        }
        getCount++;
        // After the revoke, the refreshed list no longer has device 2.
        if (getCount > 1) {
          return http.Response(
            jsonEncode({
              "sessions": [
                {
                  "id": 1,
                  "channel": "app",
                  "device_label": "This phone",
                  "ip_address": "10.0.0.1",
                  "created_at": "2026-08-01T00:00:00",
                  "last_seen_at": DateTime.now().toIso8601String(),
                  "revoked": false,
                  "is_current": true,
                },
              ],
            }),
            200,
          );
        }
        return http.Response(twoSessions, 200);
      });

      await tester.pumpWidget(MaterialApp(
      theme: AppTheme.light(),
      localizationsDelegates: appLocalizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: DevicesScreen(),
    ));
      await tester.pumpAndSettle();

      // Two revoke buttons exist (one per card) -- tap the second card's.
      final revokeButtons = find.widgetWithIcon(
        IconButton,
        Icons.logout_rounded,
      );
      expect(revokeButtons, findsNWidgets(2));
      await tester.tap(revokeButtons.last);
      await tester.pumpAndSettle();

      expect(find.text('Chrome on Windows will be signed out immediately.'), findsOneWidget);
      await tester.tap(find.text('Revoke'));
      await tester.pumpAndSettle();

      expect(revokeRequest, isNotNull);
      expect(revokeRequest!.url.path, '/api/devices/2/revoke');
      expect(find.text('Chrome on Windows'), findsNothing);
      expect(find.text('Device revoked'), findsOneWidget);
    },
  );

  testWidgets('cancelling the confirm dialog does not call the API', (
    tester,
  ) async {
    bool posted = false;
    ApiClient.testClient = MockClient((request) async {
      if (request.method == 'POST') posted = true;
      return http.Response(twoSessions, 200);
    });

    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.light(),
      localizationsDelegates: appLocalizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: DevicesScreen(),
    ));
    await tester.pumpAndSettle();

    final revokeButtons = find.widgetWithIcon(IconButton, Icons.logout_rounded);
    await tester.tap(revokeButtons.last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();

    expect(posted, isFalse);
    expect(find.text('Chrome on Windows'), findsOneWidget);
  });

  testWidgets(
    'revoking your own current device pops the screen instead of refreshing',
    (tester) async {
      ApiClient.testClient = MockClient((request) async {
        if (request.method == 'POST') {
          return http.Response(jsonEncode({"success": true}), 200);
        }
        return http.Response(twoSessions, 200);
      });

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: Builder(
            builder: (context) => Scaffold(
              body: Center(
                child: ElevatedButton(
                  onPressed: () => Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => const DevicesScreen()),
                  ),
                  child: const Text('Open'),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Open'));
      await tester.pumpAndSettle();

      final revokeButtons = find.widgetWithIcon(
        IconButton,
        Icons.logout_rounded,
      );
      // The first card is "This phone" -- the current device.
      await tester.tap(revokeButtons.first);
      await tester.pumpAndSettle();

      expect(
        find.text(
          'This is the device you\'re using right now. Revoking it will sign you out immediately.',
        ),
        findsOneWidget,
      );
      await tester.tap(find.text('Revoke'));
      await tester.pumpAndSettle();

      // Back on the screen that pushed DevicesScreen.
      expect(find.text('Open'), findsOneWidget);
      expect(find.byType(DevicesScreen), findsNothing);
    },
  );
}
