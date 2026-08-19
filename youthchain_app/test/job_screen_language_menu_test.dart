// Covers the overflow-menu "Language" quick-switch added after feedback
// that the English/Krio picker was hard to find buried inside Profile & CV
// (see LanguagePicker's own docstring). Uses HomeShell, not JobScreen
// directly, for the same reasons as home_shell_test.dart's own header
// comment -- JobScreen needs a real Scaffold/AppBar ancestor to host its
// PopupMenuButton and the dialog it opens.

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
import 'package:youthchain_app/services/locale_controller.dart';
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
      if (path == '/api/notifications') {
        return http.Response(
          jsonEncode({"notifications": [], "unread_count": 0}),
          200,
        );
      }
      return http.Response(jsonEncode([]), 200);
    });
  });

  tearDown(() async {
    ApiClient.testClient = null;
    await LocaleController.setLocale(null);
  });

  testWidgets(
    'overflow menu has a Language entry that opens the English/Krio picker',
    (tester) async {
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

      await tester.tap(find.byIcon(Icons.more_vert_rounded));
      await tester.pumpAndSettle();

      expect(find.text('Language'), findsOneWidget);

      await tester.tap(find.text('Language'));
      await tester.pumpAndSettle();

      // The dialog shows the same three choices as the profile screen's
      // inline picker, without re-showing the "Language" section label a
      // second time inside its own dialog title.
      expect(find.text('English'), findsOneWidget);
      expect(find.text('Krio'), findsOneWidget);
      expect(find.text('Match my device'), findsOneWidget);
    },
  );

  testWidgets(
    'picking Krio from the overflow dialog switches the running app locale',
    (tester) async {
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

      await tester.tap(find.byIcon(Icons.more_vert_rounded));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Language'));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Krio'));
      await tester.pumpAndSettle();

      expect(LocaleController.locale.value, const Locale('kri'));
    },
  );
}
