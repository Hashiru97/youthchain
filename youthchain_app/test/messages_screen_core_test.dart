// Core coverage for MessagesScreen's message list -- previously the only
// coverage this screen had was messages_screen_report_test.dart, which
// only exercises the report-a-message flow. This file covers the actual
// thread: rendering fetched messages from both parties, sending a new
// one, and the manual refresh action.

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

  Map<String, dynamic> messagePayload({
    required int id,
    required String senderType,
    required String body,
  }) {
    return {
      "id": id,
      "application_id": 1,
      "sender_type": senderType,
      "body": body,
      "created_at": "2026-08-06T00:00:00",
      "read": true,
      "read_at": null,
      "attachment_file": null,
    };
  }

  http.Response threadResponse(List<Map<String, dynamic>> messages) {
    return http.Response(
      jsonEncode({
        "success": true,
        "messages": messages,
        "employer": {
          "name": "Sierra Solar Co",
          "verification_status": "verified",
          "industry": "Energy & Utilities",
        },
        "employer_id": 2,
      }),
      200,
    );
  }

  testWidgets('renders messages from both the employer and the applicant', (
    tester,
  ) async {
    ApiClient.testClient = MockClient(
      (request) async => threadResponse([
        messagePayload(id: 1, senderType: 'employer', body: 'Hi, are you available?'),
        messagePayload(id: 2, senderType: 'user', body: 'Yes, ready to start.'),
      ]),
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

    expect(find.text('Hi, are you available?'), findsOneWidget);
    expect(find.text('Yes, ready to start.'), findsOneWidget);
  });

  testWidgets('sending a message posts it and shows it in the thread', (
    tester,
  ) async {
    final serverMessages = <Map<String, dynamic>>[
      messagePayload(id: 1, senderType: 'employer', body: 'Hi there'),
    ];
    http.Request? posted;
    ApiClient.testClient = MockClient((request) async {
      if (request.method == 'POST' &&
          request.url.path == '/api/application/1/messages') {
        posted = request;
        final body = jsonDecode(request.body) as Map;
        serverMessages.add(
          messagePayload(id: 2, senderType: 'user', body: body['body'] as String),
        );
        return http.Response(
          jsonEncode({"success": true, "message": serverMessages.last}),
          201,
        );
      }
      return threadResponse(serverMessages);
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

    expect(find.text('Hi there'), findsOneWidget);
    expect(find.text('Thanks, I can start Monday'), findsNothing);

    await tester.enterText(find.byType(TextField), 'Thanks, I can start Monday');
    await tester.tap(find.byIcon(Icons.send_rounded));
    await tester.pumpAndSettle();

    expect(posted, isNotNull);
    final sentBody = jsonDecode(posted!.body) as Map;
    expect(sentBody['body'], 'Thanks, I can start Monday');
    expect(find.text('Thanks, I can start Monday'), findsOneWidget);
    // The composer clears after a successful send.
    expect(find.widgetWithText(TextField, 'Thanks, I can start Monday'), findsNothing);
  });

  testWidgets('the refresh action re-fetches and shows newly arrived messages', (
    tester,
  ) async {
    final serverMessages = <Map<String, dynamic>>[
      messagePayload(id: 1, senderType: 'employer', body: 'First message'),
    ];
    ApiClient.testClient = MockClient(
      (request) async => threadResponse(serverMessages),
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

    expect(find.text('First message'), findsOneWidget);
    expect(find.text('A new one arrived'), findsNothing);

    // Simulate a new message landing server-side between fetches (what a
    // socket-less client relies on manual refresh to pick up).
    serverMessages.add(
      messagePayload(id: 2, senderType: 'employer', body: 'A new one arrived'),
    );

    await tester.tap(find.byTooltip('Refresh'));
    await tester.pumpAndSettle();

    expect(find.text('A new one arrived'), findsOneWidget);
  });
}
