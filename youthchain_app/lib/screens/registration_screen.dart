import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../services/push_notification_service.dart';
import '../theme/app_theme.dart';

/// Three steps, not one combined form -- restructured to match the web
/// portal's portal_register() flow (see app.py): choose a contact channel
/// (email or SMS) and verify it BEFORE being asked to set a password,
/// rather than typing a password that then just sits there while
/// fetching/entering a code. Real gap found via user feedback:
/// registration was email-only despite password login already accepting
/// "phone or email" as the identifier.
///
/// Unlike the web portal, this app is a stateless JWT client with no
/// server-side session to remember "this identifier was verified"
/// between steps -- step 2 calls the dedicated
/// /auth/otp/register/verify pre-check endpoint for early feedback (a
/// wrong code surfaces immediately, not only after also typing a
/// password), but the code itself is held in memory and re-sent with
/// the final /register call, which is what actually consumes it.
enum _RegStep { contact, code, details }

class RegistrationScreen extends StatefulWidget {
  const RegistrationScreen({super.key});

  @override
  RegistrationScreenState createState() => RegistrationScreenState();
}

class RegistrationScreenState extends State<RegistrationScreen> {
  _RegStep _step = _RegStep.contact;
  String _channel = 'email'; // 'email' | 'sms'

  final identifierController = TextEditingController();
  final otpController = TextEditingController();
  final firstNameController = TextEditingController();
  final lastNameController = TextEditingController();
  final otherController = TextEditingController();
  final ncraIdController = TextEditingController();
  final passwordController = TextEditingController();
  final confirmPasswordController = TextEditingController();

  bool _loading = false;
  bool _obscurePassword = true;
  bool _obscureConfirm = true;
  bool _consentAccepted = false;
  String? _error;

  bool get _isEmail => _channel == 'email';

