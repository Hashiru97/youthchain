// Covers the overflow-menu "Delete account" entry added after a
// full-codebase audit found the privacy policy (see backend/templates/
// privacy_policy.html) has always promised account/data deletion is
// reachable "from within the app", but nothing in this app ever exposed
// POST /api/account/erase. See job_screen.dart's own _confirmAndEraseAccount()
// docstring for the full reasoning.

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
  });

  tearDown(() {
    ApiClient.testClient = null;
  });

  // JobScreen's own search bar is also a TextField and stays in the
  // widget tree (just behind the modal barrier) once the dialog is open
  // -- passwordField() alone would match both and throw. Scope to
  // the dialog specifically.
  Finder passwordField() => find.descendant(
        of: find.byType(AlertDialog),
        matching: find.byType(TextField),
      );

  Future<void> pumpJobScreenAndOpenDeleteDialog(WidgetTester tester) async {
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
    await tester.tap(find.text('Delete account'));
    await tester.pumpAndSettle();
  }

  testWidgets(
    'overflow menu has a Delete account entry that opens a password-confirmation dialog',
    (tester) async {
      ApiClient.testClient = MockClient((request) async {
        if (request.url.path == '/api/notifications') {
          return http.Response(jsonEncode({"notifications": [], "unread_count": 0}), 200);
        }
        return http.Response(jsonEncode([]), 200);
      });

      await pumpJobScreenAndOpenDeleteDialog(tester);

      expect(find.text('Delete your account?'), findsOneWidget);
      expect(find.text('Delete my account'), findsOneWidget);
    },
  );

  testWidgets(
    'submitting with the wrong password shows an inline error and does not close the dialog',
    (tester) async {
      var eraseCalls = 0;
      ApiClient.testClient = MockClient((request) async {
        if (request.url.path == '/api/notifications') {
          return http.Response(jsonEncode({"notifications": [], "unread_count": 0}), 200);
        }
        if (request.url.path == '/api/account/erase') {
          eraseCalls++;
          return http.Response(jsonEncode({"success": false, "error": "Incorrect password."}), 403);
        }
        return http.Response(jsonEncode([]), 200);
      });

      await pumpJobScreenAndOpenDeleteDialog(tester);

      await tester.enterText(passwordField(), 'wrong-password');
      await tester.tap(find.text('Delete my account'));
      await tester.pumpAndSettle();

      expect(eraseCalls, 1);
      expect(find.text('Incorrect password.'), findsOneWidget);
      // Still on the confirmation dialog, not the success one.
      expect(find.text('Delete your account?'), findsOneWidget);
      expect(find.text('Account deleted'), findsNothing);
    },
  );

  testWidgets(
    'a 409 hold reason from the backend is shown verbatim, not a generic error',
    (tester) async {
      ApiClient.testClient = MockClient((request) async {
        if (request.url.path == '/api/notifications') {
          return http.Response(jsonEncode({"notifications": [], "unread_count": 0}), 200);
        }
        if (request.url.path == '/api/account/erase') {
          return http.Response(
            jsonEncode({
              "success": false,
              "error": "You have an open appeal awaiting review. Your account can be erased once that's resolved.",
            }),
            409,
          );
        }
        return http.Response(jsonEncode([]), 200);
      });

      await pumpJobScreenAndOpenDeleteDialog(tester);
      await tester.enterText(passwordField(), 'correct-password');
      await tester.tap(find.text('Delete my account'));
      await tester.pumpAndSettle();

      expect(
        find.text("You have an open appeal awaiting review. Your account can be erased once that's resolved."),
        findsOneWidget,
      );
    },
  );

  testWidgets(
    'a 429 (rate limited) response shows the too-many-attempts message',
    (tester) async {
      ApiClient.testClient = MockClient((request) async {
        if (request.url.path == '/api/notifications') {
          return http.Response(jsonEncode({"notifications": [], "unread_count": 0}), 200);
        }
        if (request.url.path == '/api/account/erase') {
          return http.Response(jsonEncode({"success": false, "error": "Too many attempts. Try again later."}), 429);
        }
        return http.Response(jsonEncode([]), 200);
      });

      await pumpJobScreenAndOpenDeleteDialog(tester);
      await tester.enterText(passwordField(), 'whatever');
      await tester.tap(find.text('Delete my account'));
      await tester.pumpAndSettle();

      expect(find.text('Too many attempts. Try again later.'), findsOneWidget);
    },
  );

  testWidgets(
    'a successful deletion shows the confirmation dialog',
    (tester) async {
      ApiClient.testClient = MockClient((request) async {
        if (request.url.path == '/api/notifications') {
          return http.Response(jsonEncode({"notifications": [], "unread_count": 0}), 200);
        }
        if (request.url.path == '/api/account/erase') {
          return http.Response(jsonEncode({"success": true}), 200);
        }
        return http.Response(jsonEncode([]), 200);
      });

      await pumpJobScreenAndOpenDeleteDialog(tester);
      await tester.enterText(passwordField(), 'correct-password');
      await tester.tap(find.text('Delete my account'));
      await tester.pumpAndSettle();

      expect(find.text('Account deleted'), findsOneWidget);
      expect(find.text('Your account and personal data have been deleted.'), findsOneWidget);
    },
  );
}
