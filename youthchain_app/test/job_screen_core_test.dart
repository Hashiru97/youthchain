// Core fetch/render coverage for JobScreen (Home) -- previously the only
// coverage this, the app's most complex screen (socket integration, 9
// navigation sites, 57 localization references), had was
// job_screen_language_menu_test.dart, which only exercises the overflow
// menu's Language entry. This file covers the actual job list itself:
// fetching/rendering a real job, and one of its navigation sites (opening
// JobDetailScreen from a job card). See home_shell_test.dart and
// job_screen_language_menu_test.dart for the same HomeShell + bounded
// pump() convention this follows -- JobScreen opens a real (but
// non-blocking, torn down on dispose) Socket.IO connection in initState,
// so pumpAndSettle() is avoided in favor of bounded pump() calls.

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
import 'package:youthchain_app/screens/job_detail_screen.dart';
import 'package:youthchain_app/services/api_client.dart';
import 'package:youthchain_app/theme/app_theme.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const secureChannel = MethodChannel(
    'plugins.it_nomads.com/flutter_secure_storage',
  );
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(secureChannel, (call) async => null);

  final sampleJob = {
    "id": 101,
    "title": "Solar Panel Installer",
    "location": "Freetown",
    "duration": "3 months",
    "job_type": "formal",
    "required_skills": "Wiring, Safety",
    "employer": {
      "name": "Acme Energy",
      "verification_status": "verified",
      "rating_count": 5,
      "avg_rating": 4.5,
      "trust_tier": "good",
    },
  };

  Future<void> pumpHome(WidgetTester tester) async {
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

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiClient.baseUrl = 'http://127.0.0.1:5000';
    ApiClient.testClient = MockClient((request) async {
      final path = request.url.path;
      // No candidate profile resolved yet -- fetchJobs() falls back to the
      // plain, unranked /jobs list (a bare array), same as a brand new user.
      if (path == '/api/candidate/me') {
        return http.Response(jsonEncode({'success': false}), 404);
      }
      if (path == '/jobs') {
        return http.Response(jsonEncode([sampleJob]), 200);
      }
      if (path == '/api/notifications') {
        return http.Response(
          jsonEncode({"notifications": [], "unread_count": 0}),
          200,
        );
      }
      // /api/saved_jobs, /my_applications/1, /api/discover_jobs, etc.
      return http.Response(jsonEncode([]), 200);
    });
  });

  tearDown(() {
    ApiClient.testClient = null;
  });

  testWidgets('fetches and renders a job from the plain /jobs list', (
    tester,
  ) async {
    await pumpHome(tester);

    expect(find.text('Solar Panel Installer'), findsOneWidget);
    expect(find.text('Freetown'), findsOneWidget);
    expect(find.textContaining('Acme Energy'), findsOneWidget);
    // job_type "formal" requires a CV -- the button reads accordingly.
    expect(find.text('Apply with CV'), findsOneWidget);
  });

  testWidgets('shows the empty state when there are no jobs', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      final path = request.url.path;
      if (path == '/api/candidate/me') {
        return http.Response(jsonEncode({'success': false}), 404);
      }
      return http.Response(jsonEncode([]), 200);
    });

    await pumpHome(tester);

    expect(find.text('No jobs available right now'), findsOneWidget);
  });

  testWidgets(
    'tapping a job card navigates to JobDetailScreen (navigation site)',
    (tester) async {
      await pumpHome(tester);

      expect(find.text('Solar Panel Installer'), findsOneWidget);
      await tester.tap(find.text('Solar Panel Installer'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));

      expect(find.byType(JobDetailScreen), findsOneWidget);
    },
  );
}