  Future<void> _openPrivacyPolicy() async {
    final uri = Uri.parse('${ApiClient.baseUrl}/privacy-policy');
    await launchUrl(uri, mode: LaunchMode.externalApplication);
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
        '/auth/otp/register/request',
        {'channel': _channel, 'identifier': identifier},
        auth: false,
      );
      final data = _tryJson(res.body);
      if (!mounted) return;
      if (res.statusCode == 200) {
        setState(() => _step = _RegStep.code);
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
        '/auth/otp/register/verify',
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
        setState(() => _step = _RegStep.details);
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

  Future<void> _createAccount() async {
    if ([firstNameController, lastNameController, otherController, passwordController, confirmPasswordController]
        .any((c) => c.text.trim().isEmpty)) {
      setState(() => _error = context.l10n.pleaseCompleteAllFields);
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
    if (!_consentAccepted) {
      setState(() => _error = context.l10n.pleaseAcceptPrivacyPolicy);
      return;
    }

    final identifier = identifierController.text.trim();
    final email = _isEmail ? identifier : otherController.text.trim();
    final phone = _isEmail ? otherController.text.trim() : identifier;

    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await ApiClient.instance.postJson(
        '/register',
        {
          'first_name': firstNameController.text.trim(),
          'last_name': lastNameController.text.trim(),
          'phone': phone,
          'email': email,
          'ncra_id': ncraIdController.text.trim(),
          'password': passwordController.text,
          'otp_code': otpController.text.trim(),
          'channel': _channel,
          'consent': _consentAccepted,
        },
        auth: false,
      );
      final data = _tryJson(res.body);
      if (!mounted) return;

      if (res.statusCode == 201 && data?['user'] != null && data?['access_token'] != null) {
        final userId = data!['user']['id'] as int;
        await ApiClient.instance.saveSession(token: data['access_token'] as String, userId: userId);
        PushNotificationService.registerToken();
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(context.l10n.registrationSuccessful)));
        Navigator.of(context).pushNamedAndRemoveUntil('/home', (_) => false, arguments: {'userId': userId});
      } else {
        setState(() => _error = data?['error'] ?? context.l10n.registrationFailedGeneric(res.statusCode));
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _error = context.l10n.networkErrorDuringRegistration);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  void dispose() {
    identifierController.dispose();
    otpController.dispose();
    firstNameController.dispose();
    lastNameController.dispose();
    otherController.dispose();
    ncraIdController.dispose();
    passwordController.dispose();
    confirmPasswordController.dispose();
    super.dispose();
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
                      Icons.emoji_people_rounded,
                      color: Colors.white,
                      size: 32,
                    ),
                  ),
                  const SizedBox(height: AppSpacing.md),
                  Text(
                    l10n.createAccountHeading,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 24,
                      fontWeight: FontWeight.w800,
                      letterSpacing: -0.5,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    l10n.createAccountSubtitle,
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
                        Text(l10n.createYouthChainAccountTitle, style: Theme.of(context).textTheme.headlineSmall),
                        const SizedBox(height: AppSpacing.md),
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
                        if (_step == _RegStep.contact) ..._buildContactStep(l10n),
                        if (_step == _RegStep.code) ..._buildCodeStep(l10n),
                        if (_step == _RegStep.details) ..._buildDetailsStep(l10n),
                        const SizedBox(height: AppSpacing.md),
                        Center(
                          child: Wrap(
                            alignment: WrapAlignment.center,
                            crossAxisAlignment: WrapCrossAlignment.center,
                            children: [
                              Text(
                                l10n.alreadyHaveAccountPrompt,
                                style: Theme.of(context).textTheme.bodyMedium,
                              ),
                              TextButton(
                                style: TextButton.styleFrom(
                                  padding: const EdgeInsets.symmetric(horizontal: 4),
                                  minimumSize: Size.zero,
                                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                                ),
                                onPressed: () => Navigator.of(context).pushReplacementNamed('/login'),
                                child: Text(l10n.loginLinkText),
                              ),
                            ],
                          ),
                        ),
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
      Text(l10n.howSendVerificationCode, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700)),
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
      _primaryButton(l10n.sendVerificationCodeButton, _sendCode),
    ];
  }

  List<Widget> _buildCodeStep(AppLocalizations l10n) {
    return [
      Text(
        l10n.verificationCodeSentTo(identifierController.text.trim()),
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
        decoration: InputDecoration(labelText: l10n.verificationCodeLabel, counterText: ''),
      ),
      const SizedBox(height: AppSpacing.sm),
      _primaryButton(l10n.verifyButton, _verifyCode),
      const SizedBox(height: 4),
      Center(
        child: TextButton(
          onPressed: _loading
              ? null
              : () => setState(() {
                    _step = _RegStep.contact;
                    _error = null;
                    otpController.clear();
                  }),
          child: Text(l10n.useDifferentEmailPhone),
        ),
      ),
    ];
  }

  List<Widget> _buildDetailsStep(AppLocalizations l10n) {
    final identifier = identifierController.text.trim();
    return [
      Text(l10n.identifierVerifiedHeading(identifier), style: Theme.of(context).textTheme.bodyMedium),
      const SizedBox(height: AppSpacing.sm),
      TextField(
        controller: firstNameController,
        decoration: InputDecoration(
          labelText: l10n.firstNameLabel,
          prefixIcon: const Icon(Icons.person_outline_rounded),
        ),
      ),
      const SizedBox(height: AppSpacing.sm),
      TextField(
        controller: lastNameController,
        decoration: InputDecoration(
          labelText: l10n.lastNameLabel,
          prefixIcon: const Icon(Icons.person_outline_rounded),
        ),
      ),
      const SizedBox(height: AppSpacing.sm),
      TextField(
        controller: otherController,
        keyboardType: _isEmail ? TextInputType.phone : TextInputType.emailAddress,
        decoration: InputDecoration(
          labelText: _isEmail ? l10n.phoneNumberLabel : l10n.emailAddressLabel,
          prefixIcon: Icon(_isEmail ? Icons.phone_outlined : Icons.email_outlined),
        ),
      ),
      const SizedBox(height: AppSpacing.sm),
      TextField(
        controller: ncraIdController,
        decoration: InputDecoration(
          labelText: l10n.ncraIdOptionalLabel,
          prefixIcon: const Icon(Icons.badge_outlined),
        ),
      ),
      const SizedBox(height: AppSpacing.sm),
      TextField(
        controller: passwordController,
        obscureText: _obscurePassword,
        decoration: InputDecoration(
          labelText: l10n.passwordLabel,
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
          labelText: l10n.confirmPasswordLabel,
          prefixIcon: const Icon(Icons.lock_outline_rounded),
          suffixIcon: IconButton(
            icon: Icon(_obscureConfirm ? Icons.visibility_outlined : Icons.visibility_off_outlined, size: 20),
            onPressed: () => setState(() => _obscureConfirm = !_obscureConfirm),
          ),
        ),
      ),
      const SizedBox(height: AppSpacing.sm),
      Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Checkbox(
            value: _consentAccepted,
            onChanged: (v) => setState(() => _consentAccepted = v ?? false),
          ),
          Expanded(
            child: Wrap(
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(l10n.agreeToThePrefix, style: Theme.of(context).textTheme.bodySmall),
                GestureDetector(
                  onTap: _openPrivacyPolicy,
                  child: Text(
                    l10n.privacyPolicyLink,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          color: context.colors.primary,
                          fontWeight: FontWeight.w700,
                          decoration: TextDecoration.underline,
                        ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
      const SizedBox(height: AppSpacing.sm),
      _primaryButton(l10n.createAccountButton, _createAccount),
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
