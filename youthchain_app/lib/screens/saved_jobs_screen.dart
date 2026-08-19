import 'dart:convert';

import 'package:flutter/material.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';
import '../widgets/save_job_button.dart';
import '../widgets/status_badge.dart';
import 'discover_job_detail_screen.dart';
import 'job_detail_screen.dart';

/// A user's bookmarked jobs — Home (employer/gig) and Discover (scraped)
/// mixed in one list, most-recently-saved first, since GET
/// /api/saved_jobs already returns both from the one shared table (see
/// SavedJob's own docstring in app.py). Told apart the same way any
/// other mixed job list in this app would be: each job's own "source"
/// field decides which detail screen to open.
///
/// Applying to an employer job from here deliberately does not open the
/// full CV-upload sheet — that flow (file picking, upload, the BL-37
/// offline write-queue) lives entirely in JobScreenState._showApplySheet,
/// tightly coupled to that screen's own instance state. Reimplementing
/// or extracting it just for this list would be a much bigger, riskier
/// change than "add a bookmark screen" — so JobDetailScreen's onApply
/// here instead sends the user back to Home, where the real flow
/// already works.
class SavedJobsScreen extends StatefulWidget {
  final int userId;

  const SavedJobsScreen({super.key, required this.userId});

  @override
  State<SavedJobsScreen> createState() => _SavedJobsScreenState();
}

class _SavedJobsScreenState extends State<SavedJobsScreen> {
  List jobs = [];
  Set<int> appliedJobIds = {};
  bool isLoading = true;
  bool _showingCachedJobs = false;

  @override
  void initState() {
    super.initState();
    _fetchAll();
  }

  Future<void> _fetchAll() async {
    if (!isLoading) setState(() => isLoading = true);
    await Future.wait([_fetchSavedJobs(), _fetchAppliedJobIds()]);
    if (mounted) setState(() => isLoading = false);
  }

  Future<void> _fetchSavedJobs() async {
    try {
      // Same cache-fallback contract as every other job list in this
      // app (JobScreen, DiscoverScreen, MyApplicationsScreen) — a
      // network failure shows the last successfully loaded copy rather
      // than an empty screen.
      final result = await ApiClient.instance.getWithCache("/api/saved_jobs");
      if (!mounted) return;
      setState(() {
        jobs = json.decode(result.body) as List;
        _showingCachedJobs = result.fromCache;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        jobs = [];
        _showingCachedJobs = false;
      });
    }
  }

  Future<void> _fetchAppliedJobIds() async {
    try {
      final res = await ApiClient.instance
          .get("/my_applications/${widget.userId}")
          .timeout(ApiClient.timeout);
      if (!mounted || res.statusCode != 200) return;
      final List list = json.decode(res.body);
      setState(() {
        appliedJobIds = list.map<int>((e) => (e["job_id"] as num).toInt()).toSet();
      });
    } catch (_) {
      // Best-effort -- worst case an already-applied job's detail
      // screen shows "Apply with CV" instead of "Applied", same
      // graceful-degradation the button already handles for a missing
      // employer_id or job_type.
    }
  }

  void _removeFromList(int jobId) {
    setState(() => jobs.removeWhere((j) => (j["id"] as num).toInt() == jobId));
  }

