import 'dart:convert';

import 'package:flutter/material.dart';

import '../services/api_client.dart';
import '../theme/app_theme.dart';

/// The "report this employer" bottom sheet — shared between JobDetailScreen
/// (reporting a specific job posting) and MessagesScreen (reporting a
/// specific conversation). Originally built only inside JobDetailScreen;
/// extracted here rather than duplicated when MessagesScreen needed the
/// same UI, so the two copies can't silently drift apart the way this
/// codebase has already found happen elsewhere (see _employer_summary()
/// on the backend, consolidated for the same reason).
///
/// [jobId] and [messageId] are both optional context — POST
/// /api/report_employer accepts either, neither, or (in principle) both,
/// though in practice each caller only ever has one of them available.
///
/// [scrapedJobId], when set instead of [employerId], switches the whole
/// sheet to the Discover-listing report path (POST /api/report_listing)
/// — a scraped listing has no real Employer account to report against
/// (see ScrapedListingReport's own docstring on the backend for why
/// that's a genuinely separate model/route, not just a nullable
/// employer_id). Exactly one of [employerId]/[scrapedJobId] must be
/// given; sharing one sheet widget for both keeps the UI identical
/// rather than maintaining two near-duplicate bottom sheets.
Future<void> showReportSheet(
  BuildContext context, {
  int? employerId,
  int? scrapedJobId,
  int? jobId,
  int? messageId,
}) async {
  assert(
    (employerId == null) != (scrapedJobId == null),
    "showReportSheet needs exactly one of employerId or scrapedJobId",
  );
  final bool isListingReport = scrapedJobId != null;

  const categories = <String, String>{
    "scam": "Scam",
    "harassment": "Harassment",
    "fake_job": "Fake job",
    "inappropriate": "Inappropriate",
    "other": "Other",
  };

  await showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: context.colors.surface,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadius.lg)),
    ),
    builder: (ctx) {
      String selectedCategory = "scam";
      final detailsCtrl = TextEditingController();
      bool submitting = false;

      return StatefulBuilder(
        builder: (ctx, setSheetState) {
          Future<void> submit() async {
            setSheetState(() => submitting = true);
            try {
              final res = await ApiClient.instance.postJson(
                isListingReport ? "/api/report_listing" : "/api/report_employer",
                {
                  if (isListingReport) "job_id": scrapedJobId else "employer_id": employerId,
                  "category": selectedCategory,
                  if (!isListingReport && jobId != null) "job_id": jobId,
                  if (messageId != null) "message_id": messageId,
                  if (detailsCtrl.text.trim().isNotEmpty)
                    "details": detailsCtrl.text.trim(),
                },
              );

              if (res.statusCode == 201) {
                if (ctx.mounted) Navigator.of(ctx).pop();
                if (!context.mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text(
                      "Report submitted — thank you for helping keep YouthChain safe",
                    ),
                  ),
                );
              } else if (res.statusCode == 429) {
                final body = _jsonDecodeSafe(res.body);
                final msg =
                    (body?["error"] as String?) ??
                    "Too many reports. Try again later.";
                if (!ctx.mounted) return;
                ScaffoldMessenger.of(
                  ctx,
                ).showSnackBar(SnackBar(content: Text(msg)));
              } else {
                final body = _jsonDecodeSafe(res.body);
                final msg =
                    (body?["error"] as String?) ?? "Failed to submit report";
                if (!ctx.mounted) return;
                ScaffoldMessenger.of(
                  ctx,
                ).showSnackBar(SnackBar(content: Text(msg)));
              }
            } catch (_) {
              if (!ctx.mounted) return;
              ScaffoldMessenger.of(ctx).showSnackBar(
                const SnackBar(
                  content: Text("Network error while submitting report"),
                ),
              );
            } finally {
              setSheetState(() => submitting = false);
            }
          }

          return Padding(
            padding: EdgeInsets.only(
              left: AppSpacing.md,
              right: AppSpacing.md,
              bottom: MediaQuery.of(ctx).viewInsets.bottom + AppSpacing.md,
              top: AppSpacing.md,
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Center(
                  child: Container(
                    height: 4,
                    width: 40,
                    margin: const EdgeInsets.only(bottom: AppSpacing.md),
                    decoration: BoxDecoration(
                      color: context.colors.outline,
                      borderRadius: BorderRadius.circular(2),
                    ),
                  ),
                ),
                Text(
                  isListingReport ? "Report this listing" : "Report this employer",
                  style: Theme.of(ctx).textTheme.titleLarge,
                ),
                const SizedBox(height: 6),
                Text(
                  isListingReport
                      ? "Tell us what's wrong. Reports help YouthChain keep Discover "
                          "listings trustworthy."
                      : "Tell us what's wrong. Reports help YouthChain keep employers "
                          "accountable.",
                  style: Theme.of(ctx).textTheme.bodyMedium,
                ),
                const SizedBox(height: AppSpacing.md),
                Text("Category", style: Theme.of(ctx).textTheme.titleSmall),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: categories.entries.map((entry) {
                    return ChoiceChip(
                      label: Text(entry.value),
                      selected: selectedCategory == entry.key,
                      onSelected: (_) {
                        setSheetState(() => selectedCategory = entry.key);
                      },
                    );
                  }).toList(),
                ),
                const SizedBox(height: AppSpacing.md),
                TextField(
                  controller: detailsCtrl,
                  maxLines: 4,
                  maxLength: 500,
                  decoration: const InputDecoration(
                    labelText: "Details (optional)",
                    hintText: "Describe what happened...",
                    alignLabelWithHint: true,
                  ),
                ),
                const SizedBox(height: AppSpacing.sm),
                SizedBox(
                  width: double.infinity,
                  height: 48,
                  child: ElevatedButton.icon(
                    icon: submitting
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.white,
                            ),
                          )
                        : const Icon(Icons.flag_outlined, size: 18),
                    label: Text(submitting ? "Submitting..." : "Submit report"),
                    onPressed: submitting ? null : submit,
                  ),
                ),
              ],
            ),
          );
        },
      );
    },
  );
}

Map<String, dynamic>? _jsonDecodeSafe(String body) {
  try {
    final v = json.decode(body);
    return v is Map<String, dynamic> ? v : null;
  } catch (_) {
    return null;
  }
}
