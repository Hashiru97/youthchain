import 'dart:convert';

import 'package:flutter/material.dart';

import '../services/api_client.dart';
import '../services/push_notification_service.dart';
import '../theme/app_theme.dart';

class LoginScreen extends StatefulWidget {
  // Set when the app routes here because a previously-valid session just
  // got rejected by the server (see ApiClient.onSessionExpired, wired in
  // main.dart) rather than because the user chose to log out — tells them
  // why they're suddenly looking at a login screen instead of leaving them
  // to wonder.
  final String? initialMessage;

  const LoginScreen({super.key, this.initialMessage});

  @override
  LoginScreenState createState() => LoginScreenState();
}

class LoginScreenState extends State<LoginScreen> {
  final phoneOrEmailController = TextEditingController();
  final passwordController = TextEditingController();
  final _formKey = GlobalKey<FormState>();

  bool _loading = false;
  bool _obscurePassword = true;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _errorMessage = widget.initialMessage;
  }

  Map<String, dynamic>? _tryJson(String body) {
    try {
      final v = json.decode(body);
      return v is Map<String, dynamic> ? v : null;
    } catch (_) {
      return null;
    }
  }

  Future<void> loginUser() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;

    setState(() {
      _loading = true;
      _errorMessage = null;
    });

    try {
      final res = await ApiClient.instance.postJson(
        "/login",
        {
          "phone": phoneOrEmailController.text.trim(),
          "email": phoneOrEmailController.text.trim(),
          "password": passwordController.text,
        },
        auth: false,
      );

      final data = _tryJson(res.body);

      if (!mounted) return;

      if (res.statusCode == 200 && data?["user"] != null && data?["access_token"] != null) {
        final userId = data!["user"]["id"] as int;
        await ApiClient.instance.saveSession(
          token: data["access_token"] as String,
          userId: userId,
        );
        PushNotificationService.registerToken();
        if (!mounted) return;
        Navigator.of(context).pushNamedAndRemoveUntil(
          '/home',
          (_) => false,
          arguments: {'userId': userId},
        );
      } else {
        setState(() => _errorMessage = data?["error"] ?? "Login failed. Please try again.");
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _errorMessage = "Network error. Check your connection and try again.");
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  void dispose() {
    phoneOrEmailController.dispose();
    passwordController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Container(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            colors: [context.colors.primaryDark, context.colors.primary],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
        ),
        child: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.lg,
                vertical: AppSpacing.xl,
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  // Wordmark
                  Container(
                    width: 64,
                    height: 64,
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: const Color(0xFFF5F3EE),
                      borderRadius: BorderRadius.circular(AppRadius.md),
                    ),
                    child: Image.asset("assets/img/youthchain_icon.png"),
                  ),
                  const SizedBox(height: AppSpacing.md),
                  const Text(
                    "YouthChain",
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 26,
                      fontWeight: FontWeight.w800,
                      letterSpacing: -0.5,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    "Connecting youth to work",
                    style: TextStyle(
                      color: Colors.white.withValues(alpha: 0.85),
                      fontSize: 13,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                  const SizedBox(height: AppSpacing.xl),

                  // Card
                  Container(
                    width: double.infinity,
                    constraints: const BoxConstraints(maxWidth: 420),
                    padding: const EdgeInsets.all(AppSpacing.lg),
                    decoration: BoxDecoration(
                      color: context.colors.surface,
                      borderRadius: BorderRadius.circular(AppRadius.lg),
                      boxShadow: [
                        BoxShadow(
                          color: Colors.black.withValues(alpha: 0.15),
                          blurRadius: 24,
                          offset: const Offset(0, 12),
                        ),
                      ],
                    ),
                    child: Form(
                      key: _formKey,
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text("Welcome back", style: Theme.of(context).textTheme.headlineSmall),
                          const SizedBox(height: 4),
                          Text(
                            "Sign in to see jobs matched to your verified skills.",
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                          const SizedBox(height: AppSpacing.lg),

                          if (_errorMessage != null) ...[
                            _ErrorBanner(message: _errorMessage!),
                            const SizedBox(height: AppSpacing.md),
                          ],

                          TextFormField(
                            controller: phoneOrEmailController,
                            decoration: const InputDecoration(
                              labelText: "Phone or email",
                              prefixIcon: Icon(Icons.person_outline_rounded),
                            ),
                            validator: (v) =>
                                (v == null || v.trim().isEmpty) ? "Required" : null,
                          ),
                          const SizedBox(height: AppSpacing.sm),
                          TextFormField(
                            controller: passwordController,
                            obscureText: _obscurePassword,
                            decoration: InputDecoration(
                              labelText: "Password",
                              prefixIcon: const Icon(Icons.lock_outline_rounded),
                              suffixIcon: IconButton(
                                tooltip: _obscurePassword ? "Show password" : "Hide password",
                                icon: Icon(
                                  _obscurePassword
                                      ? Icons.visibility_outlined
                                      : Icons.visibility_off_outlined,
                                  size: 20,
                                ),
                                onPressed: () =>
                                    setState(() => _obscurePassword = !_obscurePassword),
                              ),
                            ),
                            validator: (v) =>
                                (v == null || v.isEmpty) ? "Required" : null,
                            onFieldSubmitted: (_) => loginUser(),
                          ),
                          Align(
                            alignment: Alignment.centerRight,
                            child: TextButton(
                              style: TextButton.styleFrom(
                                padding: const EdgeInsets.symmetric(horizontal: 4),
                                minimumSize: Size.zero,
                                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                              ),
                              onPressed: () => Navigator.of(context).pushNamed('/forgot-password'),
                              child: const Text("Forgot password?"),
                            ),
                          ),
                          const SizedBox(height: AppSpacing.sm),
                          SizedBox(
                            width: double.infinity,
                            height: 50,
                            child: ElevatedButton(
                              onPressed: _loading ? null : loginUser,
                              child: _loading
                                  ? const SizedBox(
                                      width: 22,
                                      height: 22,
                                      child: CircularProgressIndicator(
                                        strokeWidth: 2.4,
                                        valueColor: AlwaysStoppedAnimation(Colors.white),
                                      ),
                                    )
                                  : const Text("Log in"),
                            ),
                          ),
                          const SizedBox(height: AppSpacing.md),
                          Center(
                            child: Wrap(
                              alignment: WrapAlignment.center,
                              crossAxisAlignment: WrapCrossAlignment.center,
                              children: [
                                Text(
                                  "Don't have an account? ",
                                  style: Theme.of(context).textTheme.bodyMedium,
                                ),
                                TextButton(
                                  style: TextButton.styleFrom(
                                    padding: const EdgeInsets.symmetric(horizontal: 4),
                                    minimumSize: Size.zero,
                                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                                  ),
                                  onPressed: () =>
                                      Navigator.of(context).pushReplacementNamed('/register'),
                                  child: const Text("Register"),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  final String message;
  const _ErrorBanner({required this.message});

  @override
  Widget build(BuildContext context) {
    return Semantics(
      liveRegion: true,
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(AppSpacing.sm),
        decoration: BoxDecoration(
          color: context.colors.errorBg,
          borderRadius: BorderRadius.circular(AppRadius.sm),
          border: Border.all(color: context.colors.error.withValues(alpha: 0.25)),
        ),
        child: Row(
          children: [
            Icon(Icons.error_outline_rounded, color: context.colors.error, size: 18),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                message,
                style: TextStyle(color: context.colors.error, fontSize: 13, fontWeight: FontWeight.w600),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
