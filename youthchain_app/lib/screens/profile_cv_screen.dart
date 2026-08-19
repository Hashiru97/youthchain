import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_html/flutter_html.dart';
import 'package:printing/printing.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/language_picker.dart';

class ProfileCvScreen extends StatefulWidget {
  final int userId;
  const ProfileCvScreen({super.key, required this.userId});

  @override
  State<ProfileCvScreen> createState() => _ProfileCvScreenState();
}

class _ProfileCvScreenState extends State<ProfileCvScreen> {
  final _formKey = GlobalKey<FormState>();

  final _nameCtrl = TextEditingController();
  final _emailCtrl = TextEditingController();
  final _locationCtrl = TextEditingController();
  final _skillsCtrl = TextEditingController();
  final _bioCtrl = TextEditingController();

  bool _saving = false;
  bool _loadingCv = false;
  bool _loadingProfile = true;
  bool _exportingPdf = false;

  // Kept for a fallback/plain-text toggle -- `cv_html` (below) is the
  // primary preview now, but some future caller (or a device where the
  // formatted render looks off) may still want the raw text.
  String? _cvText;
  String? _cvHtml;
  bool? _aiGenerated;
  bool _showPlainText = false;

  // Industry-matching preference (see GET /api/industries, POST
  // /api/candidate "preferred_industries") — fetched once so the checkbox
  // options can never drift from the backend's canonical list.
  List<String> _industryOptions = [];
  final Set<String> _selectedIndustries = {};

  // Candidate.job_alerts_enabled (see GET/POST /api/candidate) -- opt-in,
  // so this starts false for a profile that hasn't set it yet, same as
  // the backend column's own default.
  bool _jobAlertsEnabled = false;

  // User.sms_alerts_enabled (see GET /api/me, PUT /api/sms_alerts) --
  // lives on the account, not the candidate profile (a Saved Search alert
  // has no candidate behind it at all), so unlike everything else on this
  // screen it loads/saves through its own pair of calls rather than
  // riding along with GET/POST /api/candidate.
  bool _smsAlertsEnabled = false;
  bool _savingSmsAlerts = false;

  // User.whatsapp_alerts_enabled (see GET /api/me, PUT /api/whatsapp_alerts)
  // -- same account-level shape and reasoning as _smsAlertsEnabled just
  // above, kept as its own independent flag/switch rather than reusing
  // the SMS one (see whatsapp_alerts_enabled's own backend docstring for
  // why they're deliberately separate opt-ins).
  bool _whatsappAlertsEnabled = false;
  bool _savingWhatsappAlerts = false;

  @override
  void initState() {
    super.initState();
    _loadExistingProfile();
    _loadIndustries();
    _loadAlertPreferences();
  }

  Future<void> _loadAlertPreferences() async {
    try {
      final res = await ApiClient.instance.get('/api/me');
      if (!mounted || res.statusCode != 200) return;
      final body = json.decode(res.body);
      final user = body is Map ? body['user'] : null;
      if (user is Map) {
        setState(() {
          _smsAlertsEnabled = user['sms_alerts_enabled'] == true;
          _whatsappAlertsEnabled = user['whatsapp_alerts_enabled'] == true;
        });
      }
    } catch (_) {
      // Best-effort -- the switches just start unchecked-looking, same
      // convention as this screen's other best-effort background loads.
    }
  }

