// Widget coverage for SaveJobButton -- the bookmark toggle shared by
// every job card/detail screen (Home and Discover both). Uses
// ApiClient.testClient (see job_detail_screen_test.dart's own docstring
// for why postJson needed that seam added in the first place).

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/services/api_client.dart';
import 'package:youthchain_app/theme/app_theme.dart';
import 'package:youthchain_app/widgets/save_job_button.dart';

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

  Widget wrap(Widget child) => MaterialApp(theme: AppTheme.light(), home: Scaffold(body: child));

  testWidgets('starts showing the outline icon when not saved', (tester) async {
    await tester.pumpWidget(wrap(const SaveJobButton(jobId: 1, initiallySaved: false)));
    expect(find.byIcon(Icons.bookmark_border_rounded), findsOneWidget);
    expect(find.byIcon(Icons.bookmark_rounded), findsNothing);
  });

  testWidgets('starts showing the filled icon when already saved', (tester) async {
    await tester.pumpWidget(wrap(const SaveJobButton(jobId: 1, initiallySaved: true)));
    expect(find.byIcon(Icons.bookmark_rounded), findsOneWidget);
    expect(find.byIcon(Icons.bookmark_border_rounded), findsNothing);
  });

  testWidgets('tapping an unsaved button posts to the save endpoint and flips the icon', (tester) async {
    String? calledPath;
    ApiClient.testClient = MockClient((request) async {
      calledPath = request.url.path;
      return http.Response(jsonEncode({"success": true, "saved": true}), 200);
    });

    await tester.pumpWidget(wrap(const SaveJobButton(jobId: 42, initiallySaved: false)));
    await tester.tap(find.byType(IconButton));
    await tester.pump();

    expect(calledPath, '/api/jobs/42/save');
    expect(find.byIcon(Icons.bookmark_rounded), findsOneWidget);
  });

  testWidgets('tapping a saved button posts to the unsave endpoint and flips the icon', (tester) async {
    String? calledPath;
    ApiClient.testClient = MockClient((request) async {
      calledPath = request.url.path;
      return http.Response(jsonEncode({"success": true, "saved": false}), 200);
    });

    await tester.pumpWidget(wrap(const SaveJobButton(jobId: 42, initiallySaved: true)));
    await tester.tap(find.byType(IconButton));
    await tester.pump();

    expect(calledPath, '/api/jobs/42/unsave');
    expect(find.byIcon(Icons.bookmark_border_rounded), findsOneWidget);
  });

  testWidgets('reverts to the previous state and shows a snackbar on a failed save', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response('{"error": "server error"}', 500);
    });

    await tester.pumpWidget(wrap(const SaveJobButton(jobId: 42, initiallySaved: false)));
    await tester.tap(find.byType(IconButton));
    await tester.pump(); // the optimistic flip
    await tester.pump(); // the request resolves and reverts

    expect(find.byIcon(Icons.bookmark_border_rounded), findsOneWidget);
    expect(find.textContaining('Could not save'), findsOneWidget);
  });

  testWidgets('an explicit color overrides the default saved/unsaved colors', (tester) async {
    // Real bug found live on device: the default "saved" color is
    // context.colors.primary, which is also this app's global
    // AppBarTheme background -- placed in an AppBar, a saved bookmark
    // was invisible (green icon on a green bar). Both detail screens
    // now force white there; this locks the override itself in.
    await tester.pumpWidget(wrap(const SaveJobButton(jobId: 1, initiallySaved: true, color: Colors.white)));
    final icon = tester.widget<Icon>(find.byIcon(Icons.bookmark_rounded));
    expect(icon.color, Colors.white);
  });

  testWidgets('calls onChanged optimistically, before the network request resolves', (tester) async {
    final changes = <bool>[];
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode({"success": true, "saved": true}), 200);
    });

    await tester.pumpWidget(wrap(SaveJobButton(
      jobId: 1,
      initiallySaved: false,
      onChanged: changes.add,
    )));
    await tester.tap(find.byType(IconButton));
    await tester.pump();

    expect(changes, [true]);
  });
}
