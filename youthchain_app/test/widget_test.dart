// Regression coverage for RegistrationScreen's 3-step flow (contact ->
// code -> details) -- restructured from a single combined form so
// contact verification happens fully before password creation, mirroring
// the web portal's portal_register() flow (see app.py). Uses
// ApiClient.testClient (see api_client_cache_test.dart) to exercise the
// real network calls rather than mocking the widget's own methods.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/screens/registration_screen.dart';
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

  testWidgets('RegistrationScreen renders its title and contact step', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: RegistrationScreen()));

    expect(find.text('Create YouthChain Account'), findsOneWidget);
    expect(find.text('How should we send your verification code?'), findsOneWidget);
    expect(find.text('Email'), findsOneWidget);
    expect(find.text('Phone (SMS)'), findsOneWidget);
    expect(find.text('Email Address'), findsOneWidget);
    expect(find.text('Send verification code'), findsOneWidget);
  });

  testWidgets('shows a validation error when sending a code with an empty identifier', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: RegistrationScreen()));

    await tester.tap(find.text('Send verification code'));
    await tester.pump();

    expect(find.textContaining('enter your email'), findsOneWidget);
  });

  testWidgets('choosing Phone (SMS) switches the identifier field to a phone number', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: RegistrationScreen()));

    await tester.tap(find.text('Phone (SMS)'));
    await tester.pump();

    expect(find.text('Phone Number'), findsOneWidget);
    expect(find.text('Email Address'), findsNothing);
  });

  testWidgets('full happy path: contact -> code -> details -> account created', (
    WidgetTester tester,
  ) async {
    final requests = <http.Request>[];
    ApiClient.testClient = MockClient((request) async {
      requests.add(request);
      if (request.url.path == '/auth/otp/register/request') {
        return http.Response(jsonEncode({'message': 'sent'}), 200);
      }
      if (request.url.path == '/auth/otp/register/verify') {
        return http.Response(jsonEncode({'success': true}), 200);
      }
      if (request.url.path == '/register') {
        return http.Response(
          jsonEncode({
            'user': {'id': 42},
            'access_token': 'fake-token',
          }),
          201,
        );
      }
      return http.Response('not found', 404);
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        home: const RegistrationScreen(),
        routes: {'/home': (_) => const Scaffold(body: Text('Home Screen'))},
      ),
    );

    // Step 1: contact.
    await tester.enterText(find.byType(TextField).first, 'newyouth@test.com');
    await tester.tap(find.text('Send verification code'));
    await tester.pumpAndSettle();

    final otpRequest = requests.firstWhere((r) => r.url.path == '/auth/otp/register/request');
    final otpBody = jsonDecode(otpRequest.body) as Map;
    expect(otpBody['channel'], 'email');
    expect(otpBody['identifier'], 'newyouth@test.com');

    // Step 2: code.
    expect(find.textContaining('A 6-digit code was sent to newyouth@test.com'), findsOneWidget);
    await tester.enterText(find.byType(TextField).first, '123456');
    await tester.tap(find.text('Verify'));
    await tester.pumpAndSettle();

    final verifyRequest = requests.firstWhere((r) => r.url.path == '/auth/otp/register/verify');
    final verifyBody = jsonDecode(verifyRequest.body) as Map;
    expect(verifyBody['code'], '123456');

    // Step 3: details.
    expect(find.textContaining('newyouth@test.com is verified'), findsOneWidget);
    await tester.enterText(find.widgetWithText(TextField, 'First Name'), 'Aminata');
    await tester.enterText(find.widgetWithText(TextField, 'Last Name'), 'Sesay');
    await tester.enterText(find.widgetWithText(TextField, 'Phone Number'), '23276112233');
    await tester.enterText(find.widgetWithText(TextField, 'NCRA ID (optional)'), 'SL-000998877');
    await tester.enterText(find.widgetWithText(TextField, 'Password'), 'StrongPass123!');
    await tester.enterText(find.widgetWithText(TextField, 'Confirm Password'), 'StrongPass123!');
    await tester.ensureVisible(find.byType(Checkbox));
    await tester.pumpAndSettle();
    await tester.tap(find.byType(Checkbox));
    await tester.ensureVisible(find.text('Create Account'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Create Account'));
    await tester.pumpAndSettle();

    final registerRequest = requests.firstWhere((r) => r.url.path == '/register');
    final registerBody = jsonDecode(registerRequest.body) as Map;
    expect(registerBody['first_name'], 'Aminata');
    expect(registerBody['last_name'], 'Sesay');
    expect(registerBody['email'], 'newyouth@test.com');
    expect(registerBody['phone'], '23276112233');
    expect(registerBody['ncra_id'], 'SL-000998877');
    expect(registerBody['channel'], 'email');
    expect(registerBody['otp_code'], '123456');

    expect(find.text('Home Screen'), findsOneWidget);
  });

  testWidgets('wrong code on step 2 shows an error and stays on step 2', (
    WidgetTester tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/auth/otp/register/request') {
        return http.Response(jsonEncode({'message': 'sent'}), 200);
      }
      if (request.url.path == '/auth/otp/register/verify') {
        return http.Response(jsonEncode({'success': false, 'error': 'Invalid or expired code'}), 400);
      }
      return http.Response('not found', 404);
    });

    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: RegistrationScreen()));

    await tester.enterText(find.byType(TextField).first, 'wrongcode@test.com');
    await tester.tap(find.text('Send verification code'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '000000');
    await tester.tap(find.text('Verify'));
    await tester.pumpAndSettle();

    expect(find.text('Invalid or expired code'), findsOneWidget);
    expect(find.text('Verify'), findsOneWidget); // still on the code step
  });

  testWidgets('details step requires all fields before submitting', (
    WidgetTester tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/auth/otp/register/request') {
        return http.Response(jsonEncode({'message': 'sent'}), 200);
      }
      if (request.url.path == '/auth/otp/register/verify') {
        return http.Response(jsonEncode({'success': true}), 200);
      }
      return http.Response('not found', 404);
    });

    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: RegistrationScreen()));
    await tester.enterText(find.byType(TextField).first, 'incomplete@test.com');
    await tester.tap(find.text('Send verification code'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, '123456');
    await tester.tap(find.text('Verify'));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Create Account'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Create Account'));
    await tester.pump();

    expect(find.textContaining('complete all fields'), findsOneWidget);
  });
}
