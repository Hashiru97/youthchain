// Real widget coverage for the "report this job/employer" feature added
// alongside the employer-suspension/report-review work on the backend.
// Uses ApiClient.testClient (see api_client_cache_test.dart) rather than a
// real network call -- postJson() only started respecting testClient as
// part of writing this test, which is itself the finding worth recording:
// every POST-based flow in this app (this one included) was previously
// architecturally untestable in a widget test, not just untested.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/l10n/app_localizations.dart';
import 'package:youthchain_app/l10n/kri_material_fallback.dart';
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

  setUp(() {
    ApiClient.baseUrl = 'http://127.0.0.1:5000';
    ApiClient.testClient = null;
  });

  tearDown(() {
    ApiClient.testClient = null;
  });

  final ownedJob = {
    "id": 6,
    "title": "Solar Field Technician",
    "location": "Freetown",
    "duration": "6 months",
    "required_skills": "Wiring, Solar Panels",
    "employer_id": 2,
    "employer": {
      "name": "Sierra Solar Co",
      "verification_status": "verified",
      "industry": "Energy & Utilities",
    },
  };

  final unownedJob = {
    "id": 1,
    "title": "Agro-Processing Internship",
    "location": "Freetown",
    "duration": "3 months",
    "required_skills": "",
    "employer_id": null,
    "employer": null,
  };

  testWidgets('report action only appears for a job with a real employer', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: JobDetailScreen(
          job: unownedJob,
          alreadyApplied: false,
          onApply: () {},
        ),
      ),
    );

    expect(find.byTooltip('Report this job'), findsNothing);
    expect(
      find.text(
        'Posted by YouthChain (no employer account linked to this listing)',
      ),
      findsOneWidget,
    );
  });

  testWidgets(
    'report action appears and opens the report sheet for an owned job',
    (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: JobDetailScreen(
            job: ownedJob,
            alreadyApplied: false,
            onApply: () {},
          ),
        ),
      );

      expect(find.byTooltip('Report this job'), findsOneWidget);

      await tester.tap(find.byTooltip('Report this job'));
      await tester.pumpAndSettle();

      expect(find.text('Report this employer'), findsOneWidget);
      expect(find.text('Category'), findsOneWidget);
      expect(find.text('Scam'), findsOneWidget);
      expect(find.text('Harassment'), findsOneWidget);
      expect(find.text('Fake job'), findsOneWidget);
      expect(find.text('Inappropriate'), findsOneWidget);
      expect(find.text('Other'), findsOneWidget);
      expect(find.text('Submit report'), findsOneWidget);
    },
  );

  testWidgets(
    'submitting a report posts the right employer_id, job_id, and category',
    (tester) async {
      http.Request? captured;
      ApiClient.testClient = MockClient((request) async {
        captured = request;
        return http.Response(
          jsonEncode({"success": true, "report_id": 42}),
          201,
        );
      });

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: JobDetailScreen(
            job: ownedJob,
            alreadyApplied: false,
            onApply: () {},
          ),
        ),
      );

      await tester.tap(find.byTooltip('Report this job'));
      await tester.pumpAndSettle();

      // Default category is "scam" -- switch to Harassment to confirm chip
      // selection actually drives the submitted payload, not just the default.
      await tester.tap(find.text('Harassment'));
      await tester.pump();

      await tester.enterText(
        find.byType(TextField),
        'They asked for money upfront.',
      );
      await tester.tap(find.text('Submit report'));
      await tester.pumpAndSettle();

      expect(captured, isNotNull);
      expect(captured!.url.path, '/api/report_employer');
      final body = jsonDecode(captured!.body) as Map;
      expect(body['employer_id'], 2);
      expect(body['job_id'], 6);
      expect(body['category'], 'harassment');
      expect(body['details'], 'They asked for money upfront.');

      // Sheet closes and a confirming SnackBar appears.
      expect(find.text('Category'), findsNothing);
      expect(
        find.text(
          'Report submitted — thank you for helping keep YouthChain safe',
        ),
        findsOneWidget,
      );
    },
  );

  testWidgets(
    'a 429 rate-limit response shows the server error, not a generic failure',
    (tester) async {
      ApiClient.testClient = MockClient((request) async {
        return http.Response(
          jsonEncode({
            "success": false,
            "error": "Too many reports. Try again later.",
          }),
          429,
        );
      });

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: JobDetailScreen(
            job: ownedJob,
            alreadyApplied: false,
            onApply: () {},
          ),
        ),
      );

      await tester.tap(find.byTooltip('Report this job'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Submit report'));
      await tester.pumpAndSettle();

      expect(find.text('Too many reports. Try again later.'), findsOneWidget);
      // The sheet stays open on failure -- the user's category/details choice
      // (and the option to try again) isn't silently discarded.
      expect(find.text('Category'), findsOneWidget);
    },
  );

  group('employer verification_type badge and trust rating', () {
    // Real gap closed: an individual hiring informally (no business to
    // register, see Employer.verification_type in app.py) previously had
    // no way to show a distinct, honest badge -- and the earned rating
    // from past workers (the thing that actually lets a paperwork-free
    // employer build real trust) was never shown anywhere at all.

    testWidgets('shows "Verified Business" for a business-track employer', (
      tester,
    ) async {
      final job = {
        ...ownedJob,
        "employer": {
          "name": "Sierra Solar Co",
          "verification_status": "verified",
          "verification_type": "business",
          "industry": "Energy & Utilities",
        },
      };
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: JobDetailScreen(
            job: job,
            alreadyApplied: false,
            onApply: () {},
          ),
        ),
      );
      expect(find.text('Verified Business'), findsOneWidget);
      expect(find.text('Verified Individual'), findsNothing);
    });

    testWidgets(
      'shows "Verified Individual" for an individual-track employer',
      (tester) async {
        final job = {
          ...ownedJob,
          "employer": {
            "name": "Mama Kadi",
            "verification_status": "verified",
            "verification_type": "individual",
            "industry": null,
          },
        };
        await tester.pumpWidget(
          MaterialApp(
            theme: AppTheme.light(),
            localizationsDelegates: appLocalizationsDelegates,
            supportedLocales: AppLocalizations.supportedLocales,
            home: JobDetailScreen(
              job: job,
              alreadyApplied: false,
              onApply: () {},
            ),
          ),
        );
        expect(find.text('Verified Individual'), findsOneWidget);
        expect(find.text('Verified Business'), findsNothing);
      },
    );

    testWidgets('shows the earned rating from past workers when present', (
      tester,
    ) async {
      final job = {
        ...ownedJob,
        "employer": {
          "name": "Mama Kadi",
          "verification_status": "unverified",
          "verification_type": "individual",
          "industry": null,
          "avg_rating": 4.6,
          "rating_count": 11,
        },
      };
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: JobDetailScreen(
            job: job,
            alreadyApplied: false,
            onApply: () {},
          ),
        ),
      );
      expect(find.text('4.6 · 11 ratings from past workers'), findsOneWidget);
    });

    testWidgets('shows nothing when there are no ratings yet', (tester) async {
      final job = {
        ...ownedJob,
        "employer": {
          "name": "Mama Kadi",
          "verification_status": "unverified",
          "verification_type": "individual",
          "industry": null,
          "avg_rating": null,
          "rating_count": 0,
        },
      };
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: JobDetailScreen(
            job: job,
            alreadyApplied: false,
            onApply: () {},
          ),
        ),
      );
      expect(find.textContaining('ratings from past workers'), findsNothing);
    });

    testWidgets(
      'shows the employer trust badge even with zero ratings yet',
      (tester) async {
        final job = {
          ...ownedJob,
          "employer": {
            "name": "Mama Kadi",
            "verification_status": "unverified",
            "verification_type": "individual",
            "industry": null,
            "avg_rating": null,
            "rating_count": 0,
            "trust_tier": "fair",
          },
        };
        await tester.pumpWidget(
          MaterialApp(
            theme: AppTheme.light(),
            localizationsDelegates: appLocalizationsDelegates,
            supportedLocales: AppLocalizations.supportedLocales,
            home: JobDetailScreen(
              job: job,
              alreadyApplied: false,
              onApply: () {},
            ),
          ),
        );
        expect(find.text('Fair standing'), findsOneWidget);
      },
    );

    testWidgets('shows a caution trust badge for a low-trust employer', (
      tester,
    ) async {
      final job = {
        ...ownedJob,
        "employer": {
          "name": "Risky Co",
          "verification_status": "rejected",
          "verification_type": "business",
          "industry": null,
          "avg_rating": null,
          "rating_count": 0,
          "trust_tier": "caution",
        },
      };
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: JobDetailScreen(
            job: job,
            alreadyApplied: false,
            onApply: () {},
          ),
        ),
      );
      expect(find.text('Use caution'), findsOneWidget);
    });
  });
}
