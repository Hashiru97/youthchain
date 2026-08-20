import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../services/pending_applications.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';
import '../widgets/rate_employer_sheet.dart';
import '../widgets/status_badge.dart';
import 'messages_screen.dart';

class MyApplicationsScreenState extends State<MyApplicationsScreen> {
  List applications = [];
  List<PendingApplication> pending = [];
  bool isLoading = true;
  bool _showingCachedData = false;
  DateTime? _cachedAt;

  StreamSubscription<List<PendingApplication>>? _pendingSub;

  @override
  void initState() {
    super.initState();
    fetchApplications();
    _loadPending();
    _pendingSub = PendingApplicationsQueue.instance.changes.listen((_) {
      if (!mounted) return;
      // The stream emits the raw, account-unscoped on-disk queue (see
      // loadForCurrentUser()'s own docstring) -- re-derive this account's
      // own view rather than displaying that payload directly, or a
      // shared-device account switch would leak the PREVIOUS account's
      // still-pending application (CV attached) into this one's list.
      _loadPending();
      // A queued item just succeeded and dropped out of the pending list —
      // refresh the real list so it shows up there instead.
      fetchApplications();
    });
  }

  @override
  void dispose() {
    _pendingSub?.cancel();
    super.dispose();
  }

  Future<void> _loadPending() async {
    final items = await PendingApplicationsQueue.instance.loadForCurrentUser();
    if (!mounted) return;
    setState(() => pending = items);
  }

  Future<void> fetchApplications() async {
    setState(() => isLoading = true);
    try {
      // BL-37: falls back to the last successfully loaded copy when offline
      // instead of just showing an empty/error state.
      final result = await ApiClient.instance.getWithCache(
        "/my_applications/${widget.userId}",
      );
      if (!mounted) return;
      setState(() {
        applications = json.decode(result.body);
        _showingCachedData = result.fromCache;
        _cachedAt = result.cachedAt;
        isLoading = false;
      });
    } on NoCachedDataException {
      if (!mounted) return;
      setState(() {
        applications = [];
        isLoading = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(context.l10n.noConnectionNoPreviousData),
        ),
      );
    } catch (_) {
      if (!mounted) return;
      setState(() {
        applications = [];
        isLoading = false;
      });
    }
  }

