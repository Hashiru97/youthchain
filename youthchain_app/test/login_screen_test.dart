// Minimal coverage for the "Log in with a code" entry point added to
// LoginScreen alongside LoginOtpScreen (see that file's own tests for the
// actual OTP flow) -- this just proves the link is wired to the right
// route, not a full regression suite for the pre-existing password login
// form.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:youthchain_app/l10n/app_localizations.dart';
import 'package:youthchain_app/l10n/kri_material_fallback.dart';
import 'package:youthchain_app/screens/login_screen.dart';
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

  testWidgets('"Log in with a code" opens LoginOtpScreen', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const LoginScreen(),
        routes: {'/login-with-code': (_) => const LoginOtpScreen()},
      ),
    );

    await tester.tap(find.text('Log in with a code'));
    await tester.pumpAndSettle();

    expect(find.byType(LoginOtpScreen), findsOneWidget);
  });
}
