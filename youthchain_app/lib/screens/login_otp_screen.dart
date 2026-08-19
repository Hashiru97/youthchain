import 'dart:convert';

import 'package:flutter/material.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../services/push_notification_service.dart';
import '../theme/app_theme.dart';

/// Passwordless login via a 6-digit code -- same 2-step (contact -> code)
/// shape and the same channel-aware /auth/otp/request + /auth/otp/verify
/// endpoints as ForgotPasswordScreen's request/verify pair (see that
/// screen's own docstring), just ending in an actual session instead of a
/// password change. Built specifically so a phone-only-remembering user
/// (an Orange/Africell number, no email habitually used for this account)
/// has a way in that doesn't require ever having set — or now
/// remembering — a password.
enum _LoginOtpStep { contact, code }

class LoginOtpScreen extends StatefulWidget {
  const LoginOtpScreen({super.key});

  @override
  State<LoginOtpScreen> createState() => _LoginOtpScreenState();
}

class _LoginOtpScreenState extends State<LoginOtpScreen> {
  _LoginOtpStep _step = _LoginOtpStep.contact;
  String _channel = 'sms'; // 'email' | 'sms' -- SMS-first here, unlike
  // ForgotPasswordScreen's email-first default: a passwordless login is
  // the path most likely to matter to exactly the phone-only user this
  // screen exists for.

  final identifierController = TextEditingController();
  final otpController = TextEditingController();

  bool _loading = false;
  String? _error;

  bool get _isEmail => _channel == 'email';

  @override
  void dispose() {
    identifierController.dispose();
    otpController.dispose();
    super.dispose();
  }

  Map<String, dynamic>? _tryJson(String body) {
    try {
      final v = json.decode(body);
      return v is Map<String, dynamic> ? v : null;
    } catch (_) {
      return null;
    }
  }

