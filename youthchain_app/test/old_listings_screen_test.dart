// Widget coverage for the "Old Listings" screen -- GET
// /api/discover_jobs/old, the home for Discover listings whose deadline
// has passed (see backend/scanner/reaper.py for the ~1 week auto-wipe
// that keeps this list short-lived). Mirrors discover_screen_test.dart's
// ApiClient.testClient mocking pattern.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/screens/discover_job_detail_screen.dart';
import 'package:youthchain_app/screens/old_listings_screen.dart';
import 'package:youthchain_app/services/api_client.dart';
import 'package:youthchain_app/theme/app_theme.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const secureChannel = MethodChannel('plugins.it_nomads.com/flutter_secure_storage');
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(secureChannel, (call) async => null);

  setUp(() {
    ApiClient.baseUrl = 'http://127.0.0.1:5000';
    ApiClient.testClient = null;
  });

  tearDown(() {
    ApiClient.testClient = null;
  });

  final expiredJobs = [
    {
      "id": 5,
      "title": "Expired Cashier Role",
      "location": "Freetown",
      "company_name": "Old Shop SL",
      "company_verified": false,
      "source_name": "Careers.sl",
      "salary": null,
      "employment_type": null,
      "apply_url": null,
      "application_deadline": "2026-01-01",
      "is_expired": true,
    },
  ];

  testWidgets('requests /api/discover_jobs/old and renders the returned listings', (tester) async {
    final requestedPaths = <String>[];
    ApiClient.testClient = MockClient((request) async {
      requestedPaths.add(request.url.path);
      if (request.url.path == '/api/discover_jobs/old') {
        return http.Response(jsonEncode(expiredJobs), 200);
      }
      return http.Response(jsonEncode([]), 200);
    });

    await tester.pumpWidget(
      MaterialApp(theme: AppTheme.light(), home: const OldListingsScreen()),
    );
    await tester.pump();
    await tester.pump();

    expect(requestedPaths, contains('/api/discover_jobs/old'));
    expect(find.text('Expired Cashier Role'), findsOneWidget);
    expect(find.text('Old Listings'), findsOneWidget);
  });

  testWidgets('shows an empty state when there are no old listings', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode([]), 200);
    });

    await tester.pumpWidget(
      MaterialApp(theme: AppTheme.light(), home: const OldListingsScreen()),
    );
    await tester.pump();
    await tester.pump();

    expect(find.text('No old listings'), findsOneWidget);
  });

  testWidgets('pulling to refresh from the empty state re-fetches and can surface new listings', (tester) async {
    // Regression test for a real bug: RefreshIndicator previously only
    // wrapped the non-empty ListView.builder branch, so pulling down
    // while "No old listings" was showing did nothing at all -- a
    // listing that had since aged into "old" stayed invisible until the
    // user left the screen and came back. This drags on the empty
    // state specifically and asserts the list updates in place.
    var callCount = 0;
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path != '/api/discover_jobs/old') {
        return http.Response(jsonEncode([]), 200);
      }
      callCount += 1;
      return http.Response(jsonEncode(callCount == 1 ? [] : expiredJobs), 200);
    });

    await tester.pumpWidget(
      MaterialApp(theme: AppTheme.light(), home: const OldListingsScreen()),
    );
    await tester.pump();
    await tester.pump();

    expect(find.text('No old listings'), findsOneWidget);
    expect(callCount, 1);

    await tester.fling(find.byType(RefreshIndicator), const Offset(0, 300), 1000);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();

    expect(callCount, greaterThan(1));
    expect(find.text('Expired Cashier Role'), findsOneWidget);
    expect(find.text('No old listings'), findsNothing);
  });

  testWidgets('tapping an old listing opens DiscoverJobDetailScreen', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode(expiredJobs), 200);
    });

    await tester.pumpWidget(
      MaterialApp(theme: AppTheme.light(), home: const OldListingsScreen()),
    );
    await tester.pump();
    await tester.pump();

    await tester.tap(find.text('Expired Cashier Role'));
    await tester.pumpAndSettle();

    expect(find.byType(DiscoverJobDetailScreen), findsOneWidget);
    expect(find.text('Expired'), findsOneWidget);
  });
}
