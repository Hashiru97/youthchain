// Regression coverage for a real layout bug found live on a physical
// device: the three FloatingActionButton.extended widgets (My
// Applications / Passport / Work History) on JobScreen used to be
// permanently expanded, stacked in a Column. Since a floating action
// button sits at a fixed screen position regardless of scroll, they
// permanently covered part of whichever job cards rendered underneath
// them -- confirmed on-device, "Apply" buttons on some cards were
// physically untappable. Collapsed behind one toggle FAB by default now.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:youthchain_app/l10n/app_localizations.dart';
import 'package:youthchain_app/l10n/kri_material_fallback.dart';
import 'package:youthchain_app/screens/home_shell.dart';
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
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/api/notifications') {
        return http.Response(jsonEncode({"notifications": [], "unread_count": 0}), 200);
      }
      return http.Response(jsonEncode([]), 200);
    });
  });

  tearDown(() {
    ApiClient.testClient = null;
  });

  Future<void> pumpJobScreen(WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const HomeShell(userId: 1),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
  }

  testWidgets(
    'the three quick-action FABs start collapsed behind a single toggle button',
    (tester) async {
      await pumpJobScreen(tester);

      expect(find.text('My Applications'), findsNothing);
      expect(find.text('Passport'), findsNothing);
      expect(find.text('Work History'), findsNothing);
      expect(find.byIcon(Icons.menu_rounded), findsOneWidget);
    },
  );

  testWidgets(
    'tapping the toggle FAB expands the three actions, tapping again collapses them',
    (tester) async {
      await pumpJobScreen(tester);

      await tester.tap(find.byIcon(Icons.menu_rounded));
      await tester.pumpAndSettle();

      expect(find.text('My Applications'), findsOneWidget);
      expect(find.text('Passport'), findsOneWidget);
      expect(find.text('Work History'), findsOneWidget);
      expect(find.byIcon(Icons.close_rounded), findsOneWidget);

      await tester.tap(find.byIcon(Icons.close_rounded));
      await tester.pumpAndSettle();

      expect(find.text('My Applications'), findsNothing);
      expect(find.text('Passport'), findsNothing);
      expect(find.text('Work History'), findsNothing);
      expect(find.byIcon(Icons.menu_rounded), findsOneWidget);
    },
  );
}
