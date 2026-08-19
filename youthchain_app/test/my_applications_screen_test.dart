// Widget coverage for MyApplicationsScreen -- fetches
// GET /my_applications/<id> via ApiClient.testClient, the same seam every
// other screen's tests use rather than a real network call. Previously
// untested; added alongside the Krio localization pass for this screen.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:youthchain_app/l10n/app_localizations.dart';
import 'package:youthchain_app/l10n/kri_material_fallback.dart';
import 'package:youthchain_app/screens/my_applications_screen.dart';
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

  Future<void> pumpScreen(WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const MyApplicationsScreen(userId: 1),
      ),
    );
    await tester.pump(); // build
    await tester.pump(); // resolve the async fetch
  }

  testWidgets('renders every application returned by the API', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/my_applications/1') {
        return http.Response(
          jsonEncode([
            {
              "id": 10,
              "job_id": 1,
              "job_title": "Solar Field Technician",
              "job_location": "Freetown",
              "job_duration": "6 months",
              "status": "Pending",
              "cv_file": "cv.pdf",
            },
          ]),
          200,
        );
      }
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);

    expect(find.text('Solar Field Technician'), findsOneWidget);
    expect(find.text('Freetown'), findsOneWidget);
    expect(find.text('6 months'), findsOneWidget);
  });

  testWidgets('shows an empty state when there are no applications', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);

    expect(find.text('No applications yet'), findsOneWidget);
  });

  testWidgets('falls back to "Job ID: <n>" when the job title is missing', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/my_applications/1') {
        return http.Response(
          jsonEncode([
            {"id": 11, "job_id": 42, "status": "Pending"},
          ]),
          200,
        );
      }
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);

    expect(find.text('Job ID: 42'), findsOneWidget);
  });

  testWidgets('offers a rate-employer action on a completed gig application', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/my_applications/1') {
        return http.Response(
          jsonEncode([
            {
              "id": 12,
              "job_id": 2,
              "job_title": "Cleaner Needed",
              "status": "completed",
              "can_rate_employer": true,
            },
          ]),
          200,
        );
      }
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);

    expect(find.text('Rate this employer'), findsOneWidget);
  });

  testWidgets('shows the submitted rating and a dispute action once rated', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/my_applications/1') {
        return http.Response(
          jsonEncode([
            {
              "id": 13,
              "job_id": 3,
              "job_title": "Market Vendor Needed",
              "status": "completed",
              "can_rate_employer": false,
              "employer_rating": {"id": 99, "score": 5},
            },
          ]),
          200,
        );
      }
      return http.Response(jsonEncode([]), 200);
    });

    await pumpScreen(tester);

    expect(find.text('You rated: 5/5'), findsOneWidget);
    expect(find.text('Dispute'), findsOneWidget);
  });
}
