import 'dart:convert';

import 'package:flutter/material.dart';

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
                const SnackBar(content: Text("Choose a star rating.")),
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
                String msg = "Could not submit rating";
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
                  const SnackBar(
                    content: Text("Network error submitting rating"),
                  ),
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
                  "Rate this employer",
                  style: Theme.of(ctx).textTheme.titleLarge,
                ),
                const SizedBox(height: 4),
                Text(
                  "How was your experience on this gig? This helps other workers decide who to work with.",
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
                  decoration: const InputDecoration(
                    labelText: "Comment (optional)",
                    hintText: "e.g. Paid on time, clear instructions",
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
                    label: Text(submitting ? "Submitting..." : "Submit rating"),
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