  Future<void> _rateEmployer(int applicationId) async {
    final submitted = await showRateEmployerSheet(
      context,
      applicationId: applicationId,
    );
    if (submitted) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(context.l10n.ratingSubmitted)));
      await fetchApplications();
    }
  }

  /// The dispute path for a rating either side thinks is unfair (see
  /// RatingFlag's docstring in app.py) -- a small confirm-style dialog is
  /// enough here, unlike the star-rating sheet, since it's just a reason
  /// and a submit button.
  Future<void> _disputeRating(int ratingId) async {
    final l10n = context.l10n;
    final reasonCtrl = TextEditingController();
    final reason = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.disputeRatingTitle),
        content: TextField(
          controller: reasonCtrl,
          maxLines: 3,
          autofocus: true,
          decoration: InputDecoration(
            labelText: l10n.whyIsRatingUnfairLabel,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text(l10n.cancelButton),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(reasonCtrl.text.trim()),
            child: Text(l10n.submitButton),
          ),
        ],
      ),
    );
    if (reason == null || reason.isEmpty) return;

    try {
      final resp = await ApiClient.instance.postJson(
        "/api/ratings/$ratingId/flag",
        {"reason": reason},
      );
      if (!mounted) return;
      if (resp.statusCode == 201) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.l10n.reportedForAdminReview)),
        );
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.l10n.couldNotSubmitDispute)),
        );
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorSubmittingDispute)),
      );
    }
  }

  Future<void> _openFile(String filename) async {
    try {
      // Opened via an external app/browser, which can't carry our
      // Authorization header — the token is passed as a query param instead
      // (backend explicitly supports this for download routes only).
      final token = await ApiClient.instance.getToken();
      final uri = ApiClient.instance
          .uri("/application_file/${Uri.encodeComponent(filename)}")
          .replace(queryParameters: {if (token != null) "token": token});
      final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
      if (!ok && mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(context.l10n.couldNotOpenFile)));
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(context.l10n.couldNotOpenFile)));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: Text(context.l10n.myApplicationsTitle),
        backgroundColor: context.colors.tertiary,
        actions: [
          IconButton(
            tooltip: context.l10n.refreshTooltip,
            icon: const Icon(Icons.refresh_rounded),
            onPressed: () {
              fetchApplications();
              PendingApplicationsQueue.instance.trySyncAll();
            },
          ),
        ],
      ),
      body: Column(
        children: [
          if (_showingCachedData) _buildOfflineBanner(),
          Expanded(child: _buildBody()),
        ],
      ),
    );
  }

  Widget _buildOfflineBanner() {
    final ago = _cachedAt == null
        ? ""
        : " (as of ${_cachedAt!.hour.toString().padLeft(2, '0')}:${_cachedAt!.minute.toString().padLeft(2, '0')})";
    final message = context.l10n.offlineShowingPreviousDataLabel(ago);
    return Semantics(
      liveRegion: true,
      label: message,
      child: Container(
        width: double.infinity,
        color: context.colors.warningBg,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.sm,
        ),
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
                message,
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
    );
  }

  Widget _buildPendingCard(PendingApplication item) {
    final isFailed = item.status == 'failed';
    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.md),
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: isFailed ? context.colors.errorBg : context.colors.warningBg,
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(
          color: (isFailed ? context.colors.error : context.colors.warning).withValues(
            alpha: 0.3,
          ),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  item.jobTitle,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ),
              StatusBadge(
                label: isFailed ? context.l10n.failedBadge : context.l10n.queuedOfflineBadge,
                color: isFailed ? context.colors.error : context.colors.warning,
                background: Colors.white,
                icon: isFailed
                    ? Icons.error_outline_rounded
                    : Icons.cloud_upload_outlined,
                dense: true,
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            isFailed
                ? (item.lastError ??
                      context.l10n.submissionFailedRetryHint)
                : context.l10n.willSubmitAutomatically,
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  icon: const Icon(Icons.refresh_rounded, size: 16),
                  label: Text(context.l10n.retryNowButton),
                  onPressed: () =>
                      PendingApplicationsQueue.instance.retry(item),
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              IconButton(
                tooltip: context.l10n.discardTooltip,
                icon: const Icon(Icons.delete_outline_rounded),
                onPressed: () =>
                    PendingApplicationsQueue.instance.discard(item),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildBody() {
    final hasAnything = applications.isNotEmpty || pending.isNotEmpty;
    return RefreshIndicator(
      onRefresh: () async {
        await fetchApplications();
        await PendingApplicationsQueue.instance.trySyncAll();
      },
      child: isLoading
          ? const SkeletonListView()
          : !hasAnything
          ? ListView(
              children: [
                const SizedBox(height: 120),
                EmptyState(
                  icon: Icons.inbox_outlined,
                  title: context.l10n.noApplicationsYetTitle,
                  subtitle: context.l10n.noApplicationsYetSubtitle,
                ),
              ],
            )
          : ListView.builder(
              padding: const EdgeInsets.all(AppSpacing.md),
              itemCount: pending.length + applications.length,
              itemBuilder: (context, index) {
                if (index < pending.length) {
                  return _buildPendingCard(pending[index]);
                }
                final app = applications[index - pending.length];
                final status = (app["status"] ?? "Pending").toString();

                final title = (app["job_title"] as String?)?.trim();
                final location = (app["job_location"] as String?)?.trim();
                final duration = (app["job_duration"] as String?)?.trim();

                final leadingTitle = (title != null && title.isNotEmpty)
                    ? title
                    : context.l10n.jobIdFallback("${app["job_id"]}");

                final hasCV = (app["cv_file"] as String?)?.isNotEmpty == true;
                final hasSupport =
                    (app["supporting_file"] as String?)?.isNotEmpty == true;
                // Gig-work rating lifecycle (see Job.job_type /
                // employer_to_worker & worker_to_employer Rating rows) --
                // only present on gig applications, see my_applications()
                // in app.py.
                final bool canRateEmployer = app["can_rate_employer"] == true;
                final employerRating = (app["employer_rating"] as Map?)
                    ?.cast<String, dynamic>();

                return Container(
                  margin: const EdgeInsets.only(bottom: AppSpacing.md),
                  // A colored left-accent stripe combined with a rounded
                  // corner needs a uniform-color Border on the decoration
                  // itself -- Flutter's Border painter asserts "A
                  // borderRadius can only be given on borders with uniform
                  // colors" and throws at paint time otherwise (real,
                  // reproduced crash caught via WorkHistoryScreen's widget
                  // test, which shares this same card-accent pattern). The
                  // accent is drawn as a separate slim child instead,
                  // clipped to the same rounded rect via clipBehavior, with
                  // a plain uniform outline border on the decoration (which
                  // radius is fine with).
                  clipBehavior: Clip.antiAlias,
                  decoration: BoxDecoration(
                    color: context.colors.surface,
                    borderRadius: BorderRadius.circular(AppRadius.md),
                    border: Border.all(color: context.colors.outline),
                    boxShadow: cardShadow,
                  ),
                  // IntrinsicHeight: a plain Row(crossAxisAlignment:
                  // stretch) needs a bounded height to stretch its children
                  // to, but this Container sits directly in a
                  // ListView.builder, which gives each item unbounded
                  // height -- real, reproduced crash ("BoxConstraints
                  // forces an infinite height") caught via
                  // WorkHistoryScreen's widget test, which shares this same
                  // pattern. IntrinsicHeight makes the Row size itself from
                  // its tallest child first, giving `stretch` something
                  // finite to stretch the accent stripe to.
                  child: IntrinsicHeight(
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        // Real gap found via user feedback: a near-white card
                        // on a near-white background, separated only by a 1px
                        // whisper-thin grey line and a 4%-opacity shadow,
                        // reads as flat and hard to see — especially on a
                        // budget phone screen in direct sunlight. A real 4px
                        // colored accent (Applications' own blue) gives every
                        // card an actual, reliably visible edge instead of
                        // relying on a shadow alone to separate it from the
                        // page.
                        Container(width: 4, color: context.colors.tertiary),
                        Expanded(
                          child: Padding(
                            padding: const EdgeInsets.all(AppSpacing.md),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Expanded(
                                      child: Text(
                                        leadingTitle,
                                        style: Theme.of(
                                          context,
                                        ).textTheme.titleMedium,
                                      ),
                                    ),
                                    StatusBadge.applicationStatus(context, status),
                                  ],
                                ),
                                if ((location != null && location.isNotEmpty) ||
                                    (duration != null &&
                                        duration.isNotEmpty)) ...[
                                  const SizedBox(height: 6),
                                  Row(
                                    children: [
                                      if (location != null &&
                                          location.isNotEmpty) ...[
                                        Icon(
                                          Icons.location_on_outlined,
                                          size: 15,
                                          color: context.colors.textMuted,
                                        ),
                                        const SizedBox(width: 3),
                                        Flexible(
                                          child: Text(
                                            location,
                                            style: Theme.of(
                                              context,
                                            ).textTheme.bodySmall,
                                          ),
                                        ),
                                      ],
                                      if (duration != null &&
                                          duration.isNotEmpty) ...[
                                        const SizedBox(width: 10),
                                        Icon(
                                          Icons.schedule_outlined,
                                          size: 15,
                                          color: context.colors.textMuted,
                                        ),
                                        const SizedBox(width: 3),
                                        Text(
                                          duration,
                                          style: Theme.of(
                                            context,
                                          ).textTheme.bodySmall,
                                        ),
                                      ],
                                    ],
                                  ),
                                ],
                                const SizedBox(height: AppSpacing.sm),
                                const Divider(height: 1),
                                const SizedBox(height: AppSpacing.sm),
                                Row(
                                  children: [
                                    Expanded(
                                      child: OutlinedButton.icon(
                                        icon: const Icon(
                                          Icons.chat_bubble_outline_rounded,
                                          size: 16,
                                        ),
                                        label: Text(context.l10n.messagesButtonLabel),
                                        onPressed: () {
                                          Navigator.push(
                                            context,
                                            MaterialPageRoute(
                                              builder: (_) => MessagesScreen(
                                                applicationId:
                                                    (app["id"] as num).toInt(),
                                                jobTitle: leadingTitle,
                                              ),
                                            ),
                                          );
                                        },
                                      ),
                                    ),
                                    if (hasCV) ...[
                                      const SizedBox(width: AppSpacing.sm),
                                      IconButton(
                                        tooltip: context.l10n.openCvTooltip,
                                        icon: const Icon(
                                          Icons.picture_as_pdf_outlined,
                                        ),
                                        style: IconButton.styleFrom(
                                          backgroundColor: context.colors.background,
                                          shape: RoundedRectangleBorder(
                                            borderRadius: BorderRadius.circular(
                                              AppRadius.sm,
                                            ),
                                          ),
                                        ),
                                        onPressed: () =>
                                            _openFile(app["cv_file"]),
                                      ),
                                    ],
                                    if (hasSupport) ...[
                                      const SizedBox(width: AppSpacing.xs),
                                      IconButton(
                                        tooltip: context.l10n.openSupportingDocTooltip,
                                        icon: const Icon(
                                          Icons.attach_file_rounded,
                                        ),
                                        style: IconButton.styleFrom(
                                          backgroundColor: context.colors.background,
                                          shape: RoundedRectangleBorder(
                                            borderRadius: BorderRadius.circular(
                                              AppRadius.sm,
                                            ),
                                          ),
                                        ),
                                        onPressed: () =>
                                            _openFile(app["supporting_file"]),
                                      ),
                                    ],
                                  ],
                                ),
                                if (canRateEmployer ||
                                    employerRating != null) ...[
                                  const SizedBox(height: AppSpacing.sm),
                                  const Divider(height: 1),
                                  const SizedBox(height: AppSpacing.sm),
                                  if (canRateEmployer)
                                    SizedBox(
                                      width: double.infinity,
                                      child: OutlinedButton.icon(
                                        style: OutlinedButton.styleFrom(
                                          foregroundColor: context.colors.secondary,
                                          side: BorderSide(
                                            color: context.colors.secondary,
                                          ),
                                        ),
                                        icon: const Icon(
                                          Icons.star_outline_rounded,
                                          size: 16,
                                        ),
                                        label: Text(context.l10n.rateThisEmployerButton),
                                        onPressed: () => _rateEmployer(
                                          (app["id"] as num).toInt(),
                                        ),
                                      ),
                                    )
                                  else if (employerRating != null)
                                    Row(
                                      children: [
                                        StatusBadge(
                                          label: context.l10n.youRatedLabel(
                                            "${employerRating["score"]}",
                                          ),
                                          color: context.colors.secondary,
                                          background: context.colors.secondaryLight,
                                          icon: Icons.star_rounded,
                                          dense: true,
                                        ),
                                        const Spacer(),
                                        TextButton(
                                          onPressed: () => _disputeRating(
                                            (employerRating["id"] as num)
                                                .toInt(),
                                          ),
                                          child: Text(context.l10n.disputeButton),
                                        ),
                                      ],
                                    ),
                                ],
                              ],
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                );
              },
            ),
    );
  }
}

class MyApplicationsScreen extends StatefulWidget {
  final int userId;
  const MyApplicationsScreen({super.key, required this.userId});

  @override
  MyApplicationsScreenState createState() => MyApplicationsScreenState();
}
