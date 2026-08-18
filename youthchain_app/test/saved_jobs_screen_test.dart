// Widget coverage for SavedJobsScreen -- fetches GET /api/saved_jobs via
// ApiClient.testClient, same seam every other screen's tests use.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:youthchain_app/screens/discover_job_detail_screen.dart';
import 'package:youthchain_app/screens/job_detail_screen.dart';
import 'package:youthchain_app/screens/saved_jobs_screen.dart';
import 'package:youthchain_app/services/api_client.dart';
import 'package:youthchain_app/theme/app_theme.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const secureChannel = MethodChannel('plugins.it_nomads.com/flutter_secure_storage');
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

  final mixedJobs = [
    {
      "id": 1,
      "title": "Employer Job",
      "location": "Freetown",
      "source": "employer",
      "job_type": "formal",
      "employer": {"name": "Acme SL", "verification_status": "verified"},
    },
    {
      "id": 2,
      "title": "Scraped Job",
      "location": "Bo",
      "source": "scraped",
      "source_name": "Careers.sl",
      "company_name": "Beam Ltd",
      "apply_url": "https://careers.sl/2",
    },
  ];

  Future<void> pumpScreen(WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(theme: AppTheme.light(), home: const SavedJobsScreen(userId: 1)),
    );
    await tester.pump(); // build
    await tester.pump(); // resolve fetches
  }

  testWidgets('renders both employer and scraped saved jobs', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/api/saved_jobs') {
        return http.Response(jsonEncode(mixedJobs), 200);
      }
      return http.Response(jsonEncode([]), 200); // /my_applications/1
    });

    await pumpScreen(tester);

    expect(find.text('Employer Job'), findsOneWidget);
    expect(find.text('Scraped Job'), findsOneWidget);
    expect(find.text('Acme SL'), findsOneWidget);
    expect(find.text('Beam Ltd'), findsOneWidget);
    expect(find.text('Careers.sl'), findsOneWidget);
    expect(find.text('Home'), findsOneWidget); // employer job's source badge
  });

  testWidgets('shows an empty state with no saved jobs', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);

    expect(find.text('No saved jobs yet'), findsOneWidget);
  });

  testWidgets('pulling to refresh from the empty state re-fetches', (tester) async {
    // Regression test: RefreshIndicator previously only wrapped the
    // non-empty ListView.builder branch, so pulling down on "No saved
    // jobs yet" silently did nothing.
    var callCount = 0;
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path != '/api/saved_jobs') {
        return http.Response(jsonEncode([]), 200);
      }
      callCount += 1;
      return http.Response(jsonEncode(callCount == 1 ? [] : mixedJobs), 200);
    });

    await pumpScreen(tester);
    expect(find.text('No saved jobs yet'), findsOneWidget);
    expect(callCount, 1);

    await tester.fling(find.byType(RefreshIndicator), const Offset(0, 300), 1000);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();

    expect(callCount, greaterThan(1));
    expect(find.text('Employer Job'), findsOneWidget);
    expect(find.text('No saved jobs yet'), findsNothing);
  });

  testWidgets('tapping a scraped job opens DiscoverJobDetailScreen', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/api/saved_jobs') {
        return http.Response(jsonEncode([mixedJobs[1]]), 200);
      }
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);
    await tester.tap(find.text('Scraped Job'));
    await tester.pumpAndSettle();

    expect(find.byType(DiscoverJobDetailScreen), findsOneWidget);
  });

  testWidgets('tapping an employer job opens JobDetailScreen', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/api/saved_jobs') {
        return http.Response(jsonEncode([mixedJobs[0]]), 200);
      }
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);
    await tester.tap(find.text('Employer Job'));
    await tester.pumpAndSettle();

    expect(find.byType(JobDetailScreen), findsOneWidget);
  });

  testWidgets('unsaving a card removes it from the list immediately', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/api/saved_jobs') {
        return http.Response(jsonEncode(mixedJobs), 200);
      }
      if (request.url.path == '/api/jobs/1/unsave') {
        return http.Response(jsonEncode({"success": true, "saved": false}), 200);
      }
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);
    expect(find.text('Employer Job'), findsOneWidget);

    // Tap the bookmark button on the first card (already-saved -> filled icon).
    await tester.tap(find.byIcon(Icons.bookmark_rounded).first);
    await tester.pump();

    expect(find.text('Employer Job'), findsNothing);
    expect(find.text('Scraped Job'), findsOneWidget);
  });
}
