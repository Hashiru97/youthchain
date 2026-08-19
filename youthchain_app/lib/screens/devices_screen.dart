import 'dart:convert';

import 'package:flutter/material.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';

/// "Connected devices" -- the mobile counterpart to the web portal's
/// /portal/devices (see UserSession in app.py). Lets a youth see every
/// phone and browser currently signed into their account and kill one
/// remotely -- a lost/stolen phone, or a shared-device login they forgot
/// to log out of, previously had no self-service fix at all.
class DevicesScreen extends StatefulWidget {
  const DevicesScreen({super.key});

  @override
  State<DevicesScreen> createState() => _DevicesScreenState();
}

class _DevicesScreenState extends State<DevicesScreen> {
  bool _isLoading = true;
  List _sessions = [];
  String? _error;
  final Set<int> _revoking = {};

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });
    try {
      final res = await ApiClient.instance.get('/api/devices');
      if (!mounted) return;
      if (res.statusCode == 200) {
        final body = json.decode(res.body);
        setState(() {
          _sessions = (body['sessions'] as List?) ?? [];
          _isLoading = false;
        });
      } else {
        setState(() {
          _error = context.l10n.couldNotLoadDevices;
          _isLoading = false;
        });
      }
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = context.l10n.networkErrorLoadingDevices;
        _isLoading = false;
      });
    }
  }

  Future<void> _confirmAndRevoke(Map session) async {
    final bool isCurrent = session['is_current'] == true;
    final int id = (session['id'] as num).toInt();
    final l10n = context.l10n;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.revokeThisDeviceTitle),
        content: Text(
          isCurrent
              ? l10n.revokeCurrentDeviceWarning
              : l10n.revokeOtherDeviceWarning(
                  session['device_label'] ?? l10n.thisDeviceLabel,
                ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(l10n.cancelButton),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: context.colors.error),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: Text(l10n.revokeButton),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    setState(() => _revoking.add(id));
    try {
      final res = await ApiClient.instance.postJson(
        '/api/devices/$id/revoke',
        {},
      );
      if (!mounted) return;
      if (res.statusCode == 200) {
        if (isCurrent) {
          // The token used for this very call is now blocklisted -- next
          // authenticated request anywhere in the app will 401 and
          // ApiClient's own session-expiry handling takes it from there.
          // Pop back out rather than staying on a screen that can no
          // longer legitimately load anything.
          Navigator.of(context).pop();
          return;
        }
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(context.l10n.deviceRevoked)));
        await _fetch();
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.l10n.couldNotRevokeDevice)),
        );
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(context.l10n.networkErrorGeneric)));
    } finally {
      if (mounted) setState(() => _revoking.remove(id));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: Text(context.l10n.devicesMenuItem),
        actions: [
          IconButton(
            tooltip: context.l10n.refreshTooltip,
            icon: const Icon(Icons.refresh_rounded),
            onPressed: _fetch,
          ),
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_isLoading) {
      return const Center(child: CircularProgressIndicator());
    }
    // Both the error and empty states must be wrapped in RefreshIndicator
    // too, not just the populated list below -- otherwise pull-to-refresh
    // silently does nothing in either case (same bug found and fixed
    // across DiscoverScreen/OldListingsScreen/JobScreen/SavedJobsScreen/
    // WorkHistoryScreen). The AppBar's own manual refresh button is a
    // partial mitigation here, but the gesture should work too.
    if (_error != null) {
      return RefreshIndicator(
        onRefresh: _fetch,
        child: ListView(
          children: [
            const SizedBox(height: 120),
            EmptyState(
              icon: Icons.error_outline_rounded,
              title: context.l10n.somethingWentWrong,
              subtitle: _error!,
            ),
          ],
        ),
      );
    }
    if (_sessions.isEmpty) {
      return RefreshIndicator(
        onRefresh: _fetch,
        child: ListView(
          children: [
            const SizedBox(height: 120),
            EmptyState(
              icon: Icons.devices_other_rounded,
              title: context.l10n.noActiveSessions,
            ),
          ],
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _fetch,
      child: ListView.builder(
        padding: const EdgeInsets.all(AppSpacing.md),
        itemCount: _sessions.length,
        itemBuilder: (context, index) =>
            _buildDeviceCard(_sessions[index] as Map),
      ),
    );
  }

  Widget _buildDeviceCard(Map session) {
    final bool isCurrent = session['is_current'] == true;
    final bool isApp = session['channel'] == 'app';
    final int id = (session['id'] as num).toInt();
    final String label = (session['device_label'] as String?) ?? context.l10n.unknownDeviceLabel;
    final String? lastSeenRaw = session['last_seen_at'] as String?;
    final lastSeen = lastSeenRaw != null ? DateTime.tryParse(lastSeenRaw) : null;
    final bool isRevoking = _revoking.contains(id);

    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.md),
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: context.colors.surface,
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(color: context.colors.outline),
        boxShadow: cardShadow,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              color: context.colors.primaryLight,
              borderRadius: BorderRadius.circular(AppRadius.sm),
            ),
            child: Icon(
              isApp ? Icons.smartphone_rounded : Icons.desktop_windows_rounded,
              color: context.colors.primary,
              size: 20,
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        label,
                        style: Theme.of(context).textTheme.titleMedium,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    if (isCurrent) ...[
                      const SizedBox(width: 6),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 3,
                        ),
                        decoration: BoxDecoration(
                          color: context.colors.successBg,
                          borderRadius: BorderRadius.circular(AppRadius.pill),
                        ),
                        child: Text(
                          context.l10n.thisDeviceLabel,
                          style: TextStyle(
                            color: context.colors.success,
                            fontSize: 11,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                    ],
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  lastSeen != null
                      ? context.l10n.deviceLastActive(
                          isApp ? context.l10n.mobileAppLabel : context.l10n.webBrowserLabel,
                          _formatWhen(context, lastSeen),
                        )
                      : (isApp ? context.l10n.mobileAppLabel : context.l10n.webBrowserLabel),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          isRevoking
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : IconButton(
                  tooltip: context.l10n.revokeButton,
                  icon: const Icon(Icons.logout_rounded, size: 20),
                  color: context.colors.error,
                  onPressed: () => _confirmAndRevoke(session),
                ),
        ],
      ),
    );
  }

  String _formatWhen(BuildContext context, DateTime dt) {
    final local = dt.toLocal();
    final now = DateTime.now();
    final diff = now.difference(local);
    if (diff.inMinutes < 1) return context.l10n.justNowLabel;
    if (diff.inMinutes < 60) return context.l10n.minutesAgoLabel(diff.inMinutes);
    if (diff.inHours < 24) return context.l10n.hoursAgoLabel(diff.inHours);
    if (diff.inDays < 7) return context.l10n.daysAgoLabel(diff.inDays);
    return '${local.day}/${local.month}/${local.year}';
  }
}
