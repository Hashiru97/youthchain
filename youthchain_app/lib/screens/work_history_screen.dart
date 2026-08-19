import 'dart:convert';

import 'package:flutter/material.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';
import '../widgets/status_badge.dart';

/// The gig/informal-work counterpart to the Passport screen: a worker's
/// portable trust record built from completed gigs and ratings instead of
/// diplomas (see Job.job_type / the Rating model in app.py). Deliberately a
/// separate screen from Passport rather than folded into it -- diploma
/// credentials are on-chain-verified, gig ratings are peer-review-based,
/// and merging the two would blur a distinction users need to trust both
/// independently.
class WorkHistoryScreen extends StatefulWidget {
  final int userId;
  const WorkHistoryScreen({super.key, required this.userId});

  @override
  WorkHistoryScreenState createState() => WorkHistoryScreenState();
}

class WorkHistoryScreenState extends State<WorkHistoryScreen> {
  bool isLoading = true;
  bool _showingCachedData = false;
  Map<String, dynamic>? _summary;

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    setState(() => isLoading = true);
    try {
      final result = await ApiClient.instance.getWithCache(
        "/api/candidate/${widget.userId}/trust_summary",
      );
      if (!mounted) return;
      setState(() {
        _summary = json.decode(result.body) as Map<String, dynamic>;
        _showingCachedData = result.fromCache;
        isLoading = false;
      });
    } on NoCachedDataException {
      if (!mounted) return;
      setState(() {
        _summary = null;
        isLoading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _summary = null;
        isLoading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: Text(context.l10n.workHistoryFab),
        backgroundColor: context.colors.secondary,
        actions: [
          IconButton(
            tooltip: context.l10n.refreshTooltip,
            icon: const Icon(Icons.refresh_rounded),
            onPressed: _fetch,
          ),
        ],
      ),
      body: Column(
        children: [
          if (_showingCachedData)
            Container(
              width: double.infinity,
              color: context.colors.warningBg,
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.md,
                vertical: AppSpacing.sm,
              ),
              child: Semantics(
                liveRegion: true,
                label: context.l10n.offlineShowingWorkHistory,
                child: Row(
                  children: [
                    Icon(
                      Icons.cloud_off_rounded,
                      size: 16,
                      color: context.colors.warning,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        context.l10n.offlineShowingWorkHistory,
                        style: TextStyle(
                          fontSize: 12,
                          color: context.colors.warning,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          Expanded(child: _buildBody()),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (isLoading) return const SkeletonListView();

    final summary = _summary;
    if (summary == null) {
      // Must actually be wrapped in RefreshIndicator, not just a bare
      // ListView -- the subtitle below tells the user to "pull down to
      // try again," which was never true before this fix (same bug
      // found on DiscoverScreen/OldListingsScreen/JobScreen/
      // SavedJobsScreen, just more visible here since the copy itself
      // promised a gesture that didn't work).
      return RefreshIndicator(
        onRefresh: _fetch,
        child: ListView(
          children: [
            const SizedBox(height: 120),
            EmptyState(
              icon: Icons.star_outline_rounded,
              title: context.l10n.couldNotLoadWorkHistoryTitle,
              subtitle: context.l10n.pullDownToTryAgain,
            ),
          ],
        ),
      );
    }

    final int completedGigs = (summary["completed_gigs"] as num?)?.toInt() ?? 0;
    final num? avgRating = summary["avg_rating"] as num?;
    final int ratingCount = (summary["rating_count"] as num?)?.toInt() ?? 0;
    final List ratedGigs = (summary["rated_gigs"] as List?) ?? [];

    return RefreshIndicator(
      onRefresh: _fetch,
      child: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          _buildSummaryCard(completedGigs, avgRating, ratingCount),
          const SizedBox(height: AppSpacing.lg),
          Text(context.l10n.ratedGigsHeading, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          if (ratedGigs.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.lg),
              child: EmptyState(
                icon: Icons.handshake_outlined,
                title: context.l10n.noRatedGigsYetTitle,
                subtitle: context.l10n.noRatedGigsYetSubtitle,
              ),
            )
          else
            ...ratedGigs.map((g) => _buildGigCard(g as Map)),
        ],
      ),
    );
  }

  Widget _buildSummaryCard(int completedGigs, num? avgRating, int ratingCount) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: context.colors.secondary,
        borderRadius: BorderRadius.circular(AppRadius.md),
        boxShadow: cardShadow,
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  ratingCount > 0
                      ? "★ ${avgRating?.toStringAsFixed(1)}"
                      : context.l10n.noRatingsYetLabel,
                  style: Theme.of(
                    context,
                  ).textTheme.headlineSmall?.copyWith(color: Colors.white),
                ),
                const SizedBox(height: 4),
                Text(
                  ratingCount > 0
                      ? context.l10n.fromRatingsCountLabel(ratingCount)
                      : context.l10n.completeGigToEarnFirstRating,
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: Colors.white.withValues(alpha: 0.85),
                  ),
                ),
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                "$completedGigs",
                style: Theme.of(
                  context,
                ).textTheme.headlineSmall?.copyWith(color: Colors.white),
              ),
              Text(
                context.l10n.completedGigsCountLabel(completedGigs),
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: Colors.white.withValues(alpha: 0.85),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildGigCard(Map gig) {
    final String jobTitle = (gig["job_title"] as String?) ?? "Gig";
    final String? category = gig["category"] as String?;
    final int score = (gig["score"] as num?)?.toInt() ?? 0;
    final String? comment = gig["comment"] as String?;

    // A colored left-accent stripe combined with a rounded corner needs a
    // uniform-color Border on the decoration itself -- Flutter's Border
    // painter asserts "A borderRadius can only be given on borders with
    // uniform colors" and throws at paint time otherwise (real, reproduced
    // crash: a per-side Border mixing the accent color with the outline
    // color, plus borderRadius, on the same BoxDecoration). The accent is
    // drawn as a separate slim child instead, clipped to the same rounded
    // rect via the outer Container's clipBehavior, with a plain uniform
    // outline border on the decoration itself (which radius is fine with).
    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.md),
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: context.colors.surface,
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(color: context.colors.outline),
        boxShadow: cardShadow,
      ),
      // IntrinsicHeight: a plain Row(crossAxisAlignment: stretch) needs a
      // bounded height to stretch its children to, but this Container sits
      // directly in a ListView, which gives each item unbounded height --
      // real, reproduced crash ("BoxConstraints forces an infinite
      // height") caught via this screen's own widget test. IntrinsicHeight
      // makes the Row size itself from its tallest child first, giving
      // `stretch` something finite to stretch the accent stripe to.
      child: IntrinsicHeight(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(width: 4, color: context.colors.secondary),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.all(AppSpacing.md),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            jobTitle,
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          if (category != null && category.isNotEmpty) ...[
                            const SizedBox(height: 2),
                            Text(
                              category,
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ],
                          if (comment != null && comment.isNotEmpty) ...[
                            const SizedBox(height: AppSpacing.sm),
                            Text(
                              "“$comment”",
                              style: Theme.of(context).textTheme.bodyMedium,
                            ),
                          ],
                        ],
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    StatusBadge(
                      label: "$score/5",
                      color: context.colors.secondary,
                      background: context.colors.secondaryLight,
                      icon: Icons.star_rounded,
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
