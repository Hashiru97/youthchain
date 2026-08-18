// Regression coverage for NotificationsScreen -- previously untested.
// Covers the pre-existing behavior (list render, mark-as-read, direct
// navigation for "new_message") plus the new detail-view behavior added
// alongside the app-bar tidy-up (BL-48): tapping any other notification
// type now opens a bottom sheet with the full title/body/timestamp
// instead of doing nothing further.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/screens/notifications_screen.dart';
import 'package:youthchain_app/services/api_client.dart';
import 'package:youthchain_app/theme/app_theme.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // Without this, ApiClient.get()'s token read hangs forever waiting on a
  // real platform channel that doesn't exist in the test environment --
  // the actual root cause of this file's earlier "pumpAndSettle timed
  // out" / "widget not found" flakiness (every other test file mocks
  // this; this one just forgot to).
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

  Map<String, dynamic> buildNotification({
    required int id,
    required String type,
    required String title,
    String? body,
    bool read = false,
    Map<String, dynamic>? meta,
  }) {
    return {
      'id': id,
      'type': type,
      'title': title,
      'body': body,
      'read': read,
      'created_at': '2026-08-12T09:30:00',
      'meta': meta ?? {},
    };
  }

  testWidgets('renders notifications fetched from the API', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/api/notifications') {
        return http.Response(
          jsonEncode({
            'notifications': [
              buildNotification(id: 1, type: 'application_status_changed', title: 'Application accepted', body: 'Your application was accepted.'),
            ],
            'unread_count': 1,
          }),
          200,
        );
      }
      return http.Response('not found', 404);
    });

    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: NotificationsScreen()));
    await tester.pumpAndSettle();

    expect(find.text('Application accepted'), findsOneWidget);
    expect(find.text('Your application was accepted.'), findsOneWidget);
  });

  testWidgets('shows the empty state when there are no notifications', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode({'notifications': [], 'unread_count': 0}), 200);
    });

    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: NotificationsScreen()));
    await tester.pumpAndSettle();

    expect(find.text('No notifications yet'), findsOneWidget);
  });

  testWidgets('tapping an unread notification marks it read via the API', (tester) async {
    final readCalls = <String>[];
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/api/notifications') {
        return http.Response(
          jsonEncode({
            'notifications': [
              buildNotification(id: 7, type: 'worker_rated', title: 'You were rated', body: 'You received a 5-star rating.'),
            ],
            'unread_count': 1,
          }),
          200,
        );
      }
      if (request.url.path == '/api/notifications/7/read') {
        readCalls.add(request.url.path);
        return http.Response(jsonEncode({'success': true}), 200);
      }
      return http.Response('not found', 404);
    });

    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: NotificationsScreen()));
    await tester.pumpAndSettle();

    await tester.tap(find.text('You were rated'));
    await tester.pumpAndSettle();

    expect(readCalls, ['/api/notifications/7/read']);
  });

  testWidgets('tapping a non-message notification opens a detail view with the full body', (tester) async {
    const longBody =
        'This is a much longer notification body than would comfortably fit '
        'inline in the list row, describing exactly what changed and why, '
        'so the detail view needs to show all of it, not just a snippet.';

    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/api/notifications') {
        return http.Response(
          jsonEncode({
            'notifications': [
              buildNotification(id: 3, type: 'application_marked_complete', title: 'Gig marked complete', body: longBody),
            ],
            'unread_count': 1,
          }),
          200,
        );
      }
      if (request.url.path == '/api/notifications/3/read') {
        return http.Response(jsonEncode({'success': true}), 200);
      }
      return http.Response('not found', 404);
    });

    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: NotificationsScreen()));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Gig marked complete'));
    await tester.pumpAndSettle();

    // The detail sheet renders the full body text and a timestamp -- the
    // list row behind it already showed the same untruncated body too
    // (this screen never truncates), so two matches is correct here, not
    // a duplicate-rendering bug.
    expect(find.text(longBody), findsNWidgets(2));
    expect(find.textContaining('Aug 12, 2026'), findsOneWidget);
  });

  testWidgets('tapping a new_message notification navigates directly, without a detail sheet', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/api/notifications') {
        return http.Response(
          jsonEncode({
            'notifications': [
              buildNotification(
                id: 9, type: 'new_message', title: 'New message', body: 'Hi, are you available?',
                meta: {'application_id': 55},
              ),
            ],
            'unread_count': 1,
          }),
          200,
        );
      }
      if (request.url.path == '/api/notifications/9/read') {
        return http.Response(jsonEncode({'success': true}), 200);
      }
      if (request.url.path == '/api/application/55/messages') {
        return http.Response(jsonEncode({'messages': []}), 200);
      }
      return http.Response('not found', 404);
    });

    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: NotificationsScreen()));
    await tester.pumpAndSettle();

    await tester.tap(find.text('New message'));
    await tester.pumpAndSettle();

    // Navigated to MessagesScreen's AppBar title, not stuck showing a
    // detail sheet on top of the list.
    expect(find.text('New message'), findsNothing);
  });
}
