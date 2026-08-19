// Widget coverage for the Discover tab's list screen -- fetches
// GET /api/discover_jobs via ApiClient.testClient (see
// api_client_cache_test.dart), the same seam every other screen's tests
// use rather than a real network call.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:youthchain_app/l10n/app_localizations.dart';
import 'package:youthchain_app/l10n/kri_material_fallback.dart';
import 'package:youthchain_app/screens/discover_screen.dart';
import 'package:youthchain_app/screens/discover_job_detail_screen.dart';
import 'package:youthchain_app/screens/old_listings_screen.dart';
import 'package:youthchain_app/screens/saved_searches_screen.dart';
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

  final sampleJobs = [
    {
      "id": 1,
      "title": "Junior Developer",
      "location": "Freetown",
      "company_name": "Acme SL",
      "company_verified": true,
      "source_name": "Careers.sl",
      "salary": "Negotiable",
      "employment_type": "Full-time",
      "apply_url": "https://careers.sl/1",
    },
    {
      "id": 2,
      "title": "Data Entry Clerk",
      "location": "Bo",
      "company_name": null,
      "company_verified": false,
      "source_name": null,
      "salary": null,
      "apply_url": null,
    },
  ];

  testWidgets('renders every job returned by the API', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      expect(request.url.path, '/api/discover_jobs');
      return http.Response(jsonEncode(sampleJobs), 200);
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const DiscoverScreen(userId: 1),
      ),
    );
    await tester.pump(); // build
    await tester.pump(); // resolve the async fetch

    expect(find.text('Junior Developer'), findsOneWidget);
    expect(find.text('Data Entry Clerk'), findsOneWidget);
    expect(find.text('Acme SL'), findsOneWidget);
    expect(find.text('Careers.sl'), findsOneWidget);
    expect(find.text('Full-time'), findsOneWidget); // job 1's employment_type
    expect(
      find.text('Scraped listing'),
      findsOneWidget,
    ); // job 2's source fallback
  });

  testWidgets(
    'shows a scam-caution badge only on a listing with scam_signals',
    (tester) async {
      final jobsWithASignal = [
        {...sampleJobs[0], "scam_signals": ["fee_request", "personal_email"]},
        {...sampleJobs[1], "scam_signals": <String>[]},
      ];
      ApiClient.testClient = MockClient((request) async {
        return http.Response(jsonEncode(jobsWithASignal), 200);
      });

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const DiscoverScreen(userId: 1),
        ),
      );
      await tester.pump();
      await tester.pump();

      expect(find.text('Use caution'), findsOneWidget);
    },
  );

  testWidgets(
    'shows no scam-caution badge when the backend omits scam_signals entirely',
    (tester) async {
      ApiClient.testClient = MockClient((request) async {
        return http.Response(jsonEncode(sampleJobs), 200);
      });

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const DiscoverScreen(userId: 1),
        ),
      );
      await tester.pump();
      await tester.pump();

      expect(find.text('Use caution'), findsNothing);
    },
  );

  testWidgets('shows a match-score badge only when the backend includes one', (
    tester,
  ) async {
    // "score" is absent entirely (not present-but-zero) whenever the
    // backend isn't actually ranking -- no saved profile yet, or an
    // active search. containsKey, not a null/zero check, is what
    // DiscoverJobCard gates the badge on, same convention job_screen.dart
    // already uses for Home.
    final rankedJobs = [
      {...sampleJobs[0], "score": 80},
      {...sampleJobs[1], "score": 0},
    ];
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode(rankedJobs), 200);
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const DiscoverScreen(userId: 1),
      ),
    );
    await tester.pump();
    await tester.pump();

    expect(find.text('80% match'), findsOneWidget);
    expect(find.text('0% match'), findsOneWidget);
  });

  testWidgets(
    'shows no match-score badge when the backend omits score (unranked)',
    (tester) async {
      ApiClient.testClient = MockClient((request) async {
        return http.Response(
          jsonEncode(sampleJobs),
          200,
        ); // no "score" key at all
      });

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const DiscoverScreen(userId: 1),
        ),
      );
      await tester.pump();
      await tester.pump();

      expect(find.textContaining('% match'), findsNothing);
    },
  );

  testWidgets(
    'shows the scanner-hasn\'t-run-yet empty state when no search is active',
    (tester) async {
      // The exact "scanner hasn't produced listings yet" state -- either a
      // fresh install before the first scan, or every source still
      // returning zero jobs. Must read as a routine "check back later",
      // not something that looks broken.
      ApiClient.testClient = MockClient((request) async {
        return http.Response(jsonEncode([]), 200);
      });

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const DiscoverScreen(userId: 1),
        ),
      );
      await tester.pump();
      await tester.pump();

      expect(find.text('No listings found'), findsOneWidget);
      expect(
        find.text('Check back soon — sources are scanned regularly.'),
        findsOneWidget,
      );
      expect(find.text('Try a different search term.'), findsNothing);
    },
  );

  testWidgets(
    'pulling to refresh from the empty state re-fetches and can surface new listings',
    (tester) async {
      // Same regression this screen shares with OldListingsScreen:
      // RefreshIndicator previously only wrapped the non-empty
      // ListView.builder branch, so pulling down on "No listings found"
      // silently did nothing.
      var callCount = 0;
      ApiClient.testClient = MockClient((request) async {
        if (request.url.path != '/api/discover_jobs') {
          return http.Response(jsonEncode([]), 200);
        }
        callCount += 1;
        return http.Response(jsonEncode(callCount == 1 ? [] : sampleJobs), 200);
      });

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const DiscoverScreen(userId: 1),
        ),
      );
      await tester.pump();
      await tester.pump();

      expect(find.text('No listings found'), findsOneWidget);
      expect(callCount, 1);

      await tester.fling(
        find.byType(RefreshIndicator),
        const Offset(0, 300),
        1000,
      );
      await tester.pump();
      await tester.pump(const Duration(seconds: 1));
      await tester.pumpAndSettle();

      expect(callCount, greaterThan(1));
      expect(find.text('Junior Developer'), findsOneWidget);
      expect(find.text('No listings found'), findsNothing);
    },
  );

  testWidgets(
    'shows a different empty-state subtitle when a search returns nothing',
    (tester) async {
      // Distinct from the scanner-hasn't-run-yet case above: real
      // listings exist, this specific search just didn't match any of
      // them -- telling the user to "check back later" here would be
      // actively misleading (nothing about waiting will fix a search
      // typo).
      ApiClient.testClient = MockClient((request) async {
        if ((request.url.queryParameters['q'] ?? '').isEmpty) {
          return http.Response(jsonEncode(sampleJobs), 200);
        }
        return http.Response(jsonEncode([]), 200);
      });

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const DiscoverScreen(userId: 1),
        ),
      );
      await tester.pump();
      await tester.pump();
      expect(
        find.text('Junior Developer'),
        findsOneWidget,
      ); // sanity: real jobs loaded first

      await tester.enterText(find.byType(TextField), 'zzz_no_such_job');
      await tester.pump(
        const Duration(milliseconds: 500),
      ); // clear the 400ms debounce
      await tester.pump();
      await tester.pump();

      expect(find.text('No listings found'), findsOneWidget);
      expect(find.text('Try a different search term.'), findsOneWidget);
      expect(
        find.text('Check back soon — sources are scanned regularly.'),
        findsNothing,
      );
    },
  );

  testWidgets('tapping a job card opens DiscoverJobDetailScreen', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode(sampleJobs), 200);
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const DiscoverScreen(userId: 1),
      ),
    );
    await tester.pump();
    await tester.pump();

    await tester.tap(find.text('Junior Developer'));
    await tester.pumpAndSettle();

    expect(find.byType(DiscoverJobDetailScreen), findsOneWidget);
    expect(find.text('Job Details'), findsOneWidget);
  });

  testWidgets('search field debounces and requests with a q param', (
    tester,
  ) async {
    final requestedPaths = <String>[];
    ApiClient.testClient = MockClient((request) async {
      requestedPaths.add(request.url.toString());
      return http.Response(jsonEncode(sampleJobs), 200);
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const DiscoverScreen(userId: 1),
      ),
    );
    await tester.pump();
    await tester.pump();

    await tester.enterText(find.byType(TextField), 'developer');
    // Debounce is 400ms -- advance past it, then let the fetch resolve.
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pump();

    expect(requestedPaths.any((p) => p.contains('q=developer')), isTrue);
  });

  testWidgets('the saved-searches icon opens SavedSearchesScreen', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/api/saved_searches') {
        return http.Response(jsonEncode([]), 200);
      }
      return http.Response(jsonEncode(sampleJobs), 200);
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const DiscoverScreen(userId: 1),
      ),
    );
    await tester.pump();
    await tester.pump();

    await tester.tap(find.byTooltip('Saved searches'));
    await tester.pumpAndSettle();

    expect(find.byType(SavedSearchesScreen), findsOneWidget);
  });

  testWidgets('the search bell saves the current search as an alert', (
    tester,
  ) async {
    http.Request? capturedSave;
    ApiClient.testClient = MockClient((request) async {
      if (request.method == 'POST' &&
          request.url.path == '/api/saved_searches') {
        capturedSave = request;
        return http.Response(
          jsonEncode({
            "success": true,
            "saved_search": {"id": 1, "q": "developer"},
          }),
          201,
        );
      }
      return http.Response(jsonEncode(sampleJobs), 200);
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const DiscoverScreen(userId: 1),
      ),
    );
    await tester.pump();
    await tester.pump();

    // The bell is absent until there's something to alert on -- same
    // "at least one filter" rule POST /api/saved_searches itself enforces.
    expect(find.byTooltip('Alert me for this search'), findsNothing);

    await tester.enterText(find.byType(TextField), 'developer');
    await tester.pump(
      const Duration(milliseconds: 500),
    ); // clear the 400ms debounce
    await tester.pump();

    await tester.tap(find.byTooltip('Alert me for this search'));
    await tester.pumpAndSettle();

    expect(capturedSave, isNotNull);
    final body = jsonDecode(capturedSave!.body) as Map;
    expect(body['q'], 'developer');
    expect(
      find.textContaining('developer'),
      findsWidgets,
    ); // confirmation snackbar
  });

  testWidgets('the history icon opens OldListingsScreen', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/api/discover_jobs/old') {
        return http.Response(jsonEncode([]), 200);
      }
      return http.Response(jsonEncode(sampleJobs), 200);
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const DiscoverScreen(userId: 1),
      ),
    );
    await tester.pump();
    await tester.pump();

    await tester.tap(find.byIcon(Icons.history_rounded));
    await tester.pumpAndSettle();

    expect(find.byType(OldListingsScreen), findsOneWidget);
  });
}
