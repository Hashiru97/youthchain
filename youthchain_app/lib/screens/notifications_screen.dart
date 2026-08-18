import 'dart:convert';

import 'package:flutter/material.dart';

import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';
import 'messages_screen.dart';

/// BL-38: in-app notification list (no push/SMS provider is configured —
/// see the note on send_push_notification/send_sms in app.py — this is the
/// honest, working subset: a real notification feed while the app is
/// open/foregrounded or manually checked).
class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key});

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  List notifications = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    setState(() => _loading = true);
    try {
      final res = await ApiClient.instance.get('/api/notifications');
      if (!mounted) return;
      if (res.statusCode == 200) {
        final body = json.decode(res.body);
        setState(() {
          notifications = (body['notifications'] as List?) ?? [];
          _loading = false;
        });
      } else {
        setState(() => _loading = false);
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  Future<void> _markRead(int id) async {
    try {
      await ApiClient.instance.postJson('/api/notifications/$id/read', {});
    } catch (_) {
      // Non-critical — the list will just show it as unread again next
      // time; not worth surfacing an error for a read-receipt failure.
    }
  }

  static const _months = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];

  String _formatTimestamp(String? iso) {
    if (iso == null) return '';
    final dt = DateTime.tryParse(iso)?.toLocal();
    if (dt == null) return '';
    final hour12 = dt.hour % 12 == 0 ? 12 : dt.hour % 12;
    final minute = dt.minute.toString().padLeft(2, '0');
    final ampm = dt.hour >= 12 ? 'PM' : 'AM';
    return '${_months[dt.month - 1]} ${dt.day}, ${dt.year} at $hour12:$minute $ampm';
  }

  // BL-38 follow-up: previously only "new message" notifications went
  // anywhere when tapped -- every other type (application status changes,
  // gig-completion, ratings, security alerts) just sat in the list with no
  // way to see the full text on its own, which felt unfinished for a
  // notification inbox. This gives every non-navigating type a real detail
  // view instead.
  void _showNotificationDetail(Map n) {
    showModalBottomSheet(
      context: context,
      showDragHandle: true,
      builder: (context) {
        return SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.lg,
              0,
              AppSpacing.lg,
              AppSpacing.lg,
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Container(
                      width: 44,
                      height: 44,
                      decoration: BoxDecoration(
                        color: context.colors.primaryLight,
                        shape: BoxShape.circle,
                      ),
                      child: Icon(
                        _iconFor(n['type'] ?? ''),
                        size: 20,
                        color: context.colors.primary,
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    Expanded(
                      child: Text(
                        n['title'] ?? '',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: AppSpacing.md),
                if (n['body'] != null)
                  Text(n['body'], style: Theme.of(context).textTheme.bodyLarge),
                const SizedBox(height: AppSpacing.sm),
                Text(
                  _formatTimestamp(n['created_at'] as String?),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  IconData _iconFor(String type) {
    switch (type) {
      case 'application_status_changed':
        return Icons.assignment_turned_in_rounded;
      case 'new_message':
        return Icons.chat_bubble_rounded;
      // Gig/hire-based lifecycle (see Job.job_type in app.py) -- without
      // these two, a worker's "gig marked complete" / "you were rated"
      // notifications fell back to a plain bell, the one place left where
      // the gig feature didn't get the same treatment as everything else.
      case 'application_marked_complete':
        return Icons.task_alt_rounded;
      case 'worker_rated':
        return Icons.star_rounded;
      default:
        return Icons.notifications_rounded;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: const Text('Notifications'),
        actions: [
          IconButton(
            tooltip: 'Refresh',
            icon: const Icon(Icons.refresh_rounded),
            onPressed: _fetch,
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _fetch,
        child: _loading
            ? const SkeletonListView()
            : notifications.isEmpty
            ? ListView(
                children: const [
                  SizedBox(height: 120),
                  EmptyState(
                    icon: Icons.notifications_none_rounded,
                    title: 'No notifications yet',
                    subtitle:
                        "You'll see updates about your applications and messages here.",
                  ),
                ],
              )
            : ListView.separated(
                padding: const EdgeInsets.all(AppSpacing.md),
                itemCount: notifications.length,
                separatorBuilder: (_, __) =>
                    const SizedBox(height: AppSpacing.sm),
                itemBuilder: (context, index) {
                  final n = notifications[index];
                  final bool isRead = n['read'] == true;
                  return Semantics(
                    label:
                        "${n['title']}${isRead ? '' : ', unread'}. ${n['body'] ?? ''}",
                    // Material (colored) + InkWell, not InkWell wrapping an
                    // opaque Container -- real gap found via user feedback
                    // ("can't tell the tap registered"): Material paints ink
                    // splashes BEHIND its child, so an opaque Container in
                    // between hid the ripple completely even though the tap
                    // itself worked. Putting the row's own color on a
                    // Material ancestor (with InkWell inside it) is the
                    // standard fix -- the splash paints on top of that
                    // color, under the Row's content, instead of being
                    // hidden by it.
                    child: Material(
                      color: isRead
                          ? context.colors.surface
                          : context.colors.primaryLight,
                      borderRadius: BorderRadius.circular(AppRadius.md),
                      child: InkWell(
                        borderRadius: BorderRadius.circular(AppRadius.md),
                        onTap: () {
                          if (!isRead) {
                            setState(() => n['read'] = true);
                            _markRead((n['id'] as num).toInt());
                          }
                          // "New message" jumps straight to that conversation,
                          // same as before. Every other type opens a detail
                          // view instead of doing nothing further.
                          if (n['type'] == 'new_message') {
                            final meta = n['meta'];
                            final applicationId = meta is Map
                                ? meta['application_id']
                                : null;
                            if (applicationId is num) {
                              Navigator.push(
                                context,
                                MaterialPageRoute(
                                  builder: (_) => MessagesScreen(
                                    applicationId: applicationId.toInt(),
                                    jobTitle: null,
                                  ),
                                ),
                              );
                            }
                          } else {
                            _showNotificationDetail(n);
                          }
                        },
                        child: Container(
                          padding: const EdgeInsets.all(AppSpacing.sm),
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(AppRadius.md),
                            border: Border.all(
                              color: isRead
                                  ? context.colors.outline
                                  : context.colors.primary.withValues(alpha: 0.25),
                            ),
                          ),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Container(
                                width: 38,
                                height: 38,
                                decoration: BoxDecoration(
                                  color: isRead
                                      ? context.colors.background
                                      : Colors.white,
                                  shape: BoxShape.circle,
                                ),
                                child: Icon(
                                  _iconFor(n['type'] ?? ''),
                                  size: 18,
                                  color: isRead
                                      ? context.colors.textMuted
                                      : context.colors.primary,
                                ),
                              ),
                              const SizedBox(width: AppSpacing.sm),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      n['title'] ?? '',
                                      style: Theme.of(context)
                                          .textTheme
                                          .bodyLarge
                                          ?.copyWith(
                                            fontWeight: isRead
                                                ? FontWeight.w500
                                                : FontWeight.w700,
                                          ),
                                    ),
                                    if (n['body'] != null) ...[
                                      const SizedBox(height: 2),
                                      Text(
                                        n['body'],
                                        style: Theme.of(
                                          context,
                                        ).textTheme.bodyMedium,
                                      ),
                                    ],
                                  ],
                                ),
                              ),
                              if (!isRead)
                                Container(
                                  margin: const EdgeInsets.only(
                                    top: 4,
                                    left: 4,
                                  ),
                                  width: 8,
                                  height: 8,
                                  decoration: BoxDecoration(
                                    color: context.colors.primary,
                                    shape: BoxShape.circle,
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ),
                    ),
                  );
                },
              ),
      ),
    );
  }
}
