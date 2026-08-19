// Real widget coverage for the report action added to MessagesScreen after
// the backend started exposing a raw employer_id in the messages API
// response (GET /api/application/{id}/messages) -- this screen's report
// action was originally scoped out entirely because that id wasn't
// available; it is now, closing that gap.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/l10n/app_localizations.dart';
import 'package:youthchain_app/l10n/kri_material_fallback.dart';
import 'package:youthchain_app/screens/messages_screen.dart';
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

  http.Response messagesResponse({required int? employerId}) {
    return http.Response(
      jsonEncode({
        "success": true,
        "messages": [
          {
            "id": 9,
            "application_id": 1,
            "sender_type": "employer",
            "body": "Hi there",
            "created_at": "2026-08-06T00:00:00",
            "read": true,
            "read_at": "2026-08-06T00:01:00",
            "attachment_file": null,
          },
        ],
        "employer": employerId != null
            ? {
                "name": "Sierra Solar Co",
                "verification_status": "verified",
                "industry": "Energy & Utilities",
              }
            : null,
        "employer_id": employerId,
      }),
      200,
    );
  }

  testWidgets('report action appears when the thread has a real employer_id', (
    tester,
  ) async {
    ApiClient.testClient = MockClient(
      (request) async => messagesResponse(employerId: 2),
    );

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: MessagesScreen(applicationId: 1),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byTooltip('Report this employer'), findsOneWidget);
  });

  testWidgets('report action is absent when the thread has no employer_id', (
    tester,
  ) async {
    ApiClient.testClient = MockClient(
      (request) async => messagesResponse(employerId: null),
    );

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: MessagesScreen(applicationId: 1),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byTooltip('Report this employer'), findsNothing);
  });

  testWidgets(
    'submitting from the messages screen posts employer_id and message_id',
    (tester) async {
      http.Request? captured;
      ApiClient.testClient = MockClient((request) async {
        if (request.method == 'POST' &&
            request.url.path == '/api/report_employer') {
          captured = request;
          return http.Response(
            jsonEncode({"success": true, "report_id": 1}),
            201,
          );
        }
        return messagesResponse(employerId: 2);
      });

      await tester.pumpWidget(
        MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: MessagesScreen(applicationId: 1),
      ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.byTooltip('Report this employer'));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Harassment'));
      await tester.pump();
      await tester.tap(find.text('Submit report'));
      await tester.pumpAndSettle();

      expect(captured, isNotNull);
      final body = jsonDecode(captured!.body) as Map;
      expect(body['employer_id'], 2);
      expect(
        body['message_id'],
        9,
      ); // the fetched thread's one (and thus latest) message
      expect(body['category'], 'harassment');
      expect(
        body.containsKey('job_id'),
        isFalse,
      ); // this screen has no job context to send
    },
  );
}
