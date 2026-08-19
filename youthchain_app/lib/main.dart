import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';
import 'l10n/app_localizations.dart';
import 'l10n/kri_material_fallback.dart';
import 'screens/registration_screen.dart';
import 'screens/login_screen.dart';
import 'screens/login_otp_screen.dart';
import 'screens/forgot_password_screen.dart';
import 'screens/home_shell.dart';
import 'services/api_client.dart';
import 'services/locale_controller.dart';
import 'services/push_notification_service.dart';
import 'services/theme_controller.dart';
import 'theme/app_theme.dart';

/// Lets ApiClient (a plain service class with no BuildContext of its own)
/// trigger navigation when a session expires -- see
/// ApiClient.onSessionExpired's own docstring for why this exists.
final navigatorKey = GlobalKey<NavigatorState>();

void main() async {
  // Must resolve before the first frame -- otherwise the app briefly
  // paints ThemeController's ThemeMode.system default and then jumps to
  // the persisted choice a frame later, the exact flash-of-wrong-theme
  // the web portal's theme_init.js exists to prevent.
  WidgetsFlutterBinding.ensureInitialized();
  await ThemeController.load();
  await LocaleController.load();
  await Firebase.initializeApp();

  ApiClient.onSessionExpired = () {
    navigatorKey.currentState?.pushAndRemoveUntil(
      MaterialPageRoute(
        builder: (_) => const LoginScreen(
          initialMessage: 'Your session has expired. Please log in again.',
        ),
      ),
      (route) => false,
    );
  };
  runApp(const YouthChainApp());
}

/// Resolves whether a valid session already exists (from a previous login)
/// before deciding where the app should open. Previously the app always
/// booted to RegistrationScreen and forced a fresh login every cold start —
/// this is what actually makes ApiClient's persisted token useful.
class AuthGate extends StatelessWidget {
  const AuthGate({super.key});

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<bool>(
      future: ApiClient.instance.hasSession(),
      builder: (context, snapshot) {
        if (snapshot.connectionState != ConnectionState.done) {
          return const Scaffold(
            body: Center(child: CircularProgressIndicator()),
          );
        }
        if (snapshot.data == true) {
          return FutureBuilder<int?>(
            future: ApiClient.instance.getUserId(),
            builder: (context, userIdSnapshot) {
              final userId = userIdSnapshot.data;
              if (userIdSnapshot.connectionState != ConnectionState.done) {
                return const Scaffold(
                  body: Center(child: CircularProgressIndicator()),
                );
              }
              if (userId != null) {
                // Fire-and-forget: a device that was already logged in
                // before this app update, or whose FCM token rotated while
                // the app was closed, needs re-registering too -- not just
                // the fresh-login path in login_screen.dart/
                // registration_screen.dart. Must never block reaching the
                // job list over a permission prompt or a slow network.
                PushNotificationService.registerToken();
                return HomeShell(userId: userId);
              }
              return const RegistrationScreen();
            },
          );
        }
        return const RegistrationScreen();
      },
    );
  }
}

class YouthChainApp extends StatelessWidget {
  const YouthChainApp({super.key});

  @override
  Widget build(BuildContext context) {
    // Rebuilds the whole app on a theme change -- a rare, user-initiated,
    // whole-screen event, so a full-tree rebuild here is the right trade
    // (simple and correct) rather than threading theme state through
    // every individual screen's own state management.
    return ValueListenableBuilder<ThemeMode>(
      valueListenable: ThemeController.mode,
      builder: (context, themeMode, _) {
        return ValueListenableBuilder<Locale?>(
          valueListenable: LocaleController.locale,
          builder: (context, locale, _) {
            return MaterialApp(
              navigatorKey: navigatorKey,
              title: 'YouthChain',
              debugShowCheckedModeBanner: false,
              theme: AppTheme.light(),
              darkTheme: AppTheme.dark(),
              themeMode: themeMode,

              // `null` (LocaleController's own "follow the device" default)
              // is exactly what MaterialApp.locale already expects for "use
              // Flutter's own resolution against supportedLocales" -- no
              // extra plumbing needed for that fallback case.
              locale: locale,
              // See kri_material_fallback.dart's own docstring: this is
              // the one shared delegate list every MaterialApp in this
              // codebase (this one, and every widget test's own) must
              // use, so a locale gap here can never again be invisible
              // to the test suite the way the Krio crash was.
              localizationsDelegates: appLocalizationsDelegates,
              supportedLocales: AppLocalizations.supportedLocales,

              // Resolves an existing session before deciding registration vs. home.
              home: const AuthGate(),

              // Simple named routes
              routes: {
                '/login': (_) => const LoginScreen(),
                '/register': (_) => const RegistrationScreen(),
                '/forgot-password': (_) => const ForgotPasswordScreen(),
                '/login-with-code': (_) => const LoginOtpScreen(),
              },

              // Robust dynamic route for JobScreen
              onGenerateRoute: (settings) {
                if (settings.name == '/home') {
                  final args = settings.arguments;
                  if (args is Map<String, dynamic> && args['userId'] != null) {
                    final int userId = args['userId'] as int;
                    return MaterialPageRoute(
                      builder: (_) => HomeShell(userId: userId),
                    );
                  }

                  // If arguments are missing/bad, fall back to login instead of crashing
                  return MaterialPageRoute(builder: (_) => const LoginScreen());
                }
                return null;
              },
            );
          },
        );
      },
    );
  }
}
