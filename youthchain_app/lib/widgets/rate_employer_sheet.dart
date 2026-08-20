import 'dart:convert';

import 'package:flutter/material.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';

/// Bottom sheet for a worker rating the employer on a completed gig
/// application -- the worker's half of the bidirectional rating (see the
/// Rating model's docstring in app.py for why the employer-rates-worker
/// direction alone wasn't enough: employer-side risk, like a no-show or
/// wage theft, is just as real in informal work). Returns true if a
/// rating was actually submitted, so the caller can refresh its list.
Future<bool> showRateEmployerSheet(
  BuildContext context, {
  required int applicationId,
}) async {
  int score = 0;
  final commentCtrl = TextEditingController();
  final l10n = context.l10n;

  final result = await showModalBottomSheet<bool>(
    context: context,
    isScrollControlled: true,
    backgroundColor: context.colors.surface,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadius.lg)),
    ),
    builder: (ctx) {
      bool submitting = false;
      return StatefulBuilder(
        builder: (ctx, setSheetState) {
          Future<void> submit() async {
            if (score < 1) {
              ScaffoldMessenger.of(ctx).showSnackBar(
                SnackBar(content: Text(l10n.chooseStarRatingError)),
              );
              return;
            }
            setSheetState(() => submitting = true);
            try {
              final resp = await ApiClient.instance
                  .postJson("/api/applications/$applicationId/rate_employer", {
                    "score": score,
                    if (commentCtrl.text.trim().isNotEmpty)
                      "comment": commentCtrl.text.trim(),
                  });
              if (resp.statusCode == 201) {
                if (ctx.mounted) Navigator.of(ctx).pop(true);
              } else {
                String msg = l10n.couldNotSubmitRating;
                try {
                  final body = json.decode(resp.body);
                  if (body is Map && body["error"] is String) {
                    msg = body["error"] as String;
                  }
                } catch (_) {}
                if (ctx.mounted) {
                  ScaffoldMessenger.of(
                    ctx,
                  ).showSnackBar(SnackBar(content: Text(msg)));
                }
              }
            } catch (_) {
              if (ctx.mounted) {
                ScaffoldMessenger.of(ctx).showSnackBar(
                  SnackBar(content: Text(l10n.networkErrorSubmittingRating)),
                );
              }
            } finally {
              if (ctx.mounted) setSheetState(() => submitting = false);
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
                  l10n.rateThisEmployerTitle,
                  style: Theme.of(ctx).textTheme.titleLarge,
                ),
                const SizedBox(height: 4),
                Text(
                  l10n.rateEmployerSubtitle,
                  style: Theme.of(ctx).textTheme.bodyMedium,
                ),
                const SizedBox(height: AppSpacing.md),
                Center(
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: List.generate(5, (i) {
                      final starValue = i + 1;
                      final filled = starValue <= score;
                      return IconButton(
                        iconSize: 34,
                        icon: Icon(
                          filled
                              ? Icons.star_rounded
                              : Icons.star_outline_rounded,
                          color: context.colors.secondary,
                        ),
                        onPressed: () => setSheetState(() => score = starValue),
                      );
                    }),
                  ),
                ),
                const SizedBox(height: AppSpacing.sm),
                TextField(
                  controller: commentCtrl,
                  maxLines: 3,
                  decoration: InputDecoration(
                    labelText: l10n.ratingCommentOptionalLabel,
                    hintText: l10n.ratingCommentHint,
                  ),
                ),
                const SizedBox(height: AppSpacing.lg),
                SizedBox(
                  width: double.infinity,
                  height: 50,
                  child: ElevatedButton.icon(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: context.colors.secondary,
                    ),
                    icon: submitting
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.white,
                            ),
                          )
                        : const Icon(Icons.star_rounded, size: 18),
                    label: Text(submitting ? l10n.submittingEllipsis : l10n.submitRatingButton),
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
  return result ?? false;
}
