import 'dart:convert';

import 'package:flutter/material.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';

/// Same 3-step shape as RegistrationScreen (contact -> code -> confirm),
/// same reasoning: verify the code fully before setting a new password,
/// not alongside it -- see that screen's docstring. Reuses the exact
/// channel-aware /auth/otp/reset/* endpoints built for this alongside
/// the portal/employer/admin web equivalents (BL-46), rather than an
/// emailed reset-link flow that a mobile app has no way to deep-link
/// back into cleanly.
enum _ResetStep { contact, code, confirm }

class ForgotPasswordScreen extends StatefulWidget {
  const ForgotPasswordScreen({super.key});

  @override
  State<ForgotPasswordScreen> createState() => _ForgotPasswordScreenState();
}

class _ForgotPasswordScreenState extends State<ForgotPasswordScreen> {
  _ResetStep _step = _ResetStep.contact;
  String _channel = 'email'; // 'email' | 'sms'

  final identifierController = TextEditingController();
  final otpController = TextEditingController();
  final passwordController = TextEditingController();
  final confirmPasswordController = TextEditingController();

  bool _loading = false;
  bool _obscurePassword = true;
  bool _obscureConfirm = true;
  String? _error;
  String? _success;

  bool get _isEmail => _channel == 'email';

  @override
  void dispose() {
    identifierController.dispose();
    otpController.dispose();
    passwordController.dispose();
    confirmPasswordController.dispose();
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
        '/auth/otp/reset/request',
        {'channel': _channel, 'identifier': identifier},
        auth: false,
      );
      final data = _tryJson(res.body);
      if (!mounted) return;
      if (res.statusCode == 200) {
        setState(() => _step = _ResetStep.code);
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
        '/auth/otp/reset/verify',
        {
          'channel': _channel,
          'identifier': identifierController.text.trim(),
          'code': code,
        },
        auth: false,
      );
      final data = _tryJson(res.body);
      if (!mounted) return;
      if (res.statusCode == 200 && data?['success'] == true) {
        setState(() => _step = _ResetStep.confirm);
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

  Future<void> _confirmReset() async {
    if (passwordController.text.isEmpty || confirmPasswordController.text.isEmpty) {
      setState(() => _error = context.l10n.pleaseCompleteBothPasswordFields);
      return;
    }
    if (passwordController.text != confirmPasswordController.text) {
      setState(() => _error = context.l10n.passwordsDoNotMatch);
      return;
    }
    if (passwordController.text.length < 8) {
      setState(() => _error = context.l10n.passwordMinLength);
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await ApiClient.instance.postJson(
        '/auth/otp/reset/confirm',
        {
          'channel': _channel,
          'identifier': identifierController.text.trim(),
          'code': otpController.text.trim(),
          'new_password': passwordController.text,
        },
        auth: false,
      );
      final data = _tryJson(res.body);
      if (!mounted) return;
      if (res.statusCode == 200) {
        setState(() => _success = context.l10n.passwordChangedSuccess);
      } else {
        setState(() => _error = data?['error'] ?? context.l10n.resetFailedGeneric(res.statusCode));
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _error = context.l10n.networkErrorDuringReset);
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
                      Icons.lock_reset_rounded,
                      color: Colors.white,
                      size: 32,
                    ),
                  ),
                  const SizedBox(height: AppSpacing.md),
                  Text(
                    l10n.resetPasswordHeading,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 24,
                      fontWeight: FontWeight.w800,
                      letterSpacing: -0.5,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    l10n.resetPasswordSubtitle,
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
                        if (_success != null) ..._buildSuccess(l10n) else ...[
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
                          if (_step == _ResetStep.contact) ..._buildContactStep(l10n),
                          if (_step == _ResetStep.code) ..._buildCodeStep(l10n),
                          if (_step == _ResetStep.confirm) ..._buildConfirmStep(l10n),
                        ],
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

  List<Widget> _buildSuccess(AppLocalizations l10n) {
    return [
      Icon(Icons.check_circle_rounded, color: context.colors.success, size: 40),
      const SizedBox(height: AppSpacing.sm),
      Text(_success!, style: Theme.of(context).textTheme.bodyLarge),
      const SizedBox(height: AppSpacing.md),
      SizedBox(
        width: double.infinity,
        height: 50,
        child: ElevatedButton(
          onPressed: () => Navigator.of(context).pushReplacementNamed('/login'),
          child: Text(l10n.backToLoginButton),
        ),
      ),
    ];
  }

  List<Widget> _buildContactStep(AppLocalizations l10n) {
    return [
      Text(l10n.whereSendResetCode, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700)),
      const SizedBox(height: AppSpacing.sm),
      Row(
        children: [
          Expanded(
            child: _ChannelChoiceCard(
              label: l10n.channelEmail,
              icon: Icons.email_outlined,
              selected: _isEmail,
              onTap: () => setState(() => _channel = 'email'),
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: _ChannelChoiceCard(
              label: l10n.channelPhoneSms,
              icon: Icons.sms_outlined,
              selected: !_isEmail,
              onTap: () => setState(() => _channel = 'sms'),
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
      ),
      const SizedBox(height: 4),
      Text(
        l10n.registrationSendCodeHint,
        style: Theme.of(context).textTheme.bodySmall,
      ),
      const SizedBox(height: AppSpacing.sm),
      _primaryButton(l10n.sendResetCodeButton, _sendCode),
      const SizedBox(height: 4),
      Center(
        child: TextButton(
          onPressed: () => Navigator.of(context).pushReplacementNamed('/login'),
          child: Text(l10n.rememberedPasswordLink),
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
        decoration: InputDecoration(labelText: l10n.resetCodeLabel, counterText: ''),
      ),
      const SizedBox(height: AppSpacing.sm),
      _primaryButton(l10n.verifyButton, _verifyCode),
      const SizedBox(height: 4),
      Center(
        child: TextButton(
          onPressed: _loading
              ? null
              : () => setState(() {
                    _step = _ResetStep.contact;
                    _error = null;
                    otpController.clear();
                  }),
          child: Text(l10n.useDifferentEmailPhone),
        ),
      ),
    ];
  }

  List<Widget> _buildConfirmStep(AppLocalizations l10n) {
    return [
      Text(l10n.verifiedChooseNewPassword(identifierController.text.trim()), style: Theme.of(context).textTheme.bodyMedium),
      const SizedBox(height: AppSpacing.sm),
      TextField(
        controller: passwordController,
        obscureText: _obscurePassword,
        decoration: InputDecoration(
          labelText: l10n.newPasswordLabel,
          prefixIcon: const Icon(Icons.lock_outline_rounded),
          suffixIcon: IconButton(
            icon: Icon(_obscurePassword ? Icons.visibility_outlined : Icons.visibility_off_outlined, size: 20),
            onPressed: () => setState(() => _obscurePassword = !_obscurePassword),
          ),
        ),
      ),
      const SizedBox(height: AppSpacing.sm),
      TextField(
        controller: confirmPasswordController,
        obscureText: _obscureConfirm,
        decoration: InputDecoration(
          labelText: l10n.confirmNewPasswordLabel,
          prefixIcon: const Icon(Icons.lock_outline_rounded),
          suffixIcon: IconButton(
            icon: Icon(_obscureConfirm ? Icons.visibility_outlined : Icons.visibility_off_outlined, size: 20),
            onPressed: () => setState(() => _obscureConfirm = !_obscureConfirm),
          ),
        ),
      ),
      const SizedBox(height: AppSpacing.sm),
      _primaryButton(l10n.changePasswordButton, _confirmReset),
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
    // Material (colored) + InkWell, not InkWell wrapping an opaque
    // Container -- same ripple-visibility bug found and fixed in
    // notifications_screen.dart: Material paints ink splashes BEHIND its
    // child, so an opaque Container in between hides the ripple entirely.
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
