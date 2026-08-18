// Real widget coverage for showRateEmployerSheet -- the worker's half of
// the bidirectional gig rating (POST /api/applications/<id>/rate_employer,
// see the Rating model's docstring in app.py).

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/services/api_client.dart';
import 'package:youthchain_app/theme/app_theme.dart';
import 'package:youthchain_app/widgets/rate_employer_sheet.dart';

class _Host extends StatelessWidget {
  const _Host();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Builder(
        builder: (context) => ElevatedButton(
          onPressed: () => showRateEmployerSheet(context, applicationId: 7),
          child: const Text('Open'),
        ),
      ),
    );
  }
}

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

  testWidgets('requires a star rating before submitting', (tester) async {
    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: _Host()));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Submit rating'));
    await tester.pump();

    expect(find.text('Choose a star rating.'), findsOneWidget);
  });

  testWidgets('submitting posts the chosen score and optional comment', (
    tester,
  ) async {
    http.Request? captured;
    ApiClient.testClient = MockClient((request) async {
      captured = request;
      return http.Response(jsonEncode({"success": true}), 201);
    });

    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: _Host()));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();

    // Tap the 4th star (index 3) to pick a 4/5 rating.
    final stars = find.byIcon(Icons.star_outline_rounded);
    await tester.tap(stars.at(3));
    await tester.pump();

    await tester.enterText(find.byType(TextField), 'Paid on time');
    await tester.tap(find.text('Submit rating'));
    await tester.pumpAndSettle();

    expect(captured, isNotNull);
    expect(captured!.url.path, '/api/applications/7/rate_employer');
    final body = jsonDecode(captured!.body) as Map;
    expect(body['score'], 4);
    expect(body['comment'], 'Paid on time');

    // Sheet closed after a successful submit.
    expect(find.text('Rate this employer'), findsNothing);
  });

  testWidgets('shows the server error on failure and keeps the sheet open', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(
        jsonEncode({"success": false, "error": "You have already rated this employer"}),
        403,
      );
    });

    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: _Host()));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();

    final stars = find.byIcon(Icons.star_outline_rounded);
    await tester.tap(stars.at(0));
    await tester.pump();
    await tester.tap(find.text('Submit rating'));
    await tester.pumpAndSettle();

    expect(find.text('You have already rated this employer'), findsOneWidget);
    expect(find.text('Rate this employer'), findsOneWidget);
  });
}
