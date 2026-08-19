// Coverage for MessagesScreen's Socket.IO live-update wiring (mirrors
// JobScreenState._connectSocket -- see that screen's own docstring).
// Previously a user with a thread open had no way to see a new message
// arrive short of manually tapping Refresh.
//
// A real Socket.IO server isn't available under flutter_test, so
// MessagesScreen.testMessageEvents (a test-only seam, see its docstring
// in lib/screens/messages_screen.dart) stands in for the real connection
// -- feeding a plain event map through it exercises exactly the same
// _handleIncomingMessageEvent code path a genuine `message_created`
// socket event would.

import 'dart:async';
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

  testWidgets(
    'an incoming message_created event for this thread refreshes and shows the new message',
    (tester) async {
      final serverMessages = <Map<String, dynamic>>[
        messagePayload(id: 1, senderType: 'employer', body: 'Are you still interested?'),
      ];
      ApiClient.testClient = MockClient(
        (request) async => threadResponse(serverMessages),
      );

      final eventsController = StreamController<dynamic>();
      addTearDown(eventsController.close);

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: MessagesScreen(
            applicationId: 1,
            testMessageEvents: eventsController.stream,
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Are you still interested?'), findsOneWidget);
      expect(find.text('Yes, still very interested!'), findsNothing);

      // The message lands server-side first (as a real push would), then
      // the socket event announcing it arrives.
      serverMessages.add(
        messagePayload(id: 2, senderType: 'user', body: 'Yes, still very interested!'),
      );
      eventsController.add({"application_id": 1, "id": 2});
      await tester.pumpAndSettle();

      expect(find.text('Yes, still very interested!'), findsOneWidget);
    },
  );

  testWidgets(
    'a message_created event for a different application is ignored',
    (tester) async {
      var fetchCount = 0;
      final serverMessages = <Map<String, dynamic>>[
        messagePayload(id: 1, senderType: 'employer', body: 'Hello'),
      ];
      ApiClient.testClient = MockClient((request) async {
        fetchCount += 1;
        return threadResponse(serverMessages);
      });

      final eventsController = StreamController<dynamic>();
      addTearDown(eventsController.close);

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: MessagesScreen(
            applicationId: 1,
            testMessageEvents: eventsController.stream,
          ),
        ),
      );
      await tester.pumpAndSettle();
      final countAfterInitialLoad = fetchCount;

      eventsController.add({"application_id": 999, "id": 42});
      await tester.pumpAndSettle();

      expect(fetchCount, countAfterInitialLoad);
    },
  );
}