  /// Saves immediately on toggle rather than waiting for "Save Profile" --
  /// this is an account-level setting (see _smsAlertsEnabled's own
  /// comment), not a candidate-profile field, so there's no reason it
  /// should need a valid name/email in the form above to take effect.
  /// Optimistic update, reverted on failure so the switch never lies
  /// about what's actually saved server-side.
  Future<void> _setSmsAlertsEnabled(bool value) async {
    final previous = _smsAlertsEnabled;
    setState(() {
      _smsAlertsEnabled = value;
      _savingSmsAlerts = true;
    });
    try {
      final res = await ApiClient.instance.putJson('/api/sms_alerts', {'enabled': value});
      if (!mounted) return;
      if (res.statusCode != 200) {
        setState(() => _smsAlertsEnabled = previous);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.l10n.couldNotUpdateSmsAlerts)),
        );
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _smsAlertsEnabled = previous);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorUpdatingSmsAlerts)),
      );
    } finally {
      if (mounted) setState(() => _savingSmsAlerts = false);
    }
  }

  /// Mirrors _setSmsAlertsEnabled exactly, just against the independent
  /// WhatsApp opt-in/endpoint -- see whatsapp_alerts_enabled's backend
  /// docstring for why this isn't just the SMS switch reused.
  Future<void> _setWhatsappAlertsEnabled(bool value) async {
    final previous = _whatsappAlertsEnabled;
    setState(() {
      _whatsappAlertsEnabled = value;
      _savingWhatsappAlerts = true;
    });
    try {
      final res = await ApiClient.instance.putJson('/api/whatsapp_alerts', {'enabled': value});
      if (!mounted) return;
      if (res.statusCode != 200) {
        setState(() => _whatsappAlertsEnabled = previous);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.l10n.couldNotUpdateWhatsappAlerts)),
        );
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _whatsappAlertsEnabled = previous);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorUpdatingWhatsappAlerts)),
      );
    } finally {
      if (mounted) setState(() => _savingWhatsappAlerts = false);
    }
  }

  Future<void> _loadIndustries() async {
    try {
      final res = await ApiClient.instance.get('/api/industries');
      if (!mounted) return;
      if (res.statusCode == 200) {
        final body = json.decode(res.body);
        final industries = body is Map ? body['industries'] : null;
        if (industries is List) {
          setState(() {
            _industryOptions = industries.map((e) => e.toString()).toList();
          });
        }
      }
    } catch (_) {
      // Non-critical — the section just won't offer any options this cycle.
    }
  }

  Future<void> _loadExistingProfile() async {
    try {
      final res = await ApiClient.instance.get('/api/candidate/me');
      if (!mounted) return;
      if (res.statusCode == 200) {
        final body = json.decode(res.body);
        final candidate = body is Map ? body['candidate'] : null;
        if (candidate is Map) {
          if (candidate['id'] != null) {
            await ApiClient.instance.saveCandidateId(
              (candidate['id'] as num).toInt(),
            );
          }
          _nameCtrl.text = (candidate['name'] ?? '').toString();
          _emailCtrl.text = (candidate['email'] ?? '').toString();
          _locationCtrl.text = (candidate['location'] ?? '').toString();
          _skillsCtrl.text = (candidate['skills'] ?? '').toString();
          _bioCtrl.text = (candidate['bio'] ?? '').toString();
          _jobAlertsEnabled = candidate['job_alerts_enabled'] == true;
          final preferredIndustries = candidate['preferred_industries'];
          if (preferredIndustries is String) {
            setState(() {
              _selectedIndustries
                ..clear()
                ..addAll(
                  preferredIndustries
                      .split(',')
                      .map((e) => e.trim())
                      .where((e) => e.isNotEmpty),
                );
            });
          }
        }
      }
    } catch (_) {
      // No existing profile yet, or a transient network error — the form
      // just starts blank either way, same as before this screen existed.
    } finally {
      if (mounted) setState(() => _loadingProfile = false);
    }
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _emailCtrl.dispose();
    _locationCtrl.dispose();
    _skillsCtrl.dispose();
    _bioCtrl.dispose();
    super.dispose();
  }

  Future<void> _saveProfile() async {
    if (!_formKey.currentState!.validate()) return;

    setState(() => _saving = true);
    try {
      final res = await ApiClient.instance.postJson("/api/candidate", {
        "name": _nameCtrl.text.trim(),
        "email": _emailCtrl.text.trim(),
        "location": _locationCtrl.text.trim(),
        "skills": _skillsCtrl.text.trim(), // comma-separated
        "bio": _bioCtrl.text.trim(),
        "preferred_industries": _selectedIndustries.toList(),
        "job_alerts_enabled": _jobAlertsEnabled,
      });

      if (!mounted) return;
      if (res.statusCode == 200 || res.statusCode == 201) {
        final body = json.decode(res.body);
        if (body is Map && body['candidate_id'] != null) {
          await ApiClient.instance.saveCandidateId(
            (body['candidate_id'] as num).toInt(),
          );
        }
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.l10n.profileSavedIndexed)),
        );
      } else {
        final body = json.decode(res.body);
        final msg = body is Map && body["error"] is String
            ? body["error"] as String
            : context.l10n.failedToSaveProfile;
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(msg)));
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorSavingProfile)),
      );
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _generateCv() async {
    setState(() {
      _loadingCv = true;
      _cvText = null;
      _cvHtml = null;
      _aiGenerated = null;
      _showPlainText = false;
    });
    try {
      final candidateId = await ApiClient.instance.resolveCandidateId();
      if (candidateId == null) {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(context.l10n.saveProfileFirstForCv),
          ),
        );
        return;
      }

      final res = await ApiClient.instance.get('/api/generate_cv/$candidateId');

      if (!mounted) return;

      if (res.statusCode == 200) {
        final data = json.decode(res.body);
        setState(() {
          _cvText = data["cv"] as String?;
          _cvHtml = data["cv_html"] as String?;
          _aiGenerated = data["ai_generated"] as bool?;
        });
      } else {
        final body = json.decode(res.body);
        final msg = body is Map && body["error"] is String
            ? body["error"] as String
            : context.l10n.couldNotGenerateCv;
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(msg)));
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorGeneratingCv)),
      );
    } finally {
      if (mounted) setState(() => _loadingCv = false);
    }
  }

  /// Opens the native OS print/share sheet (Android's includes "Save as
  /// PDF"; iOS's is the standard print dialog with a PDF/share option) for
  /// the currently generated CV. Wraps the same `cv_html` fragment the
  /// in-app preview renders in a standalone HTML document so the PDF is a
  /// close visual match of what the user just previewed.
  Future<void> _exportCvPdf() async {
    final html = _cvHtml;
    if (html == null) return;

    setState(() => _exportingPdf = true);
    try {
      final fullDocument = _standaloneCvHtmlDocument(html);
      await Printing.layoutPdf(
        onLayout: (format) async =>
            // ignore: deprecated_member_use
            Printing.convertHtml(format: format, html: fullDocument),
      );
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(context.l10n.couldNotOpenPrintExport),
        ),
      );
    } finally {
      if (mounted) setState(() => _exportingPdf = false);
    }
  }

  /// Wraps the sanitized `cv_html` fragment in a full HTML document with a
  /// print-oriented stylesheet that mirrors the in-app preview's design
  /// (see [_cvHtmlStyles]) as closely as CSS allows -- deliberately fixed,
  /// light-background colors here rather than the app's live theme tokens,
  /// since a printed/exported CV should read the same on paper regardless
  /// of whether the phone was in dark mode when it was generated.
  String _standaloneCvHtmlDocument(String cvHtmlFragment) {
    return '''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>CV</title>
<style>
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    margin: 40px;
    color: #15211E;
    background: #FFFFFF;
  }
  .cv-doc { max-width: 720px; margin: 0 auto; }
  .cv-header {
    border-bottom: 2px solid #0F7A5C;
    padding-bottom: 12px;
    margin-bottom: 18px;
  }
  h1 { font-size: 26px; font-weight: 800; margin: 0 0 6px 0; color: #15211E; }
  .cv-headline {
    font-size: 14px;
    font-weight: 600;
    color: #0F7A5C;
    margin: 0 0 4px 0;
  }
  .cv-contact { font-size: 12.5px; color: #5B6B67; margin: 0; }
  .cv-section { margin-top: 18px; }
  h2 {
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: #0F7A5C;
    border-bottom: 1px solid #E3E8E8;
    padding-bottom: 5px;
    margin: 0 0 8px 0;
  }
  p { font-size: 13px; line-height: 1.55; color: #15211E; margin: 0; }
  ul { margin: 0; padding-left: 20px; list-style: none; }
  li { font-size: 13px; line-height: 1.6; color: #15211E; margin-bottom: 4px; }
  .cv-skills {
    padding-left: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .cv-skills li {
    background: #E3F5EE;
    color: #0B5D46;
    border-radius: 999px;
    padding: 4px 12px;
    margin: 0;
    font-size: 12px;
    font-weight: 600;
  }
  .cv-list.cv-credentials li {
    background: #E6F6EE;
    padding: 8px 10px;
    border-radius: 6px;
    margin-bottom: 8px;
  }
  .cv-badge {
    display: inline-block;
    background: #D9F0E4;
    color: #1E8E5A;
    font-size: 10.5px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 999px;
    margin-left: 6px;
  }
  strong { font-weight: 700; }
  hr { border: none; border-top: 1px solid #E3E8E8; margin: 16px 0; }
</style>
</head>
<body>
$cvHtmlFragment
</body>
</html>''';
  }

  /// The `flutter_html` style map for the in-app CV preview, keyed by the
  /// exact (and exhaustive) set of classes/tags the backend's `cv_html`
  /// fragment ever emits -- see the backend contract this screen was built
  /// against. `flutter_html`'s `Style` has no border-radius concept (it
  /// wraps a plain `Border`, not a `BoxDecoration`), so skill tags and the
  /// verified badge render as soft-colored rectangles in-app rather than
  /// true pill shapes; the standalone print/export document above (which
  /// is plain CSS) restores real `border-radius` there.
  Map<String, Style> _cvHtmlStyles(BuildContext context) {
    final colors = context.colors;
    return {
      ".cv-doc": Style(margin: Margins.zero, padding: HtmlPaddings.zero),
      ".cv-header": Style(
        margin: Margins.only(bottom: 16),
        padding: HtmlPaddings.only(bottom: 12),
        border: Border(bottom: BorderSide(color: colors.outline, width: 1.4)),
      ),
      "h1": Style(
        fontSize: FontSize(21),
        fontWeight: FontWeight.w800,
        color: colors.textPrimary,
        margin: Margins.zero,
        lineHeight: const LineHeight(1.2),
      ),
      ".cv-headline": Style(
        fontSize: FontSize(13.5),
        fontWeight: FontWeight.w600,
        color: colors.primary,
        margin: Margins.only(top: 4, bottom: 2),
        lineHeight: const LineHeight(1.35),
      ),
      ".cv-contact": Style(
        fontSize: FontSize(12.5),
        color: colors.textSecondary,
        margin: Margins.zero,
      ),
      ".cv-section": Style(margin: Margins.only(top: 16)),
      "h2": Style(
        fontSize: FontSize(12.5),
        fontWeight: FontWeight.w700,
        color: colors.primary,
        textTransform: TextTransform.uppercase,
        letterSpacing: 0.6,
        margin: Margins.only(bottom: 8),
        padding: HtmlPaddings.only(bottom: 5),
        border: Border(bottom: BorderSide(color: colors.outline, width: 1)),
      ),
      "p": Style(
        fontSize: FontSize(13),
        color: colors.textPrimary,
        lineHeight: const LineHeight(1.5),
        margin: Margins.zero,
      ),
      ".cv-list": Style(
        padding: HtmlPaddings.only(inlineStart: 4),
        margin: Margins.zero,
        listStyleType: ListStyleType.none,
      ),
      ".cv-list li": Style(
        fontSize: FontSize(13),
        color: colors.textPrimary,
        lineHeight: const LineHeight(1.5),
        margin: Margins.only(bottom: 6),
      ),
      ".cv-list.cv-credentials li": Style(
        backgroundColor: colors.successBg,
        padding: HtmlPaddings.symmetric(horizontal: 10, vertical: 8),
        margin: Margins.only(bottom: 8),
      ),
      "strong": Style(fontWeight: FontWeight.w700, color: colors.textPrimary),
      ".cv-skills": Style(
        padding: HtmlPaddings.zero,
        margin: Margins.only(top: 2),
        listStyleType: ListStyleType.none,
      ),
      ".cv-skills li": Style(
        display: Display.inlineBlock,
        backgroundColor: colors.primaryLight,
        color: colors.primaryDark,
        fontSize: FontSize(12),
        fontWeight: FontWeight.w600,
        padding: HtmlPaddings.symmetric(horizontal: 10, vertical: 4),
        margin: Margins.only(right: 6, bottom: 6),
      ),
      ".cv-badge": Style(
        backgroundColor: colors.successBg,
        color: colors.success,
        fontSize: FontSize(10.5),
        fontWeight: FontWeight.w700,
        padding: HtmlPaddings.symmetric(horizontal: 7, vertical: 2),
        margin: Margins.only(left: 6),
        before: "✓ ",
      ),
      "hr": Style(
        margin: Margins.symmetric(vertical: 12),
        border: Border(top: BorderSide(color: colors.outline, width: 1)),
      ),
    };
  }

  /// Small, understated indicator of whether the AI-polish step ran on
  /// this CV (`ai_generated: true`) or the backend fell back to the
  /// candidate's raw bio/skills text (no API key configured, or the AI
  /// call failed) -- both are fully valid CVs, this is informational only.
  Widget _buildCvModeChip(BuildContext context) {
    final isAi = _aiGenerated == true;
    final colors = context.colors;
    final fg = isAi ? colors.secondary : colors.textMuted;
    final bg = isAi ? colors.secondaryLight : colors.background;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(AppRadius.pill),
        border: Border.all(color: isAi ? colors.secondary.withValues(alpha: 0.35) : colors.outline),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            isAi ? Icons.auto_awesome_rounded : Icons.description_outlined,
            size: 12,
            color: fg,
          ),
          const SizedBox(width: 4),
          Text(
            isAi ? context.l10n.aiEnhancedChip : context.l10n.basicChip,
            style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: fg),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(title: Text(l10n.myProfileCvMenuItem)),
      body: SafeArea(
        child: _loadingProfile
            ? const Center(child: CircularProgressIndicator())
            : SingleChildScrollView(
                padding: const EdgeInsets.all(AppSpacing.md),
                child: Column(
                  children: [
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(AppSpacing.md),
                      decoration: BoxDecoration(
                        color: context.colors.surface,
                        borderRadius: BorderRadius.circular(AppRadius.md),
                        border: Border.all(color: context.colors.outline),
                        boxShadow: cardShadow,
                      ),
                      child: Form(
                        key: _formKey,
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Container(
                                  width: 40,
                                  height: 40,
                                  decoration: BoxDecoration(
                                    color: context.colors.primaryLight,
                                    shape: BoxShape.circle,
                                  ),
                                  child: Icon(
                                    Icons.person_outline_rounded,
                                    color: context.colors.primary,
                                    size: 20,
                                  ),
                                ),
                                const SizedBox(width: AppSpacing.sm),
                                Text(
                                  l10n.tellUsAboutYouHeading,
                                  style: Theme.of(context).textTheme.titleLarge,
                                ),
                              ],
                            ),
                            const SizedBox(height: 6),
                            Text(
                              l10n.profileIntroText,
                              style: Theme.of(context).textTheme.bodyMedium,
                            ),
                            const SizedBox(height: AppSpacing.md),
                            TextFormField(
                              controller: _nameCtrl,
                              decoration: InputDecoration(
                                labelText: l10n.fullNameLabel,
                                prefixIcon: const Icon(Icons.badge_outlined),
                              ),
                              validator: (v) => (v == null || v.trim().isEmpty)
                                  ? l10n.requiredField
                                  : null,
                            ),
                            const SizedBox(height: AppSpacing.sm),
                            TextFormField(
                              controller: _emailCtrl,
                              decoration: InputDecoration(
                                labelText: l10n.emailLabel,
                                prefixIcon: const Icon(Icons.email_outlined),
                              ),
                              keyboardType: TextInputType.emailAddress,
                              validator: (v) => (v == null || v.trim().isEmpty)
                                  ? l10n.requiredField
                                  : null,
                            ),
                            const SizedBox(height: AppSpacing.sm),
                            TextFormField(
                              controller: _locationCtrl,
                              decoration: InputDecoration(
                                labelText: l10n.locationHint,
                                hintText: l10n.locationExampleHint,
                                prefixIcon: const Icon(Icons.location_on_outlined),
                              ),
                            ),
                            const SizedBox(height: AppSpacing.sm),
                            TextFormField(
                              controller: _skillsCtrl,
                              decoration: InputDecoration(
                                labelText: l10n.skillsCommaLabel,
                                hintText: l10n.skillsExampleHint,
                                prefixIcon: const Icon(Icons.build_outlined),
                              ),
                            ),
                            const SizedBox(height: AppSpacing.sm),
                            TextFormField(
                              controller: _bioCtrl,
                              maxLines: 3,
                              decoration: InputDecoration(
                                labelText: l10n.shortBioLabel,
                                hintText: l10n.bioHint,
                                alignLabelWithHint: true,
                              ),
                            ),
                            const SizedBox(height: AppSpacing.md),
                            Text(
                              l10n.preferredIndustriesLabel,
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                            const SizedBox(height: 4),
                            Text(
                              l10n.preferredIndustriesHint,
                              style: Theme.of(context).textTheme.bodyMedium,
                            ),
                            const SizedBox(height: AppSpacing.sm),
                            if (_industryOptions.isEmpty)
                              Text(
                                l10n.noIndustriesAvailable,
                                style: Theme.of(context).textTheme.bodySmall,
                              )
                            else
                              Wrap(
                                spacing: 6,
                                runSpacing: 6,
                                children: _industryOptions.map((industry) {
                                  final selected = _selectedIndustries.contains(
                                    industry,
                                  );
                                  return FilterChip(
                                    label: Text(industry),
                                    selected: selected,
                                    onSelected: (value) {
                                      setState(() {
                                        if (value) {
                                          _selectedIndustries.add(industry);
                                        } else {
                                          _selectedIndustries.remove(industry);
                                        }
                                      });
                                    },
                                  );
                                }).toList(),
                              ),
                            const SizedBox(height: AppSpacing.md),
                            // Candidate.job_alerts_enabled -- opt-in
                            // notification (see
                            // app._dispatch_job_alerts_for_scan) whenever
                            // a newly scraped Discover job's required
                            // skills overlap the skills entered above.
                            // Distinct from Discover's own per-search
                            // "Alert me" bell (SavedSearchesScreen): this
                            // one alert covers every future scan, driven
                            // by the profile rather than a one-off search.
                            SwitchListTile(
                              contentPadding: EdgeInsets.zero,
                              value: _jobAlertsEnabled,
                              onChanged: (value) => setState(() => _jobAlertsEnabled = value),
                              title: Text(l10n.jobAlertsTitle),
                              subtitle: Text(l10n.jobAlertsSubtitle),
                            ),
                            // Saves immediately (see _setSmsAlertsEnabled)
                            // -- an account-level setting, not part of the
                            // form Save Profile below submits.
                            SwitchListTile(
                              contentPadding: EdgeInsets.zero,
                              value: _smsAlertsEnabled,
                              onChanged: _savingSmsAlerts ? null : _setSmsAlertsEnabled,
                              title: Text(l10n.smsAlertsTitle),
                              subtitle: Text(l10n.smsAlertsSubtitle),
                            ),
                            // Saves immediately (see _setWhatsappAlertsEnabled)
                            // -- same account-level, independent-of-SMS
                            // opt-in as above.
                            SwitchListTile(
                              contentPadding: EdgeInsets.zero,
                              value: _whatsappAlertsEnabled,
                              onChanged: _savingWhatsappAlerts ? null : _setWhatsappAlertsEnabled,
                              title: Text(l10n.whatsappAlertsTitle),
                              subtitle: Text(l10n.whatsappAlertsSubtitle),
                            ),
                            const SizedBox(height: AppSpacing.md),
                            const LanguagePicker(),
                            const SizedBox(height: AppSpacing.sm),
                            SizedBox(
                              width: double.infinity,
                              height: 48,
                              child: ElevatedButton.icon(
                                icon: _saving
                                    ? const SizedBox(
                                        width: 16,
                                        height: 16,
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                          color: Colors.white,
                                        ),
                                      )
                                    : const Icon(Icons.save_outlined, size: 18),
                                label: Text(
                                  _saving ? l10n.savingButton : l10n.saveProfileButton,
                                ),
                                onPressed: _saving ? null : _saveProfile,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: AppSpacing.md),
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(AppSpacing.md),
                      decoration: BoxDecoration(
                        color: context.colors.surface,
                        borderRadius: BorderRadius.circular(AppRadius.md),
                        border: Border.all(color: context.colors.outline),
                        boxShadow: cardShadow,
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Container(
                                width: 40,
                                height: 40,
                                decoration: BoxDecoration(
                                  color: context.colors.tertiaryLight,
                                  shape: BoxShape.circle,
                                ),
                                child: Icon(
                                  Icons.description_outlined,
                                  color: context.colors.tertiary,
                                  size: 20,
                                ),
                              ),
                              const SizedBox(width: AppSpacing.sm),
                              Text(
                                l10n.cvPreviewTitle,
                                style: Theme.of(context).textTheme.titleLarge,
                              ),
                            ],
                          ),
                          const SizedBox(height: 6),
                          Text(
                            l10n.cvPreviewIntro,
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                          const SizedBox(height: AppSpacing.md),
                          SizedBox(
                            width: double.infinity,
                            height: 46,
                            child: OutlinedButton.icon(
                              icon: _loadingCv
                                  ? const SizedBox(
                                      width: 16,
                                      height: 16,
                                      child: CircularProgressIndicator(
                                        strokeWidth: 2,
                                      ),
                                    )
                                  : const Icon(
                                      Icons.auto_awesome_outlined,
                                      size: 18,
                                    ),
                              label: Text(
                                _loadingCv ? l10n.generatingButton : l10n.generateCvButton,
                              ),
                              onPressed: _loadingCv ? null : _generateCv,
                            ),
                          ),
                          if (_cvHtml != null) ...[
                            const SizedBox(height: AppSpacing.md),
                            Row(
                              children: [
                                _buildCvModeChip(context),
                                const Spacer(),
                                TextButton.icon(
                                  onPressed: () => setState(
                                    () => _showPlainText = !_showPlainText,
                                  ),
                                  icon: Icon(
                                    _showPlainText
                                        ? Icons.article_outlined
                                        : Icons.text_snippet_outlined,
                                    size: 16,
                                  ),
                                  label: Text(
                                    _showPlainText
                                        ? l10n.viewFormattedButton
                                        : l10n.viewAsPlainTextButton,
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: AppSpacing.sm),
                            Container(
                              width: double.infinity,
                              padding: EdgeInsets.all(
                                _showPlainText ? AppSpacing.sm : AppSpacing.md,
                              ),
                              decoration: BoxDecoration(
                                color: context.colors.background,
                                borderRadius: BorderRadius.circular(
                                  AppRadius.sm,
                                ),
                                border: Border.all(color: context.colors.outline),
                              ),
                              child: _showPlainText
                                  ? SelectableText(
                                      _cvText ?? '',
                                      style: TextStyle(
                                        fontFamily: "monospace",
                                        fontSize: 13,
                                        color: context.colors.textPrimary,
                                      ),
                                    )
                                  : Html(
                                      data: _cvHtml!,
                                      style: _cvHtmlStyles(context),
                                    ),
                            ),
                            const SizedBox(height: AppSpacing.md),
                            SizedBox(
                              width: double.infinity,
                              height: 46,
                              child: OutlinedButton.icon(
                                icon: _exportingPdf
                                    ? const SizedBox(
                                        width: 16,
                                        height: 16,
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                        ),
                                      )
                                    : const Icon(
                                        Icons.picture_as_pdf_outlined,
                                        size: 18,
                                      ),
                                label: Text(
                                  _exportingPdf
                                      ? l10n.preparingButton
                                      : l10n.exportAsPdfButton,
                                ),
                                onPressed: _exportingPdf ? null : _exportCvPdf,
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ],
                ),
              ),
      ),
    );
  }
}

