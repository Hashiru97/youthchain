import 'dart:convert';

import 'package:flutter/material.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';
import 'discover_job_detail_screen.dart';
import 'discover_screen.dart';

/// Read-only view of Discover listings past their stated application
/// deadline — GET /api/discover_jobs/old on the backend. These rows are
/// wiped by scanner/reaper.py roughly a week after their deadline (see
/// REAP_GRACE_DAYS there), so this list is naturally short-lived; there's
/// no pagination or search here because of that, unlike DiscoverScreen.
class OldListingsScreen extends StatefulWidget {
  const OldListingsScreen({super.key});

  @override
  State<OldListingsScreen> createState() => _OldListingsScreenState();
}

class _OldListingsScreenState extends State<OldListingsScreen> {
  List jobs = [];
  bool isLoading = true;
  Set<int> _savedJobIds = {};

  @override
  void initState() {
    super.initState();
    _fetchOldJobs();
    _fetchSavedJobIds();
  }

  Future<void> _fetchSavedJobIds() async {
    try {
      final res = await ApiClient.instance.get("/api/saved_jobs");
      if (!mounted || res.statusCode != 200) return;
      final List saved = json.decode(res.body) as List;
      setState(() {
        _savedJobIds = saved.map<int>((j) => ((j as Map)["id"] as num).toInt()).toSet();
      });
    } catch (_) {
      // Best-effort, same convention as DiscoverScreen._fetchSavedJobIds.
    }
  }

  Future<void> _fetchOldJobs() async {
    if (!isLoading) setState(() => isLoading = true);
    try {
      final res = await ApiClient.instance.get("/api/discover_jobs/old");
      if (!mounted) return;
      if (res.statusCode == 200) {
        jobs = json.decode(res.body) as List;
      } else {
        jobs = [];
      }
    } catch (_) {
      if (!mounted) return;
      jobs = [];
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorLoadingOldListings)),
      );
    } finally {
      if (mounted) setState(() => isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: Text(l10n.oldListingsPageTitle),
        backgroundColor: context.colors.tertiary,
      ),
      body: isLoading
          ? const SkeletonListView()
          // RefreshIndicator needs a scrollable descendant to detect the
          // pull gesture at all -- previously only wrapped the non-empty
          // ListView.builder case, so pulling down on the empty state did
          // nothing (no visible spinner, no re-fetch): a real listing that
          // had since aged into "old" stayed invisible until the user left
          // the screen and came back. Wrapping both branches in the same
          // RefreshIndicator, with AlwaysScrollableScrollPhysics on the
          // empty branch so it's draggable even though its content doesn't
          // fill/overflow the viewport, fixes that for both cases.
          : RefreshIndicator(
              onRefresh: _fetchOldJobs,
              child: jobs.isEmpty
                  // EmptyState centers itself via an internal Center widget,
                  // which needs bounded height to do that -- a bare ListView
                  // gives its children unbounded height, so without this
                  // LayoutBuilder the empty state would shrink-wrap to the
                  // top of the screen instead of sitting in the middle.
                  ? LayoutBuilder(
                      builder: (context, constraints) => ListView(
                        physics: const AlwaysScrollableScrollPhysics(),
                        children: [
                          SizedBox(
                            height: constraints.maxHeight,
                            child: EmptyState(
                              icon: Icons.history_rounded,
                              title: l10n.noOldListings,
                              subtitle: l10n.oldListingsEmptySubtitle,
                            ),
                          ),
                        ],
                      ),
                    )
                  : ListView.builder(
                      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: AppSpacing.sm),
                      itemCount: jobs.length,
                      itemBuilder: (context, index) {
                        final job = (jobs[index] as Map).cast<String, dynamic>();
                        final jobId = (job["id"] as num).toInt();
                        return DiscoverJobCard(
                          job: job,
                          saved: _savedJobIds.contains(jobId),
                          onSavedChanged: (saved) => setState(() {
                            if (saved) {
                              _savedJobIds.add(jobId);
                            } else {
                              _savedJobIds.remove(jobId);
                            }
                          }),
                          onTap: () async {
                            await Navigator.push(
                              context,
                              MaterialPageRoute(
                                builder: (_) => DiscoverJobDetailScreen(
                                  job: job,
                                  initiallySaved: _savedJobIds.contains(jobId),
                                ),
                              ),
                            );
                            if (mounted) _fetchSavedJobIds();
                          },
                        );
                      },
                    ),
            ),
    );
  }
}
