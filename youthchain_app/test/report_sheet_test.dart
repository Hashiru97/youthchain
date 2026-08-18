// Direct coverage for showReportSheet()'s dual-mode branching --
// employer reports (existing, POST /api/report_employer) vs Discover-
// listing reports (new, POST /api/report_listing). Previously only
// exercised indirectly through JobDetailScreen/MessagesScreen; this
// file targets the sheet itself, especially the new scrapedJobId path
// those screens don't (yet) call into.

import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/services/api_client.dart';
import 'package:youthchain_app/theme/app_theme.dart';
import 'package:youthchain_app/widgets/report_sheet.dart';

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

  Widget wrap(VoidCallback onPressed) => MaterialApp(
        theme: AppTheme.light(),
        home: Scaffold(
          body: Builder(
            builder: (context) => ElevatedButton(onPressed: onPressed, child: const Text('open')),
          ),
        ),
      );

  testWidgets('scrapedJobId posts to /api/report_listing with job_id, not employer_id', (tester) async {
    Map<String, dynamic>? body;
    String? path;
    ApiClient.testClient = MockClient((request) async {
      path = request.url.path;
      body = jsonDecode(request.body) as Map<String, dynamic>;
      return http.Response(jsonEncode({"success": true, "report_id": 1}), 201);
    });

    late BuildContext ctx;
    await tester.pumpWidget(wrap(() {}));
    ctx = tester.element(find.byType(ElevatedButton));
    unawaited(showReportSheet(ctx, scrapedJobId: 42));
    await tester.pumpAndSettle();

    expect(find.text('Report this listing'), findsOneWidget);
    await tester.tap(find.text('Submit report'));
    await tester.pumpAndSettle();

    expect(path, '/api/report_listing');
    expect(body!['job_id'], 42);
    expect(body!.containsKey('employer_id'), isFalse);
    expect(body!['category'], 'scam');
  });

  testWidgets('employerId posts to /api/report_employer, unaffected by the new path', (tester) async {
    Map<String, dynamic>? body;
    String? path;
    ApiClient.testClient = MockClient((request) async {
      path = request.url.path;
      body = jsonDecode(request.body) as Map<String, dynamic>;
      return http.Response(jsonEncode({"success": true, "report_id": 1}), 201);
    });

    await tester.pumpWidget(wrap(() {}));
    final ctx = tester.element(find.byType(ElevatedButton));
    unawaited(showReportSheet(ctx, employerId: 7, jobId: 9));
    await tester.pumpAndSettle();

    expect(find.text('Report this employer'), findsOneWidget);
    await tester.tap(find.text('Submit report'));
    await tester.pumpAndSettle();

    expect(path, '/api/report_employer');
    expect(body!['employer_id'], 7);
    expect(body!['job_id'], 9);
  });

  testWidgets('a listing report includes details when provided', (tester) async {
    Map<String, dynamic>? body;
    ApiClient.testClient = MockClient((request) async {
      body = jsonDecode(request.body) as Map<String, dynamic>;
      return http.Response(jsonEncode({"success": true, "report_id": 1}), 201);
    });

    await tester.pumpWidget(wrap(() {}));
    final ctx = tester.element(find.byType(ElevatedButton));
    unawaited(showReportSheet(ctx, scrapedJobId: 42));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'Asked me to pay a registration fee upfront');
    await tester.tap(find.text('Submit report'));
    await tester.pumpAndSettle();

    expect(body!['details'], 'Asked me to pay a registration fee upfront');
  });

  testWidgets('a listing report shows the rate-limit error on 429', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode({"error": "Too many reports. Try again later."}), 429);
    });

    await tester.pumpWidget(wrap(() {}));
    final ctx = tester.element(find.byType(ElevatedButton));
    unawaited(showReportSheet(ctx, scrapedJobId: 42));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Submit report'));
    await tester.pumpAndSettle();

    expect(find.text('Too many reports. Try again later.'), findsOneWidget);
  });
}
