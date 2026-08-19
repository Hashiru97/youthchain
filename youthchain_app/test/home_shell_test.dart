// Widget coverage for the app's first real tab navigation. JobScreen
// (Home) opens a real Socket.IO connection and fires three concurrent
// fetches in initState -- ApiClient.testClient covers the HTTP fetches;
// the socket connection attempt is real but async/non-blocking and is
// torn down by JobScreen's own dispose() at test end, same as it would
// be on a real logout/navigation-away in the app. Deliberately uses
// bounded pump() calls, not pumpAndSettle(), so this test can't hang on
// the socket's own reconnection timers.

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
      final path = request.url.path;
      if (path == '/api/discover_jobs') {
        return http.Response(jsonEncode([]), 200);
      }
      if (path == '/api/notifications') {
        return http.Response(
          jsonEncode({"notifications": [], "unread_count": 0}),
          200,
        );
      }
      // /jobs, /api/match_jobs/<id>, /my_applications/<id> and anything
      // else JobScreen's initState fetches -- an empty-but-valid list is
      // enough for it to reach a stable, non-loading state.
      return http.Response(jsonEncode([]), 200);
    });
  });

  tearDown(() {
    ApiClient.testClient = null;
  });

  testWidgets('shows a bottom nav with Home and Discover, defaulting to Home', (
    tester,
  ) async {
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

    expect(find.byType(BottomNavigationBar), findsOneWidget);
    expect(find.text('Home'), findsOneWidget);
    expect(find.text('Discover'), findsOneWidget);
  });

  testWidgets('tapping Discover switches the active IndexedStack tab', (
    tester,
  ) async {
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

    // IndexedStack keeps every tab's widget tree mounted (that's the
    // point -- Home's state survives switching away and back), so
    // find.text() alone can't distinguish "present in the tree" from
    // "the active tab" the way it would for a lazily-built TabBarView.
    // BottomNavigationBar.currentIndex is the real signal of which tab
    // is actually showing.
    BottomNavigationBar navBar() =>
        tester.widget<BottomNavigationBar>(find.byType(BottomNavigationBar));

    expect(navBar().currentIndex, 0);

    await tester.tap(find.text('Discover'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(navBar().currentIndex, 1);
  });

  testWidgets(
    'rendering in Krio does not crash the bottom nav (regression test)',
    (tester) async {
      // Real crash found on a physical device, not caught by any test
      // before this one: picking Krio in the profile screen's language
      // picker sets MaterialApp.locale to Locale('kri') directly, which
      // skips Flutter's normal "fall back to a locale it actually
      // supports" resolution -- every delegate gets asked to resolve
      // 'kri', and Flutter's built-in Material localizations don't have
      // it. BottomNavigationBar specifically requires MaterialLocalizations
      // and threw "No MaterialLocalizations found" the instant this
      // screen tried to render. See kri_material_fallback.dart's own
      // docstring for the fix and why appLocalizationsDelegates (not the
      // narrower AppLocalizations.localizationsDelegates) is what every
      // MaterialApp in this codebase, including every other test in this
      // file, must use -- this test only proves the fix; the shared
      // constant above is what stops the gap from reopening.
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          locale: const Locale('kri'),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const HomeShell(userId: 1),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));

      expect(tester.takeException(), isNull);
      expect(find.byType(BottomNavigationBar), findsOneWidget);
      // navHome/navDiscover's own Krio translations (see app_kri.arb) --
      // pins that the fix didn't just avoid a crash by silently falling
      // back to English for our own content too.
      expect(find.text('Os'), findsOneWidget);
      expect(find.text('Fɛn'), findsOneWidget);
    },
  );
}
