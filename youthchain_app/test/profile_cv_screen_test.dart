// Real widget coverage for the "Preferred Industries" section added
// alongside the employer-industry/matching-bonus work on the backend.
// Exercises both GET /api/industries (chip options) and GET/POST
// /api/candidate (existing preference pre-selection, and what actually
// gets submitted) via ApiClient.testClient.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/screens/profile_cv_screen.dart';
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

  http.Response route(
    http.Request request, {
    required Map<String, dynamic> candidate,
  }) {
    if (request.url.path == '/api/industries') {
      return http.Response(
        jsonEncode({
          "success": true,
          "industries": ["Agriculture", "Fisheries", "Healthcare"],
        }),
        200,
      );
    }
    if (request.url.path == '/api/candidate/me') {
      return http.Response(
        jsonEncode({"success": true, "candidate": candidate}),
        200,
      );
    }
    return http.Response(
      jsonEncode({"success": false, "error": "not found"}),
      404,
    );
  }

  testWidgets('renders an industry chip per option fetched from the backend', (
    tester,
  ) async {
    ApiClient.testClient = MockClient(
      (request) async => route(request, candidate: {}),
    );

    await tester.pumpWidget(
      MaterialApp(theme: AppTheme.light(), home: ProfileCvScreen(userId: 1)),
    );
    await tester.pumpAndSettle();

    expect(find.text('Preferred Industries (optional)'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Agriculture'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Fisheries'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Healthcare'), findsOneWidget);
  });

  testWidgets(
    'pre-selects chips from the candidate\'s existing preferred_industries',
    (tester) async {
      ApiClient.testClient = MockClient(
        (request) async => route(
          request,
          candidate: {
            "name": "Zeus",
            "email": "zeus@test.com",
            "preferred_industries": "Agriculture,Fisheries",
          },
        ),
      );

      await tester.pumpWidget(
        MaterialApp(theme: AppTheme.light(), home: ProfileCvScreen(userId: 1)),
      );
      await tester.pumpAndSettle();

      final agriculture = tester.widget<FilterChip>(
        find.widgetWithText(FilterChip, 'Agriculture'),
      );
      final fisheries = tester.widget<FilterChip>(
        find.widgetWithText(FilterChip, 'Fisheries'),
      );
      final healthcare = tester.widget<FilterChip>(
        find.widgetWithText(FilterChip, 'Healthcare'),
      );
      expect(agriculture.selected, isTrue);
      expect(fisheries.selected, isTrue);
      expect(healthcare.selected, isFalse);
    },
  );

  testWidgets('tapping a chip toggles its selection', (tester) async {
    ApiClient.testClient = MockClient(
      (request) async => route(request, candidate: {}),
    );

    await tester.pumpWidget(
      MaterialApp(theme: AppTheme.light(), home: ProfileCvScreen(userId: 1)),
    );
    await tester.pumpAndSettle();

    expect(
      tester
          .widget<FilterChip>(find.widgetWithText(FilterChip, 'Healthcare'))
          .selected,
      isFalse,
    );

    await tester.ensureVisible(find.widgetWithText(FilterChip, 'Healthcare'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilterChip, 'Healthcare'));
    await tester.pump();

    expect(
      tester
          .widget<FilterChip>(find.widgetWithText(FilterChip, 'Healthcare'))
          .selected,
      isTrue,
    );
  });

  testWidgets(
    'saving the profile submits exactly the currently-selected industries',
    (tester) async {
      http.Request? capturedSave;
      ApiClient.testClient = MockClient((request) async {
        if (request.method == 'POST' && request.url.path == '/api/candidate') {
          capturedSave = request;
          return http.Response(
            jsonEncode({"success": true, "candidate_id": 7}),
            200,
          );
        }
        return route(request, candidate: {"preferred_industries": "Fisheries"});
      });

      await tester.pumpWidget(
        MaterialApp(theme: AppTheme.light(), home: ProfileCvScreen(userId: 1)),
      );
      await tester.pumpAndSettle();

      // Starts pre-selected with Fisheries only; add Healthcare, remove
      // nothing -- the save must reflect exactly {Fisheries, Healthcare}.
      await tester.ensureVisible(find.widgetWithText(FilterChip, 'Healthcare'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilterChip, 'Healthcare'));
      await tester.pump();

      // Name and email are both required by the form's validators -- leaving
      // either blank makes validate() fail silently and the save never even
      // reaches postJson, which is exactly what happened before this fix.
      await tester.enterText(find.byType(TextField).at(0), 'Zeus');
      await tester.enterText(find.byType(TextField).at(1), 'zeus@test.com');
      await tester.ensureVisible(find.text('Save Profile'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save Profile'));
      await tester.pumpAndSettle();

      expect(capturedSave, isNotNull);
      final body = jsonDecode(capturedSave!.body) as Map;
      final submitted = Set<String>.from(body['preferred_industries'] as List);
      expect(submitted, {'Fisheries', 'Healthcare'});
    },
  );
}
