// Regression coverage for ForgotPasswordScreen's 3-step flow (contact ->
// code -> confirm), mirroring registration_screen_test.dart's structure
// and the api_client_cache_test.dart MockClient seam.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/screens/forgot_password_screen.dart';
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

  testWidgets('renders the contact step by default', (WidgetTester tester) async {
    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: ForgotPasswordScreen()));

    expect(find.text('Reset your password'), findsOneWidget);
    expect(find.text('Where should we send your reset code?'), findsOneWidget);
    expect(find.text('Email'), findsOneWidget);
    expect(find.text('Phone (SMS)'), findsOneWidget);
    expect(find.text('Send reset code'), findsOneWidget);
  });

  testWidgets('shows a validation error when sending with an empty identifier', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: ForgotPasswordScreen()));

    await tester.tap(find.text('Send reset code'));
    await tester.pump();

    expect(find.textContaining('enter your email'), findsOneWidget);
  });

  testWidgets('choosing Phone (SMS) switches the identifier field to a phone number', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: ForgotPasswordScreen()));

    await tester.tap(find.text('Phone (SMS)'));
    await tester.pump();

    expect(find.text('Phone Number'), findsOneWidget);
    expect(find.text('Email Address'), findsNothing);
  });

  testWidgets('full happy path: contact -> code -> confirm -> success', (
    WidgetTester tester,
  ) async {
    final requests = <http.Request>[];
    ApiClient.testClient = MockClient((request) async {
      requests.add(request);
      if (request.url.path == '/auth/otp/reset/request') {
        return http.Response(jsonEncode({'message': 'sent'}), 200);
      }
      if (request.url.path == '/auth/otp/reset/verify') {
        return http.Response(jsonEncode({'success': true}), 200);
      }
      if (request.url.path == '/auth/otp/reset/confirm') {
        return http.Response(jsonEncode({'message': 'reset ok'}), 200);
      }
      return http.Response('not found', 404);
    });

    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: ForgotPasswordScreen()));

    // Step 1: contact.
    await tester.enterText(find.byType(TextField).first, 'forgetful@test.com');
    await tester.tap(find.text('Send reset code'));
    await tester.pumpAndSettle();

    final resetRequest = requests.firstWhere((r) => r.url.path == '/auth/otp/reset/request');
    final resetBody = jsonDecode(resetRequest.body) as Map;
    expect(resetBody['channel'], 'email');
    expect(resetBody['identifier'], 'forgetful@test.com');

    // Step 2: code.
    expect(find.textContaining('forgetful@test.com'), findsOneWidget);
    await tester.enterText(find.byType(TextField).first, '654321');
    await tester.tap(find.text('Verify'));
    await tester.pumpAndSettle();

    final verifyRequest = requests.firstWhere((r) => r.url.path == '/auth/otp/reset/verify');
    expect((jsonDecode(verifyRequest.body) as Map)['code'], '654321');

    // Step 3: confirm.
    expect(find.textContaining('is verified'), findsOneWidget);
    await tester.enterText(find.widgetWithText(TextField, 'New Password'), 'BrandNewPassw0rd!');
    await tester.enterText(find.widgetWithText(TextField, 'Confirm New Password'), 'BrandNewPassw0rd!');
    await tester.tap(find.text('Change Password'));
    await tester.pumpAndSettle();

    final confirmRequest = requests.firstWhere((r) => r.url.path == '/auth/otp/reset/confirm');
    final confirmBody = jsonDecode(confirmRequest.body) as Map;
    expect(confirmBody['identifier'], 'forgetful@test.com');
    expect(confirmBody['code'], '654321');
    expect(confirmBody['new_password'], 'BrandNewPassw0rd!');

    expect(find.textContaining('Your password was changed'), findsOneWidget);
    expect(find.text('Back to login'), findsOneWidget);
  });

  testWidgets('wrong code on step 2 shows an error and stays on step 2', (
    WidgetTester tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/auth/otp/reset/request') {
        return http.Response(jsonEncode({'message': 'sent'}), 200);
      }
      if (request.url.path == '/auth/otp/reset/verify') {
        return http.Response(jsonEncode({'success': false, 'error': 'Invalid or expired code'}), 400);
      }
      return http.Response('not found', 404);
    });

    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: ForgotPasswordScreen()));

    await tester.enterText(find.byType(TextField).first, 'wrongcode@test.com');
    await tester.tap(find.text('Send reset code'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, '000000');
    await tester.tap(find.text('Verify'));
    await tester.pumpAndSettle();

    expect(find.text('Invalid or expired code'), findsOneWidget);
    expect(find.text('Verify'), findsOneWidget); // still on the code step
  });

  testWidgets('mismatched passwords on the confirm step show an error', (
    WidgetTester tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      if (request.url.path == '/auth/otp/reset/request') {
        return http.Response(jsonEncode({'message': 'sent'}), 200);
      }
      if (request.url.path == '/auth/otp/reset/verify') {
        return http.Response(jsonEncode({'success': true}), 200);
      }
      return http.Response('not found', 404);
    });

    await tester.pumpWidget(MaterialApp(theme: AppTheme.light(), home: ForgotPasswordScreen()));
    await tester.enterText(find.byType(TextField).first, 'mismatch@test.com');
    await tester.tap(find.text('Send reset code'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, '123456');
    await tester.tap(find.text('Verify'));
    await tester.pumpAndSettle();

    await tester.enterText(find.widgetWithText(TextField, 'New Password'), 'FirstPassword1!');
    await tester.enterText(find.widgetWithText(TextField, 'Confirm New Password'), 'SecondPassword1!');
    await tester.tap(find.text('Change Password'));
    await tester.pump();

    expect(find.text('Passwords do not match'), findsOneWidget);
  });
}
