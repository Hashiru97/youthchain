import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../l10n/l10n_context.dart';
import '../theme/app_theme.dart';
import '../widgets/formatted_description.dart';
import '../widgets/report_sheet.dart';
import '../widgets/save_job_button.dart';
import '../widgets/status_badge.dart';

/// Full-detail view for a single Discover (scraped) listing. Deliberately
/// its own screen, not a reuse of JobDetailScreen — JobDetailScreen's
/// constructor hard-requires an `onApply`/CV-upload callback wired to
/// JobScreen's in-platform apply flow, which doesn't apply here: a
/// scraped listing's "apply" action is opening its external apply_url,
/// not this platform's own /apply endpoint. Reuses the same visual
/// language (StatusBadge, spacing) as JobDetailScreen, just for a
/// different action shape.
class DiscoverJobDetailScreen extends StatelessWidget {
  final Map<String, dynamic> job;
  // Passed in by the caller (which already knows this job's saved state
  // from its own list) rather than fetched again here -- avoids an
  // extra round trip just to draw one icon.
  final bool initiallySaved;

  const DiscoverJobDetailScreen({super.key, required this.job, this.initiallySaved = false});

  static const _months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];

  // Deliberately hand-rolled rather than pulling in the `intl` package for
  // one "YYYY-MM-DD" -> "23 Aug 2026" conversion -- no other screen in this
  // app formats dates yet, so a whole new dependency isn't justified for it.
  String? _formatDeadline(String? isoDate) {
    if (isoDate == null) return null;
    final parsed = DateTime.tryParse(isoDate);
    if (parsed == null) return null;
    return "${parsed.day} ${_months[parsed.month - 1]} ${parsed.year}";
  }

  Future<void> _openApplyUrl(BuildContext context) async {
    final url = job["apply_url"] as String?;
    if (url == null || url.isEmpty) return;
    try {
      final uri = Uri.parse(url);
      final ok = await launchUrl(uri, mode: LaunchMode.inAppWebView);
      if (!ok && context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.l10n.couldNotOpenApplicationLink)),
        );
      }
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.l10n.couldNotOpenApplicationLink)),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final title = (job["title"] as String?) ?? l10n.jobDetailFallbackTitle;
    final location = (job["location"] as String?) ?? "";
    final companyName = job["company_name"] as String?;
    final companyVerified = job["company_verified"] == true;
    final sourceName = job["source_name"] as String?;
    final salary = job["salary"] as String?;
    final employmentType = job["employment_type"] as String?;
    final description = job["description"] as String?;
    final applyUrl = job["apply_url"] as String?;
    final jobId = (job["id"] as num).toInt();
    final deadlineFormatted = _formatDeadline(job["application_deadline"] as String?);
    final isExpired = job["is_expired"] == true;

    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: Text(l10n.jobDetailsTitle),
        actions: [
          // See JobDetailScreen's identical fix for why color is forced
          // here: this AppBar's background is colors.primary too, same
          // theme-wide default.
          SaveJobButton(jobId: jobId, initiallySaved: initiallySaved, color: Colors.white),
          IconButton(
            icon: const Icon(Icons.flag_outlined, color: Colors.white),
            tooltip: l10n.reportThisListingTooltip,
            onPressed: () => showReportSheet(context, scrapedJobId: jobId),
          ),
        ],
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 52,
                  height: 52,
                  decoration: BoxDecoration(
                    color: context.colors.primaryLight,
                    borderRadius: BorderRadius.circular(AppRadius.sm),
                  ),
                  child: Icon(
                    Icons.travel_explore_rounded,
                    color: context.colors.primary,
                    size: 26,
                  ),
                ),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(title, style: Theme.of(context).textTheme.headlineSmall),
                      const SizedBox(height: 6),
                      Wrap(
                        spacing: 6,
                        runSpacing: 6,
                        children: [
                          StatusBadge(
                            label: sourceName ?? l10n.scrapedListingBadge,
                            color: context.colors.textMuted,
                            background: context.colors.background,
                            icon: Icons.travel_explore_rounded,
                            dense: true,
                          ),
                          if (isExpired)
                            StatusBadge(
                              label: l10n.expiredBadge,
                              color: context.colors.error,
                              background: context.colors.errorBg,
                              icon: Icons.event_busy_rounded,
                              dense: true,
                            ),
                        ],
                      ),
                      if (deadlineFormatted != null) ...[
                        const SizedBox(height: 4),
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Icon(
                              Icons.event_outlined,
                              size: 16,
                              color: isExpired ? context.colors.error : context.colors.textMuted,
                            ),
                            const SizedBox(width: 4),
                            Expanded(
                              child: Text(
                                isExpired ? l10n.deadlineWasLabel(deadlineFormatted) : l10n.applyByLabel(deadlineFormatted),
                                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                      color: isExpired ? context.colors.error : null,
                                    ),
                              ),
                            ),
                          ],
                        ),
                      ],
                      if (location.isNotEmpty) ...[
                        const SizedBox(height: 6),
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Icon(Icons.location_on_outlined, size: 16, color: context.colors.textMuted),
                            const SizedBox(width: 4),
                            Expanded(child: Text(location, style: Theme.of(context).textTheme.bodyMedium)),
                          ],
                        ),
                      ],
                      if (salary != null && salary.isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Icon(Icons.payments_outlined, size: 16, color: context.colors.textMuted),
                            const SizedBox(width: 4),
                            Expanded(child: Text(salary, style: Theme.of(context).textTheme.bodyMedium)),
                          ],
                        ),
                      ],
                      if (employmentType != null && employmentType.isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Icon(Icons.work_outline_rounded, size: 16, color: context.colors.textMuted),
                            const SizedBox(width: 4),
                            Expanded(child: Text(employmentType, style: Theme.of(context).textTheme.bodyMedium)),
                          ],
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
            if (companyName != null && companyName.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.lg),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(AppSpacing.md),
                decoration: BoxDecoration(
                  color: context.colors.surface,
                  borderRadius: BorderRadius.circular(AppRadius.md),
                  border: Border.all(color: context.colors.outline),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(l10n.companyLabel, style: Theme.of(context).textTheme.titleSmall),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            companyName,
                            style: Theme.of(context).textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w600),
                          ),
                        ),
                        if (companyVerified)
                          StatusBadge(
                            label: l10n.verifiedBadge,
                            color: context.colors.success,
                            background: context.colors.successBg,
                            icon: Icons.verified_rounded,
                          ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
            if (!companyVerified) ...[
              const SizedBox(height: AppSpacing.lg),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(AppSpacing.md),
                decoration: BoxDecoration(
                  color: context.colors.warningBg,
                  borderRadius: BorderRadius.circular(AppRadius.md),
                  border: Border.all(color: context.colors.warning.withValues(alpha: 0.4)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.shield_outlined, size: 18, color: context.colors.warning),
                        const SizedBox(width: 6),
                        Text(
                          l10n.employerNotVerifiedWarning,
                          style: Theme.of(context).textTheme.titleSmall?.copyWith(color: context.colors.warning),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(
                      l10n.unverifiedEmployerSafetyTips,
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ],
                ),
              ),
            ],
            if (description != null && description.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.lg),
              Text(l10n.descriptionLabel, style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 8),
              FormattedJobDescription(text: description),
            ],
            const SizedBox(height: AppSpacing.xl),
          ],
        ),
      ),
      bottomNavigationBar: applyUrl != null && applyUrl.isNotEmpty
          ? SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(AppSpacing.md),
                child: SizedBox(
                  width: double.infinity,
                  height: 48,
                  child: ElevatedButton.icon(
                    icon: const Icon(Icons.open_in_new_rounded, size: 18),
                    label: Text(l10n.applyButtonLabel),
                    onPressed: () => _openApplyUrl(context),
                  ),
                ),
              ),
            )
          : null,
    );
  }
}
