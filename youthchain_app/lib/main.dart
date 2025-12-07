import 'package:flutter/material.dart';
import 'screens/registration_screen.dart';
import 'screens/login_screen.dart';
import 'screens/job_screen.dart';

void main() {
  runApp(const YouthChainApp());
}

class YouthChainApp extends StatelessWidget {
  const YouthChainApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'YouthChain',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        primaryColor: const Color(0xff047857),
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xff047857)),
        scaffoldBackgroundColor: const Color(0xfff1f5f9),
        appBarTheme: const AppBarTheme(elevation: 0, centerTitle: true),
        inputDecorationTheme: InputDecorationTheme(
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),

      // Start on registration
      home: const RegistrationScreen(),

      // Simple named routes
      routes: {
        '/login': (_) => const LoginScreen(),
        '/register': (_) => const RegistrationScreen(),
      },

      // Robust dynamic route for JobScreen
      onGenerateRoute: (settings) {
        if (settings.name == '/home') {
          final args = settings.arguments;
          if (args is Map<String, dynamic> && args['userId'] != null) {
            final int userId = args['userId'] as int;
            return MaterialPageRoute(builder: (_) => JobScreen(userId: userId));
          }

          // If arguments are missing/bad, fall back to login instead of crashing
          return MaterialPageRoute(builder: (_) => const LoginScreen());
        }
        return null;
      },
    );
  }
}