  Future<void> _sendCode() async {
    final identifier = identifierController.text.trim();
    if (identifier.isEmpty) {
      setState(() => _error = _isEmail ? context.l10n.pleaseEnterEmail : context.l10n.pleaseEnterPhone);
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await ApiClient.instance.postJson(
        '/auth/otp/request',
        {'channel': _channel, 'identifier': identifier},
        auth: false,
      );
      final data = _tryJson(res.body);
      if (!mounted) return;
      if (res.statusCode == 200) {
        setState(() => _step = _LoginOtpStep.code);
      } else {
        setState(() => _error = data?['error'] ?? context.l10n.failedToSendCode(res.statusCode));
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _error = context.l10n.networkErrorGeneric);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _verifyCode() async {
    final code = otpController.text.trim();
    if (code.length != 6) {
      setState(() => _error = context.l10n.enterSixDigitCode);
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await ApiClient.instance.postJson(
        '/auth/otp/verify',
        {
          'channel': _channel,
          'identifier': identifierController.text.trim(),
          'code': code,
        },
        auth: false,
      );
      final data = _tryJson(res.body);
      if (!mounted) return;
      if (res.statusCode == 200 && data?['user'] != null && data?['access_token'] != null) {
        final userId = data!['user']['id'] as int;
        await ApiClient.instance.saveSession(
          token: data['access_token'] as String,
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
        setState(() => _error = data?['error'] ?? context.l10n.invalidOrExpiredCode);
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _error = context.l10n.networkErrorGeneric);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
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
                  Container(
                    width: 64,
                    height: 64,
                    decoration: BoxDecoration(
                      color: Colors.white.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(AppRadius.md),
                    ),
                    child: const Icon(
                      Icons.password_rounded,
                      color: Colors.white,
                      size: 32,
                    ),
                  ),
                  const SizedBox(height: AppSpacing.md),
                  Text(
                    l10n.loginOtpHeading,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 24,
                      fontWeight: FontWeight.w800,
                      letterSpacing: -0.5,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    l10n.loginOtpSubtitle,
                    style: TextStyle(
                      color: Colors.white.withValues(alpha: 0.85),
                      fontSize: 13,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                  const SizedBox(height: AppSpacing.xl),
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
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (_error != null) ...[
                          Container(
                            width: double.infinity,
                            padding: const EdgeInsets.all(AppSpacing.sm),
                            decoration: BoxDecoration(
                              color: context.colors.errorBg,
                              borderRadius: BorderRadius.circular(AppRadius.sm),
                              border: Border.all(color: context.colors.error.withValues(alpha: 0.25)),
                            ),
                            child: Text(
                              _error!,
                              style: TextStyle(color: context.colors.error, fontSize: 13, fontWeight: FontWeight.w600),
                            ),
                          ),
                          const SizedBox(height: AppSpacing.sm),
                        ],
                        if (_step == _LoginOtpStep.contact) ..._buildContactStep(l10n),
                        if (_step == _LoginOtpStep.code) ..._buildCodeStep(l10n),
                      ],
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

  List<Widget> _buildContactStep(AppLocalizations l10n) {
    return [
      Text(l10n.whereSendCode, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700)),
      const SizedBox(height: AppSpacing.sm),
      Row(
        children: [
          Expanded(
            child: _ChannelChoiceCard(
              label: l10n.channelPhoneSms,
              icon: Icons.sms_outlined,
              selected: !_isEmail,
              onTap: () => setState(() => _channel = 'sms'),
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: _ChannelChoiceCard(
              label: l10n.channelEmail,
              icon: Icons.email_outlined,
              selected: _isEmail,
              onTap: () => setState(() => _channel = 'email'),
            ),
          ),
        ],
      ),
      const SizedBox(height: AppSpacing.sm),
      TextField(
        key: ValueKey(_channel),
        controller: identifierController,
        keyboardType: _isEmail ? TextInputType.emailAddress : TextInputType.phone,
        decoration: InputDecoration(
          labelText: _isEmail ? l10n.emailAddressLabel : l10n.phoneNumberLabel,
          prefixIcon: Icon(_isEmail ? Icons.email_outlined : Icons.phone_outlined),
        ),
        onSubmitted: (_) => _sendCode(),
      ),
      const SizedBox(height: AppSpacing.sm),
      _primaryButton(l10n.sendCodeButton, _sendCode),
      const SizedBox(height: 4),
      Center(
        child: TextButton(
          onPressed: () => Navigator.of(context).pushReplacementNamed('/login'),
          child: Text(l10n.useYourPasswordInstead),
        ),
      ),
    ];
  }

  List<Widget> _buildCodeStep(AppLocalizations l10n) {
    return [
      Text(
        l10n.accountCodeSentTo(identifierController.text.trim()),
        style: Theme.of(context).textTheme.bodyMedium,
      ),
      const SizedBox(height: AppSpacing.sm),
      TextField(
        controller: otpController,
        keyboardType: TextInputType.number,
        maxLength: 6,
        textAlign: TextAlign.center,
        autofocus: true,
        style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700, letterSpacing: 8),
        decoration: InputDecoration(labelText: l10n.loginCodeLabel, counterText: ''),
        onSubmitted: (_) => _verifyCode(),
      ),
      const SizedBox(height: AppSpacing.sm),
      _primaryButton(l10n.logInButton, _verifyCode),
      const SizedBox(height: 4),
      Center(
        child: TextButton(
          onPressed: _loading
              ? null
              : () => setState(() {
                    _step = _LoginOtpStep.contact;
                    _error = null;
                    otpController.clear();
                  }),
          child: Text(l10n.useDifferentEmailPhone),
        ),
      ),
    ];
  }

  Widget _primaryButton(String label, VoidCallback onPressed) {
    return SizedBox(
      width: double.infinity,
      height: 50,
      child: ElevatedButton(
        onPressed: _loading ? null : onPressed,
        child: _loading
            ? const SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(strokeWidth: 2.4, valueColor: AlwaysStoppedAnimation(Colors.white)),
              )
            : Text(label),
      ),
    );
  }
}

/// Same visual card ForgotPasswordScreen's own private _ChannelChoiceCard
/// renders -- duplicated rather than shared, matching how this file
/// otherwise deliberately mirrors that screen's structure without
/// depending on its private widgets.
class _ChannelChoiceCard extends StatelessWidget {
  final String label;
  final IconData icon;
  final bool selected;
  final VoidCallback onTap;

  const _ChannelChoiceCard({
    required this.label,
    required this.icon,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: selected ? context.colors.primaryLight : context.colors.surface,
      borderRadius: BorderRadius.circular(AppRadius.sm),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(AppRadius.sm),
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 10),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(AppRadius.sm),
            border: Border.all(color: selected ? context.colors.primary : context.colors.outline, width: selected ? 1.5 : 1),
          ),
          child: Column(
            children: [
              Icon(icon, size: 20, color: selected ? context.colors.primary : context.colors.textMuted),
              const SizedBox(height: 4),
              Text(
                label,
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w700,
                  color: selected ? context.colors.primary : context.colors.textSecondary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
