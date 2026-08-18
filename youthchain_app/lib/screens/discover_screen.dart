import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';

import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';
import '../widgets/save_job_button.dart';
import '../widgets/status_badge.dart';
import 'discover_job_detail_screen.dart';
import 'old_listings_screen.dart';

/// The scraped-listings feed — real jobs pulled in by the job scanner
/// (backend/scanner/) from configured external sources, kept entirely
/// separate from JobScreen's employer/gig feed. See GET
/// /api/discover_jobs's own docstring in app.py for why this is a
/// distinct endpoint rather than a filter on GET /jobs.
///
/// Deliberately simpler than JobScreen: no Socket.IO real-time updates, no
/// candidate-skill ranking (scraped jobs are unranked, matching /jobs's
/// own plain fallback), no in-app apply flow (a scraped listing's "apply"
/// action opens its external apply_url — see DiscoverJobDetailScreen).
class DiscoverScreen extends StatefulWidget {
  final int userId;

  const DiscoverScreen({super.key, required this.userId});

  @override
  State<DiscoverScreen> createState() => _DiscoverScreenState();
}

class _DiscoverScreenState extends State<DiscoverScreen> {
  List jobs = [];
  bool isLoading = true;
  bool _showingCachedJobs = false;
  Set<int> _savedJobIds = {};

  final _searchController = TextEditingController();
  String _searchQuery = "";
  Timer? _debounce;

  @override
  void initState() {
    super.initState();
    fetchJobs();
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
      // Best-effort: every SaveJobButton still works standalone (it
      // just starts unsaved-looking until the next successful fetch),
      // so a failure here isn't worth an error banner of its own.
    }
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _searchController.dispose();
    super.dispose();
  }

  Future<void> fetchJobs() async {
    if (!isLoading) setState(() => isLoading = true);
    try {
      final params = <String, String>{
        if (_searchQuery.isNotEmpty) "q": _searchQuery,
      };
      final path = Uri(path: "/api/discover_jobs", queryParameters: params.isEmpty ? null : params).toString();
      // Same cache-fallback contract as JobScreen.fetchJobs() — a network
      // failure shows the last successfully loaded copy instead of an
      // empty screen, not a new behavior invented for this screen.
      final result = await ApiClient.instance.getWithCache(path);
      if (!mounted) return;
      jobs = json.decode(result.body) as List;
      _showingCachedJobs = result.fromCache;
    } on NoCachedDataException {
      if (!mounted) return;
      jobs = [];
      _showingCachedJobs = false;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("No connection and no previously loaded jobs.")),
      );
    } catch (_) {
      if (!mounted) return;
      jobs = [];
      _showingCachedJobs = false;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Network error loading jobs.")),
      );
    } finally {
      if (mounted) setState(() => isLoading = false);
    }
  }

  void _onSearchChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), () {
      setState(() => _searchQuery = value.trim());
      fetchJobs();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: const Text("Discover"),
        backgroundColor: context.colors.tertiary,
        actions: [
          IconButton(
            icon: const Icon(Icons.history_rounded, color: Colors.white),
            tooltip: "Old listings",
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const OldListingsScreen()),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(AppSpacing.md),
            child: TextField(
              controller: _searchController,
              onChanged: _onSearchChanged,
              decoration: InputDecoration(
                hintText: "Search opportunities",
                prefixIcon: const Icon(Icons.search_rounded),
                filled: true,
                fillColor: context.colors.surface,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(AppRadius.md),
                  borderSide: BorderSide.none,
                ),
              ),
            ),
          ),
          if (_showingCachedJobs)
            Container(
              width: double.infinity,
              color: context.colors.warningBg,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: 6),
              child: Text(
                "Showing previously loaded results — offline or connection issue.",
                style: Theme.of(context).textTheme.bodySmall?.copyWith(color: context.colors.warning),
              ),
            ),
          Expanded(
            child: isLoading
                ? const SkeletonListView()
                // Same fix as OldListingsScreen: RefreshIndicator needs a
                // scrollable descendant to detect the pull gesture, so it
                // must wrap the empty state too (via a LayoutBuilder+
                // SizedBox so EmptyState's own internal Center still gets
                // bounded height to center in, rather than shrink-wrapping
                // to the top of a bare ListView) -- otherwise pulling to
                // refresh from "No listings found" silently does nothing.
                : RefreshIndicator(
                    onRefresh: fetchJobs,
                    child: jobs.isEmpty
                        ? LayoutBuilder(
                            builder: (context, constraints) => ListView(
                              physics: const AlwaysScrollableScrollPhysics(),
                              children: [
                                SizedBox(
                                  height: constraints.maxHeight,
                                  child: EmptyState(
                                    icon: Icons.travel_explore_rounded,
                                    title: "No listings found",
                                    subtitle: _searchQuery.isEmpty
                                        ? "Check back soon — sources are scanned regularly."
                                        : "Try a different search term.",
                                  ),
                                ),
                              ],
                            ),
                          )
                        : ListView.builder(
                          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
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
                                // Catches a toggle made on the detail
                                // screen itself, which this card's own
                                // local state has no other way to learn
                                // about after the pop.
                                if (mounted) _fetchSavedJobIds();
                              },
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

class DiscoverJobCard extends StatelessWidget {
  final Map<String, dynamic> job;
  final bool saved;
  final ValueChanged<bool> onSavedChanged;
  final VoidCallback onTap;

  const DiscoverJobCard({
    super.key,
    required this.job,
    required this.saved,
    required this.onSavedChanged,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final title = (job["title"] as String?) ?? "Untitled listing";
    final location = (job["location"] as String?) ?? "";
    final companyName = job["company_name"] as String?;
    final companyVerified = job["company_verified"] == true;
    final sourceName = job["source_name"] as String?;
    final salary = job["salary"] as String?;
    final employmentType = job["employment_type"] as String?;
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
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(child: Text(title, style: Theme.of(context).textTheme.titleMedium)),
                  SizedBox(
                    width: 36,
                    height: 36,
                    child: SaveJobButton(jobId: jobId, initiallySaved: saved, size: 20, onChanged: onSavedChanged),
                  ),
                ],
              ),
              if (companyName != null && companyName.isNotEmpty) ...[
                const SizedBox(height: 3),
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        companyName,
                        style: Theme.of(context).textTheme.bodySmall,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    if (companyVerified) ...[
                      const SizedBox(width: 4),
                      StatusBadge(
                        label: "Verified",
                        color: context.colors.success,
                        background: context.colors.successBg,
                        icon: Icons.verified_rounded,
                        dense: true,
                      ),
                    ],
                  ],
                ),
              ],
              const SizedBox(height: 6),
              Wrap(
                spacing: 8,
                runSpacing: 4,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  if (location.isNotEmpty)
                    _IconLabel(icon: Icons.location_on_outlined, label: location),
                  if (salary != null && salary.isNotEmpty)
                    _IconLabel(icon: Icons.payments_outlined, label: salary),
                  if (employmentType != null && employmentType.isNotEmpty)
                    _IconLabel(icon: Icons.work_outline_rounded, label: employmentType),
                  StatusBadge(
                    label: sourceName ?? "Scraped listing",
                    color: context.colors.textMuted,
                    background: context.colors.background,
                    icon: Icons.travel_explore_rounded,
                    dense: true,
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _IconLabel extends StatelessWidget {
  final IconData icon;
  final String label;
  const _IconLabel({required this.icon, required this.label});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 14, color: context.colors.textMuted),
        const SizedBox(width: 3),
        Text(label, style: Theme.of(context).textTheme.bodySmall),
      ],
    );
  }
}
