// Real widget coverage for WorkHistoryScreen -- the gig/informal-work
// counterpart to Passport, sourcing GET /api/candidate/<id>/trust_summary
// (see _worker_trust_summary in app.py).

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:youthchain_app/l10n/app_localizations.dart';
import 'package:youthchain_app/l10n/kri_material_fallback.dart';
import 'package:youthchain_app/screens/work_history_screen.dart';
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

  testWidgets('shows average rating, completed gigs, and rated gig cards', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      expect(request.url.path, '/api/candidate/1/trust_summary');
      return http.Response(
        jsonEncode({
          "completed_gigs": 3,
          "avg_rating": 4.5,
          "rating_count": 2,
          "rated_gigs": [
            {
              "application_id": 10,
              "job_title": "Cleaner Needed",
              "category": "Cleaning",
              "score": 5,
              "comment": "Great work",
              "rated_at": "2026-08-01T00:00:00",
            },
            {
              "application_id": 11,
              "job_title": "Market Vendor Needed",
              "category": "Market Vending",
              "score": 4,
              "comment": null,
              "rated_at": "2026-08-02T00:00:00",
            },
          ],
        }),
        200,
      );
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: WorkHistoryScreen(userId: 1),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('★ 4.5'), findsOneWidget);
    expect(find.text('from 2 ratings'), findsOneWidget);
    expect(find.text('3'), findsOneWidget);
    expect(find.text('completed gigs'), findsOneWidget);
    expect(find.text('Cleaner Needed'), findsOneWidget);
    expect(find.text('“Great work”'), findsOneWidget);
    expect(find.text('Market Vendor Needed'), findsOneWidget);
  });

  testWidgets('shows an empty state when there are no rated gigs yet', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(
        jsonEncode({
          "completed_gigs": 0,
          "avg_rating": null,
          "rating_count": 0,
          "rated_gigs": [],
        }),
        200,
      );
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: WorkHistoryScreen(userId: 1),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('No ratings yet'), findsOneWidget);
    expect(find.text('No rated gigs yet'), findsOneWidget);
  });

  testWidgets('pulling to refresh from the "couldn\'t load" state actually retries', (tester) async {
    // Regression test: the "couldn't load" error state's own subtitle
    // says "Pull down to try again," but that branch was previously a
    // bare ListView with no RefreshIndicator at all -- the gesture it
    // told the user to use didn't do anything.
    var callCount = 0;
    ApiClient.testClient = MockClient((request) async {
      callCount += 1;
      if (callCount == 1) throw Exception('network error');
      return http.Response(
        jsonEncode({
          "completed_gigs": 1,
          "avg_rating": 5.0,
          "rating_count": 1,
          "rated_gigs": [],
        }),
        200,
      );
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: WorkHistoryScreen(userId: 1),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text("Couldn't load your work history"), findsOneWidget);
    expect(find.text("Pull down to try again."), findsOneWidget);
    expect(callCount, 1);

    await tester.fling(find.byType(RefreshIndicator), const Offset(0, 300), 1000);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();

    expect(callCount, greaterThan(1));
    expect(find.text("Couldn't load your work history"), findsNothing);
    expect(find.text('★ 5.0'), findsOneWidget);
  });
}
