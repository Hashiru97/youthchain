// Widget coverage for PassportScreen -- fetches GET /passport/<id> via
// ApiClient.testClient, the same seam every other screen's tests use rather
// than a real network call. Previously untested; added alongside the Krio
// localization pass for this screen.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:youthchain_app/l10n/app_localizations.dart';
import 'package:youthchain_app/l10n/kri_material_fallback.dart';
import 'package:youthchain_app/screens/passport_screen.dart';
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
    SharedPreferences.setMockInitialValues({});
    ApiClient.baseUrl = 'http://127.0.0.1:5000';
    ApiClient.testClient = null;
  });

  tearDown(() {
    ApiClient.testClient = null;
  });

  Future<void> pumpScreen(WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const PassportScreen(userId: 1),
      ),
    );
    await tester.pump(); // build
    await tester.pump(); // resolve the async fetch
  }

  testWidgets('renders every credential returned by the API', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/passport/1') {
        return http.Response(
          jsonEncode([
            {
              "id": 1,
              "title": "Solar Installation Certificate",
              "issuer": "Njala University",
              "year": "2025",
              "onchain_tx": "0xabc123",
              "revoked_at": null,
            },
          ]),
          200,
        );
      }
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);

    expect(find.text('Solar Installation Certificate'), findsOneWidget);
    expect(find.text('Njala University'), findsOneWidget);
    expect(find.text('On-chain'), findsOneWidget);
  });

  testWidgets('shows an empty state when there are no credentials', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);

    expect(find.text('No credentials yet'), findsOneWidget);
  });

  testWidgets('shows a pending badge for a credential not yet on-chain', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/passport/1') {
        return http.Response(
          jsonEncode([
            {
              "id": 2,
              "title": "Welding Certificate",
              "issuer": "GTTC",
              "year": "2024",
              "onchain_tx": null,
              "revoked_at": null,
            },
          ]),
          200,
        );
      }
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);

    expect(find.text('Pending'), findsOneWidget);
  });

  testWidgets('shows a revoked badge for a credential an admin revoked', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/passport/1') {
        return http.Response(
          jsonEncode([
            {
              "id": 3,
              "title": "Data Entry Certificate",
              "issuer": "NCTVA",
              "year": "2023",
              "onchain_tx": "0xdef456",
              "revoked_at": "2026-08-01T00:00:00",
            },
          ]),
          200,
        );
      }
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);

    expect(find.text('Revoked'), findsOneWidget);
  });

  testWidgets('the "Add Credential" FAB opens the upload sheet', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);

    await tester.tap(find.text('Add Credential'));
    await tester.pumpAndSettle();

    expect(find.text('Add a credential'), findsOneWidget);
    expect(find.text('Certificate title'), findsOneWidget);
    expect(find.text('Issued by'), findsOneWidget);
  });
}
