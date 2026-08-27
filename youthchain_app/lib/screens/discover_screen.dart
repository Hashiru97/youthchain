import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';
import '../widgets/name_with_badge.dart';
import '../widgets/save_job_button.dart';
import '../widgets/status_badge.dart';
import 'discover_job_detail_screen.dart';
import 'old_listings_screen.dart';
import 'saved_searches_screen.dart';

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
  bool _savingSearch = false;
  // Bumped on every fetchJobs() call so a slow, superseded request can tell
  // it's stale once it resolves and skip applying its (now out-of-date)
  // results -- otherwise two overlapping searches (debounce fires again
  // while the previous request is still in flight) can race, and whichever
  // response lands last wins even if it's for a search term no longer in
  // the box.
  int _fetchGeneration = 0;

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
    final generation = ++_fetchGeneration;
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
      if (!mounted || generation != _fetchGeneration) return;
      jobs = json.decode(result.body) as List;
      _showingCachedJobs = result.fromCache;
    } on NoCachedDataException {
      if (!mounted || generation != _fetchGeneration) return;
      jobs = [];
      _showingCachedJobs = false;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.noConnectionNoPreviousJobs)),
      );
    } catch (_) {
      if (!mounted || generation != _fetchGeneration) return;
      jobs = [];
      _showingCachedJobs = false;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorLoadingJobs)),
      );
    } finally {
      if (mounted && generation == _fetchGeneration) setState(() => isLoading = false);
    }
  }

  void _onSearchChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), () {
      setState(() => _searchQuery = value.trim());
      fetchJobs();
    });
  }

  /// Saves the current free-text search as a standing alert (POST
  /// /api/saved_searches) -- Discover's search bar only ever drives the
  /// "q" filter (see fetchJobs()), so that's the only field this sends;
  /// location/skill exist on the backend model for a future filter UI,
  /// not because this button has any way to set them today.
  Future<void> _saveCurrentSearch() async {
    final q = _searchQuery.trim();
    if (q.isEmpty || _savingSearch) return;

    setState(() => _savingSearch = true);
    try {
      final res = await ApiClient.instance.postJson("/api/saved_searches", {"q": q});
      if (!mounted) return;
      if (res.statusCode == 201) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.l10n.alertSetForSearch(q))),
        );
      } else {
        final body = json.decode(res.body);
        final msg = body is Map && body["error"] is String
            ? body["error"] as String
            : context.l10n.couldNotSaveSearch;
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorSavingSearch)),
      );
    } finally {
      if (mounted) setState(() => _savingSearch = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: Text(l10n.discoverTitle),
        backgroundColor: context.colors.tertiary,
        actions: [
          IconButton(
            icon: const Icon(Icons.notifications_outlined, color: Colors.white),
            tooltip: l10n.savedSearchesTooltip,
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const SavedSearchesScreen()),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.history_rounded, color: Colors.white),
            tooltip: l10n.oldListingsTooltip,
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
                hintText: l10n.searchOpportunitiesHint,
                prefixIcon: const Icon(Icons.search_rounded),
                // Only meaningful once there's something to alert on --
                // mirrors POST /api/saved_searches' own "at least one
                // filter" requirement rather than surfacing a bell that
                // would just 400 on an empty search.
                suffixIcon: _searchQuery.isEmpty
                    ? null
                    : IconButton(
                        icon: _savingSearch
                            ? const SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.notifications_outlined),
                        tooltip: l10n.alertMeForSearchTooltip,
                        onPressed: _savingSearch ? null : _saveCurrentSearch,
                      ),
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
                l10n.showingCachedResults,
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
                                    title: l10n.noListingsFound,
                                    subtitle: _searchQuery.isEmpty
                                        ? l10n.checkBackSoonListings
                                        : l10n.tryDifferentSearchTerm,
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
    final l10n = context.l10n;
    final title = (job["title"] as String?) ?? l10n.untitledListing;
    final location = (job["location"] as String?) ?? "";
    final companyName = job["company_name"] as String?;
    final companyVerified = job["company_verified"] == true;
    final scamSignals = (job["scam_signals"] as List?)?.cast<String>() ?? const [];
    final sourceName = job["source_name"] as String?;
    final salary = job["salary"] as String?;
    final employmentType = job["employment_type"] as String?;
    final jobId = (job["id"] as num).toInt();
    // Absent entirely when unranked (no saved profile yet, or an active
    // search — ranking and free-text search don't mix, same rule Home's
    // own /api/match_jobs follows), same convention job_screen.dart
    // already relies on so a card never shows a misleading "0% match"
    // before ranking is actually meaningful.
    final bool hasScore = job.containsKey("score");
    final int score = (job["score"] ?? 0) as int;

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
                  if (hasScore) ...[
                    const SizedBox(width: 6),
                    StatusBadge.matchScore(context, score),
                  ],
                  SizedBox(
                    width: 36,
                    height: 36,
                    child: SaveJobButton(jobId: jobId, initiallySaved: saved, size: 20, onChanged: onSavedChanged),
                  ),
                ],
              ),
              if (companyName != null && companyName.isNotEmpty) ...[
                const SizedBox(height: 3),
                // Same fix as job_screen.dart's identical employer-name +
                // verification-badge row -- see NameWithBadge's own
                // docstring for why this needs to be more than a plain
                // Row + Expanded.
                NameWithBadge(
                  name: companyName,
                  style: Theme.of(context).textTheme.bodySmall,
                  spacing: 4,
                  badge: companyVerified
                      ? StatusBadge(
                          label: l10n.verifiedBadge,
                          color: context.colors.success,
                          background: context.colors.successBg,
                          icon: Icons.verified_rounded,
                          dense: true,
                        )
                      : null,
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
                    label: sourceName ?? l10n.scrapedListingBadge,
                    color: context.colors.textMuted,
                    background: context.colors.background,
                    icon: Icons.travel_explore_rounded,
                    dense: true,
                  ),
                  if (scamSignals.isNotEmpty)
                    Tooltip(
                      message: l10n.scamWarningTooltip(
                        scamSignals.map((s) => _scamSignalLabel(l10n, s)).join(', '),
                      ),
                      child: StatusBadge.scamWarning(context),
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

/// Translates a raw Job.scam_signals entry (see scanner.scam_signals in
/// the backend -- "fee_request" | "personal_email" | "vague_pay") into
/// the tooltip's own human-readable phrase. Falls back to the raw signal
/// name for anything this build doesn't recognize yet (an older mobile
/// build talking to a newer backend that's grown a signal type this
/// switch doesn't know about) rather than crashing or hiding the
/// warning entirely -- the badge itself still shows either way.
String _scamSignalLabel(AppLocalizations l10n, String signal) {
  switch (signal) {
    case 'fee_request':
      return l10n.scamSignalFeeRequest;
    case 'personal_email':
      return l10n.scamSignalPersonalEmail;
    case 'vague_pay':
      return l10n.scamSignalVaguePay;
    default:
      return signal;
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
