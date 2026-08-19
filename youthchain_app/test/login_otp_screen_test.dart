// Regression coverage for LoginOtpScreen's 2-step (contact -> code)
// passwordless login flow -- mirrors ForgotPasswordScreen's own
// request/verify pair (widget_test.dart's RegistrationScreen tests use
// the same testClient pattern) but ends in a real session instead of a
// password change.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/l10n/app_localizations.dart';
import 'package:youthchain_app/l10n/kri_material_fallback.dart';
import 'package:youthchain_app/screens/login_otp_screen.dart';
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

  testWidgets('renders the contact step with SMS selected by default', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const LoginOtpScreen(),
      ),
    );

    expect(find.text('Log in with a code'), findsOneWidget);
    expect(find.text('Phone Number'), findsOneWidget);
    expect(find.text('Email Address'), findsNothing);
    expect(find.text('Send code'), findsOneWidget);
  });

  testWidgets('choosing Email switches the identifier field', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const LoginOtpScreen(),
      ),
    );

    await tester.tap(find.text('Email'));
    await tester.pump();

    expect(find.text('Email Address'), findsOneWidget);
    expect(find.text('Phone Number'), findsNothing);
  });

  testWidgets(
    'shows a validation error when sending a code with an empty identifier',
    (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const LoginOtpScreen(),
        ),
      );

      await tester.tap(find.text('Send code'));
      await tester.pump();

      expect(find.textContaining('enter your phone number'), findsOneWidget);
    },
  );

  testWidgets('full happy path: contact -> code -> logged in', (tester) async {
    final requests = <http.Request>[];
    ApiClient.testClient = MockClient((request) async {
      requests.add(request);
      if (request.url.path == '/auth/otp/request') {
        return http.Response(jsonEncode({'message': 'sent'}), 200);
      }
      if (request.url.path == '/auth/otp/verify') {
        return http.Response(
          jsonEncode({
            'user': {'id': 42},
            'access_token': 'fake-token',
          }),
          200,
        );
      }
      return http.Response('not found', 404);
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const LoginOtpScreen(),
        routes: {'/home': (_) => const Scaffold(body: Text('Home Screen'))},
      ),
    );

    // Step 1: contact (SMS is the default channel here).
    await tester.enterText(find.byType(TextField).first, '23279001199');
    await tester.tap(find.text('Send code'));
    await tester.pumpAndSettle();

    final otpRequest = requests.firstWhere(
      (r) => r.url.path == '/auth/otp/request',
    );
    final otpBody = jsonDecode(otpRequest.body) as Map;
    expect(otpBody['channel'], 'sms');
    expect(otpBody['identifier'], '23279001199');

    // Step 2: code.
    expect(
      find.textContaining('If an account exists for 23279001199'),
      findsOneWidget,
    );
    await tester.enterText(find.byType(TextField).first, '123456');
    await tester.tap(find.text('Log in'));
    await tester.pumpAndSettle();

    final verifyRequest = requests.firstWhere(
      (r) => r.url.path == '/auth/otp/verify',
    );
    final verifyBody = jsonDecode(verifyRequest.body) as Map;
    expect(verifyBody['channel'], 'sms');
    expect(verifyBody['identifier'], '23279001199');
    expect(verifyBody['code'], '123456');

    // Landed on home -- session save + navigation both actually happened.
    expect(find.text('Home Screen'), findsOneWidget);
    expect(find.byType(LoginOtpScreen), findsNothing);
  });

  testWidgets(
    'shows the server error and stays on the code step for a wrong code',
    (tester) async {
      ApiClient.testClient = MockClient((request) async {
        if (request.url.path == '/auth/otp/request') {
          return http.Response(jsonEncode({'message': 'sent'}), 200);
        }
        if (request.url.path == '/auth/otp/verify') {
          return http.Response(
            jsonEncode({'success': false, 'error': 'Invalid or expired code'}),
            400,
          );
        }
        return http.Response('not found', 404);
      });

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const LoginOtpScreen(),
        ),
      );

      await tester.enterText(find.byType(TextField).first, '23279001199');
      await tester.tap(find.text('Send code'));
      await tester.pumpAndSettle();

      await tester.enterText(find.byType(TextField).first, '000000');
      await tester.tap(find.text('Log in'));
      await tester.pumpAndSettle();

      expect(find.text('Invalid or expired code'), findsOneWidget);
      expect(find.byType(LoginOtpScreen), findsOneWidget);
    },
  );
}
