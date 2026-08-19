import 'dart:convert';

import 'package:flutter/material.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';

/// Manages Saved Search alerts (GET/POST/delete /api/saved_searches) --
/// created from DiscoverScreen's "Alert me" action on the current search,
/// listed and removable here. Each saved search fires a push/in-app
/// notification the next time a scanned job matches it (see backend
/// app._dispatch_job_alerts_for_scan); this screen has no notion of "past
/// matches", only the standing filters themselves.
class SavedSearchesScreen extends StatefulWidget {
  const SavedSearchesScreen({super.key});

  @override
  State<SavedSearchesScreen> createState() => _SavedSearchesScreenState();
}

class _SavedSearchesScreenState extends State<SavedSearchesScreen> {
  List<Map<String, dynamic>> _searches = [];
  bool _isLoading = true;
  final Set<int> _deleting = {};

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    if (!_isLoading) setState(() => _isLoading = true);
    try {
      final res = await ApiClient.instance.get("/api/saved_searches");
      if (!mounted) return;
      if (res.statusCode == 200) {
        final List rows = json.decode(res.body) as List;
        _searches = rows.map((r) => (r as Map).cast<String, dynamic>()).toList();
      } else {
        _searches = [];
      }
    } catch (_) {
      if (!mounted) return;
      _searches = [];
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorLoadingSavedSearches)),
      );
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _delete(int id) async {
    setState(() => _deleting.add(id));
    try {
      final res = await ApiClient.instance.postJson("/api/saved_searches/$id/delete", {});
      if (!mounted) return;
      if (res.statusCode == 200) {
        setState(() => _searches.removeWhere((s) => (s["id"] as num).toInt() == id));
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.l10n.couldNotDeleteSavedSearch)),
        );
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorDeletingSavedSearch)),
      );
    } finally {
      if (mounted) setState(() => _deleting.remove(id));
    }
  }

  String _describe(AppLocalizations l10n, Map<String, dynamic> search) {
    final parts = <String>[];
    final q = search["q"] as String?;
    final location = search["location"] as String?;
    final skill = search["skill"] as String?;
    if (q != null && q.isNotEmpty) parts.add('"$q"');
    if (skill != null && skill.isNotEmpty) parts.add(l10n.savedSearchSkillLabel(skill));
    if (location != null && location.isNotEmpty) parts.add(l10n.savedSearchLocationLabel(location));
    return parts.isEmpty ? l10n.anyNewListing : parts.join(" · ");
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: Text(l10n.savedSearchesPageTitle),
        backgroundColor: context.colors.tertiary,
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _fetch,
              child: _searches.isEmpty
                  ? LayoutBuilder(
                      builder: (context, constraints) => ListView(
                        physics: const AlwaysScrollableScrollPhysics(),
                        children: [
                          SizedBox(
                            height: constraints.maxHeight,
                            child: EmptyState(
                              icon: Icons.notifications_outlined,
                              title: l10n.noSavedSearchesYet,
                              subtitle: l10n.savedSearchesEmptySubtitle,
                            ),
                          ),
                        ],
                      ),
                    )
                  : ListView.builder(
                      padding: const EdgeInsets.all(AppSpacing.md),
                      itemCount: _searches.length,
                      itemBuilder: (context, index) {
                        final search = _searches[index];
                        final id = (search["id"] as num).toInt();
                        return Card(
                          margin: const EdgeInsets.only(bottom: AppSpacing.sm),
                          color: context.colors.surface,
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(AppRadius.md),
                          ),
                          child: ListTile(
                            leading: Icon(Icons.notifications_active_outlined, color: context.colors.tertiary),
                            title: Text(_describe(l10n, search)),
                            trailing: _deleting.contains(id)
                                ? const SizedBox(
                                    width: 20,
                                    height: 20,
                                    child: CircularProgressIndicator(strokeWidth: 2),
                                  )
                                : IconButton(
                                    icon: const Icon(Icons.delete_outline_rounded),
                                    tooltip: l10n.deleteTooltip,
                                    onPressed: () => _delete(id),
                                  ),
                          ),
                        );
                      },
                    ),
            ),
    );
  }
}
