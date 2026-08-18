import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../widgets/report_sheet.dart';
import '../widgets/save_job_button.dart';
import '../widgets/status_badge.dart';

/// Full-detail view for a single job, opened by tapping a job card on the
/// homepage (previously job cards had no tap handler at all — the only way
/// to interact with a job was the "Apply with CV" button, with no way to
/// see the full picture, including who's actually hiring, before applying).
///
/// Deliberately does not own the apply flow itself: JobScreen's
/// `_showApplySheet` already handles CV picking, upload, and the offline
/// write-queue fallback (BL-37) — reimplementing that here would risk
/// drifting out of sync with that logic. Instead this screen pops itself
/// and invokes [onApply], the same callback JobScreen's own button uses.
class JobDetailScreen extends StatelessWidget {
  final Map job;
  final bool alreadyApplied;
  final VoidCallback onApply;
  final bool initiallySaved;

  const JobDetailScreen({
    super.key,
    required this.job,
    required this.alreadyApplied,
    required this.onApply,
    this.initiallySaved = false,
  });

  List<String> _parseSkills(String? skills) => (skills ?? "")
      .split(',')
      .map((e) => e.trim())
      .where((e) => e.isNotEmpty)
      .toList();

  @override
  Widget build(BuildContext context) {
    final String title = (job["title"] as String?) ?? "Job";
    final String location = (job["location"] as String?) ?? "";
    final String duration = (job["duration"] as String?) ?? "";
    final skillList = _parseSkills(job["required_skills"] as String?);
    final employer = (job["employer"] as Map?)?.cast<String, dynamic>();
    final employerId = (job["employer_id"] as num?)?.toInt();
    final bool isGig = (job["job_type"] as String?) == "gig";
    final String? category = job["category"] as String?;

    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: const Text("Job Details"),
        actions: [
          if (job["id"] != null)
            SaveJobButton(
              jobId: (job["id"] as num).toInt(),
              initiallySaved: initiallySaved,
              // AppBarTheme's own background is colors.primary -- the
              // button's default "saved" color would be invisible
              // against it (confirmed live: green icon vanished into
              // the green app bar).
              color: Colors.white,
            ),
          if (employerId != null)
            IconButton(
              tooltip: "Report this job",
              icon: const Icon(Icons.flag_outlined),
              onPressed: () => showReportSheet(
                context,
                employerId: employerId,
                jobId: (job["id"] as num?)?.toInt(),
              ),
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
                    Icons.work_outline_rounded,
                    color: context.colors.primary,
                    size: 26,
                  ),
                ),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        title,
                        style: Theme.of(context).textTheme.headlineSmall,
                      ),
                      if (isGig) ...[
                        const SizedBox(height: 6),
                        StatusBadge.jobType(context, "gig"),
                      ],
                      const SizedBox(height: 6),
                      if (location.isNotEmpty)
                        Row(
                          children: [
                            Icon(
                              Icons.location_on_outlined,
                              size: 16,
                              color: context.colors.textMuted,
                            ),
                            const SizedBox(width: 4),
                            Text(
                              location,
                              style: Theme.of(context).textTheme.bodyMedium,
                            ),
                          ],
                        ),
                      if (duration.isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Row(
                          children: [
                            Icon(
                              Icons.schedule_outlined,
                              size: 16,
                              color: context.colors.textMuted,
                            ),
                            const SizedBox(width: 4),
                            Text(
                              duration,
                              style: Theme.of(context).textTheme.bodyMedium,
                            ),
                          ],
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
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
                  Text(
                    "Hiring organization",
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                  const SizedBox(height: 8),
                  if (employer == null)
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(
                          Icons.info_outline_rounded,
                          size: 16,
                          color: context.colors.textMuted,
                        ),
                        const SizedBox(width: 6),
                        Expanded(
                          child: Text(
                            "Posted by YouthChain (no employer account linked to this listing)",
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                        ),
                      ],
                    )
                  else
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Expanded(
                              child: Text(
                                (employer["name"] as String?) ?? "Employer",
                                style: Theme.of(context).textTheme.bodyLarge
                                    ?.copyWith(fontWeight: FontWeight.w600),
                              ),
                            ),
                            StatusBadge.employerVerification(
                              context,
                              (employer["verification_status"] as String?) ??
                                  "unverified",
                              type: employer["verification_type"] as String?,
                            ),
                          ],
                        ),
                        // Earned trust from past gig workers (see
                        // _employer_trust_summary in app.py) -- the same
                        // "trust from a real track record" signal a
                        // CV-less worker gets, extended to the employer
                        // side too, so an individual employer with no
                        // business papers at all can still show real
                        // evidence they're worth working for.
                        if ((employer["rating_count"] as num?) != null &&
                            (employer["rating_count"] as num) > 0) ...[
                          const SizedBox(height: 4),
                          Row(
                            children: [
                              Icon(
                                Icons.star_rounded,
                                size: 15,
                                color: context.colors.secondary,
                              ),
                              const SizedBox(width: 3),
                              Text(
                                "${employer["avg_rating"]} · ${employer["rating_count"]} rating${(employer["rating_count"] as num) == 1 ? '' : 's'} from past workers",
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                            ],
                          ),
                        ],
                      ],
                    ),
                ],
              ),
            ),
            if (isGig) ...[
              const SizedBox(height: AppSpacing.lg),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(AppSpacing.md),
                decoration: BoxDecoration(
                  color: context.colors.secondaryLight,
                  borderRadius: BorderRadius.circular(AppRadius.md),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(
                      Icons.handshake_rounded,
                      size: 18,
                      color: context.colors.secondary,
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    Expanded(
                      child: Text(
                        category != null && category.isNotEmpty
                            ? "$category — no CV needed to apply. Trust here comes from completed gigs and ratings instead."
                            : "No CV needed to apply for gig/hire-based work — trust here comes from completed gigs and ratings instead.",
                        style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          color: context.colors.secondaryDark,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
            if (skillList.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.lg),
              Text(
                "Required skills",
                style: Theme.of(context).textTheme.titleSmall,
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: skillList.map((s) => Chip(label: Text(s))).toList(),
              ),
            ],
            const SizedBox(height: AppSpacing.xl),
          ],
        ),
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          child: SizedBox(
            width: double.infinity,
            height: 48,
            child: alreadyApplied
                ? OutlinedButton.icon(
                    onPressed: null,
                    icon: const Icon(Icons.check_rounded, size: 18),
                    label: const Text("Applied"),
                  )
                : ElevatedButton.icon(
                    icon: const Icon(Icons.send_rounded, size: 18),
                    label: Text(isGig ? "Apply" : "Apply with CV"),
                    onPressed: () {
                      Navigator.pop(context);
                      onApply();
                    },
                  ),
          ),
        ),
      ),
    );
  }
}