  Future<void> _openJob(Map<String, dynamic> job) async {
    final jobId = (job["id"] as num).toInt();
    final bool isScraped = job["source"] == "scraped";

    if (isScraped) {
      await Navigator.push(
        context,
        MaterialPageRoute(builder: (_) => DiscoverJobDetailScreen(job: job, initiallySaved: true)),
      );
    } else {
      await Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => JobDetailScreen(
            job: job,
            alreadyApplied: appliedJobIds.contains(jobId),
            initiallySaved: true,
            onApply: () {
              Navigator.of(context).pop();
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(content: Text(context.l10n.openJobFromHomeToApply)),
              );
            },
          ),
        ),
      );
    }
    // The detail screen's own bookmark button may have unsaved it —
    // re-check rather than assume this list is still accurate.
    if (mounted) _fetchSavedJobs();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: Text(context.l10n.savedJobsMenuItem),
        backgroundColor: context.colors.primary,
      ),
      body: Column(
        children: [
          if (_showingCachedJobs)
            Container(
              width: double.infinity,
              color: context.colors.warningBg,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: 6),
              child: Text(
                context.l10n.showingCachedResults,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(color: context.colors.warning),
              ),
            ),
          Expanded(
            child: isLoading
                ? const Center(child: CircularProgressIndicator())
                // RefreshIndicator must wrap the empty branch too, not
                // just the populated one -- otherwise pull-to-refresh
                // silently does nothing once the list is empty (real
                // bug found and fixed the same way on DiscoverScreen/
                // OldListingsScreen/JobScreen).
                : RefreshIndicator(
                    onRefresh: _fetchAll,
                    child: jobs.isEmpty
                        ? ListView(
                            children: [
                              const SizedBox(height: 120),
                              EmptyState(
                                icon: Icons.bookmark_border_rounded,
                                title: context.l10n.noSavedJobsYetTitle,
                                subtitle: context.l10n.noSavedJobsYetSubtitle,
                              ),
                            ],
                          )
                        : ListView.builder(
                            padding: const EdgeInsets.all(AppSpacing.md),
                            itemCount: jobs.length,
                            itemBuilder: (context, index) {
                              final job = (jobs[index] as Map).cast<String, dynamic>();
                              return _SavedJobCard(
                                job: job,
                                onTap: () => _openJob(job),
                                onUnsaved: () => _removeFromList((job["id"] as num).toInt()),
                              );
                            },
                          ),
                  ),
          ),
        ],
      ),
    );
  }
}

class _SavedJobCard extends StatelessWidget {
  final Map<String, dynamic> job;
  final VoidCallback onTap;
  final VoidCallback onUnsaved;

  const _SavedJobCard({required this.job, required this.onTap, required this.onUnsaved});

  @override
  Widget build(BuildContext context) {
    final title = (job["title"] as String?) ?? "Untitled job";
    final location = (job["location"] as String?) ?? "";
    final bool isScraped = job["source"] == "scraped";
    final employer = (job["employer"] as Map?)?.cast<String, dynamic>();
    final companyName = isScraped ? job["company_name"] as String? : employer?["name"] as String?;
    final jobId = (job["id"] as num).toInt();

    return Card(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      color: context.colors.surface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AppRadius.md)),
      child: InkWell(
        borderRadius: BorderRadius.circular(AppRadius.md),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: Theme.of(context).textTheme.titleMedium),
                    if (companyName != null && companyName.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text(
                        companyName,
                        style: Theme.of(context).textTheme.bodySmall,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ],
                    const SizedBox(height: 6),
                    Wrap(
                      spacing: 8,
                      runSpacing: 4,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        if (location.isNotEmpty)
                          Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(Icons.location_on_outlined, size: 14, color: context.colors.textMuted),
                              const SizedBox(width: 3),
                              Text(location, style: Theme.of(context).textTheme.bodySmall),
                            ],
                          ),
                        StatusBadge(
                          label: isScraped
                              ? (job["source_name"] as String? ?? context.l10n.discoverSourceBadge)
                              : context.l10n.homeSourceBadge,
                          color: context.colors.textMuted,
                          background: context.colors.background,
                          icon: isScraped ? Icons.travel_explore_rounded : Icons.work_outline_rounded,
                          dense: true,
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              SizedBox(
                width: 36,
                height: 36,
                child: SaveJobButton(
                  jobId: jobId,
                  initiallySaved: true,
                  size: 20,
                  onChanged: (saved) {
                    if (!saved) onUnsaved();
                  },
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
