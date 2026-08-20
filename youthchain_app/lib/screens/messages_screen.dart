import 'dart:async';
import 'dart:convert';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:socket_io_client/socket_io_client.dart' as sio;
import 'package:url_launcher/url_launcher.dart';

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';
import '../widgets/report_sheet.dart';
import '../widgets/status_badge.dart';

/// BL-39: employer<->applicant messaging, scoped to one application.
class MessagesScreen extends StatefulWidget {
  final int applicationId;
  // Nullable: this screen can also be opened from a notification tap (BL-38
  // follow-up), where only the applicationId is known at that point — the
  // job title is fetched/known separately, or simply omitted in the AppBar.
  final String? jobTitle;

  /// Test-only seam: when provided, this stream stands in for the real
  /// Socket.IO connection (see _connectSocket) -- feeding it an event map
  /// simulates the server pushing a `message_created` event, the same way
  /// ApiClient.testClient stands in for real HTTP calls in every other
  /// screen's tests. Left null in production, where _connectSocket() opens
  /// a real connection instead.
  @visibleForTesting
  final Stream<dynamic>? testMessageEvents;

  const MessagesScreen({
    super.key,
    required this.applicationId,
    this.jobTitle,
    this.testMessageEvents,
  });

  @override
  State<MessagesScreen> createState() => _MessagesScreenState();
}

class _MessagesScreenState extends State<MessagesScreen> {
  final _bodyController = TextEditingController();
  final _scrollController = ScrollController();
  List messages = [];
  Map<String, dynamic>? _employer;
  // Raw FK, needed to call POST /api/report_employer -- _employer above is
  // deliberately the trimmed public summary (name/verification_status/
  // industry) with no id, so this is a separate field, not something
  // parsed out of _employer. Originally this screen's report action was
  // scoped out entirely because the messages API didn't expose this; it
  // does now (see the backend's api_application_messages).
  int? _employerId;
  bool _loading = true;
  bool _sending = false;
  XFile? _pendingAttachment;

  sio.Socket? _socket;
  StreamSubscription<dynamic>? _testEventsSub;

  @override
  void initState() {
    super.initState();
    _fetchMessages();
    if (widget.testMessageEvents != null) {
      _testEventsSub = widget.testMessageEvents!.listen(_handleIncomingMessageEvent);
    } else {
      _connectSocket();
    }
  }

  @override
  void dispose() {
    _bodyController.dispose();
    _scrollController.dispose();
    _testEventsSub?.cancel();
    try {
      _socket?.off('message_created');
      _socket?.disconnect();
      _socket?.dispose();
    } catch (_) {}
    super.dispose();
  }

  // ---------------- Socket.IO (Realtime) ----------------
  //
  // Real gap found via a full-codebase review: JobScreen already
  // subscribes to Socket.IO for job/application/notification push events
  // (see JobScreenState._connectSocket), but this screen had no
  // subscription at all -- a user with a thread open would never see a
  // reply arrive without manually tapping Refresh. Mirrors JobScreen's
  // connection setup exactly (same path/transports/reconnection options,
  // same JWT-in-auth-payload handshake), just listening for one different
  // event.
  Future<void> _connectSocket() async {
    final token = await ApiClient.instance.getToken();
    final socketOptions = sio.OptionBuilder()
        .setPath('/socket.io')
        .setTransports(['websocket', 'polling'])
        .enableReconnection()
        .setReconnectionAttempts(1 << 20)
        .setReconnectionDelay(800)
        .setAuth(token != null ? {'token': token} : {})
        .build();
    socketOptions['reconnectionDelayMax'] = 8000;
    _socket = sio.io(ApiClient.baseUrl, socketOptions);

    // message_created is emitted to both the applicant's and the
    // employer's private rooms (see _create_message in app.py) whenever
    // either side posts to any of their threads -- only react to it when
    // it belongs to the specific thread open on this screen.
    _socket!.on('message_created', _handleIncomingMessageEvent);
  }

  void _handleIncomingMessageEvent(dynamic data) {
    if (!mounted) return;
    if (data is Map && (data['application_id'] as num?)?.toInt() == widget.applicationId) {
      _fetchMessages();
    }
  }

  Future<void> _fetchMessages() async {
    setState(() => _loading = true);
    try {
      final res = await ApiClient.instance.get(
        "/api/application/${widget.applicationId}/messages",
      );
      if (!mounted) return;
      if (res.statusCode == 200) {
        final body = json.decode(res.body);
        setState(() {
          messages = (body["messages"] as List?) ?? [];
          _employer = (body["employer"] as Map?)?.cast<String, dynamic>();
          _employerId = (body["employer_id"] as num?)?.toInt();
          _loading = false;
        });
        WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToBottom());
      } else {
        setState(() => _loading = false);
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorLoadingMessages)),
      );
    }
  }

  void _scrollToBottom() {
    if (!_scrollController.hasClients) return;
    _scrollController.animateTo(
      _scrollController.position.maxScrollExtent,
      duration: const Duration(milliseconds: 250),
      curve: Curves.easeOut,
    );
  }

  Future<void> _pickAttachment() async {
    const docsGroup = XTypeGroup(
      label: 'Documents',
      extensions: ['pdf', 'doc', 'docx', 'png', 'jpg', 'jpeg'],
    );
    final res = await openFile(acceptedTypeGroups: const [docsGroup]);
    if (res != null) setState(() => _pendingAttachment = res);
  }

  Future<void> _openAttachment(String filename) async {
    try {
      final token = await ApiClient.instance.getToken();
      final uri = ApiClient.instance
          .uri("/message_attachment/${Uri.encodeComponent(filename)}")
          .replace(queryParameters: {"token": ?token});
      final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
      if (!ok && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.l10n.couldNotOpenAttachment)),
        );
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.couldNotOpenAttachment)),
      );
    }
  }

  Future<void> _send() async {
    final body = _bodyController.text.trim();
    final attachment = _pendingAttachment;
    if (body.isEmpty && attachment == null) return;

    setState(() => _sending = true);
    try {
      final http.Response res;
      if (attachment != null) {
        final req = await ApiClient.instance.multipartRequest(
          "/api/application/${widget.applicationId}/messages",
        );
        req.fields["body"] = body;
        if (attachment.path.isNotEmpty) {
          req.files.add(
            await http.MultipartFile.fromPath("attachment", attachment.path),
          );
        } else {
          final bytes = await attachment.readAsBytes();
          req.files.add(
            http.MultipartFile.fromBytes(
              "attachment",
              bytes,
              filename: attachment.name,
            ),
          );
        }
        final streamed = await ApiClient.instance.sendMultipart(req);
        res = await http.Response.fromStream(streamed);
      } else {
        res = await ApiClient.instance.postJson(
          "/api/application/${widget.applicationId}/messages",
          {"body": body},
        );
      }

      if (!mounted) return;
      if (res.statusCode == 201) {
        _bodyController.clear();
        setState(() => _pendingAttachment = null);
        await _fetchMessages();
      } else if (res.statusCode == 413) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.l10n.fileTooLarge)),
        );
      } else {
        final data = jsonDecodeSafe(res.body);
        final msg = (data?["error"] as String?) ?? context.l10n.failedToSendMessage;
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(msg)));
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorSendingMessage)),
      );
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  StatusBadge _employerBadge(BuildContext context, String status, {String? type}) {
    final l10n = context.l10n;
    switch (status) {
      case 'verified':
        return StatusBadge(
          label: type == 'individual'
              ? l10n.employerVerifiedIndividual
              : l10n.employerVerifiedBusiness,
          color: Colors.white,
          background: Colors.transparent,
          icon: Icons.verified_rounded,
          dense: true,
        );
      case 'pending':
        return StatusBadge(
          label: l10n.employerPendingReview,
          color: Colors.white,
          background: Colors.transparent,
          icon: Icons.schedule_rounded,
          dense: true,
        );
      case 'rejected':
        return StatusBadge(
          label: l10n.employerRejected,
          color: Colors.white,
          background: Colors.transparent,
          icon: Icons.cancel_rounded,
          dense: true,
        );
      default:
        return StatusBadge(
          label: l10n.employerUnverified,
          color: Colors.white,
          background: Colors.transparent,
          icon: Icons.help_outline_rounded,
          dense: true,
        );
    }
  }

  /// Employer name + verification status shown under the AppBar title —
  /// answers "who am I even talking to" (previously nothing surfaced this
  /// at all). Handles a null employer (e.g. seeded jobs with no owning
  /// employer account) gracefully instead of showing a blank/broken row.
  PreferredSizeWidget? _employerBar() {
    if (_loading) return null;
    final employer = _employer;
    final String line;
    Widget? badge;
    if (employer == null) {
      line = context.l10n.postedByYouthChain;
    } else {
      final name = (employer["name"] as String?) ?? context.l10n.employerFallbackName;
      final status =
          (employer["verification_status"] as String?) ?? "unverified";
      final type = employer["verification_type"] as String?;
      line = name;
      badge = _employerBadge(context, status, type: type);
    }
    return PreferredSize(
      preferredSize: const Size.fromHeight(28),
      child: Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Flexible(
              child: Text(
                line,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 12.5,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            if (badge != null) ...[
              const SizedBox(width: 6),
              const Text(
                " · ",
                style: TextStyle(color: Colors.white70, fontSize: 12),
              ),
              badge,
            ],
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: Text(
          widget.jobTitle != null
              ? context.l10n.messagesTitleWithJob(widget.jobTitle!)
              : context.l10n.messagesTitle,
          overflow: TextOverflow.ellipsis,
        ),
        backgroundColor: context.colors.tertiary,
        bottom: _employerBar(),
        actions: [
          if (_employerId != null)
            IconButton(
              tooltip: context.l10n.reportThisEmployerTooltip,
              icon: const Icon(Icons.flag_outlined),
              onPressed: () => showReportSheet(
                context,
                employerId: _employerId!,
                messageId: messages.isNotEmpty
                    ? (messages.last["id"] as num?)?.toInt()
                    : null,
              ),
            ),
          IconButton(
            tooltip: context.l10n.refreshTooltip,
            icon: const Icon(Icons.refresh_rounded),
            onPressed: _fetchMessages,
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : messages.isEmpty
                ? EmptyState(
                    icon: Icons.forum_outlined,
                    title: context.l10n.noMessagesYetTitle,
                    subtitle: context.l10n.noMessagesYetSubtitle,
                  )
                : ListView.builder(
                    controller: _scrollController,
                    padding: const EdgeInsets.all(AppSpacing.md),
                    itemCount: messages.length,
                    itemBuilder: (context, index) {
                      final m = messages[index];
                      final isEmployer = m["sender_type"] == "employer";
                      final attachmentFile = m["attachment_file"] as String?;
                      final isRead = m["read"] == true;
                      final body = (m["body"] as String?) ?? "";
                      return Align(
                        alignment: isEmployer
                            ? Alignment.centerLeft
                            : Alignment.centerRight,
                        child: Container(
                          constraints: BoxConstraints(
                            maxWidth: MediaQuery.of(context).size.width * 0.75,
                          ),
                          margin: const EdgeInsets.symmetric(vertical: 4),
                          padding: const EdgeInsets.symmetric(
                            horizontal: 14,
                            vertical: 10,
                          ),
                          decoration: BoxDecoration(
                            color: isEmployer
                                ? context.colors.surface
                                : context.colors.tertiary,
                            borderRadius: BorderRadius.only(
                              topLeft: const Radius.circular(AppRadius.sm),
                              topRight: const Radius.circular(AppRadius.sm),
                              bottomLeft: Radius.circular(
                                isEmployer ? 2 : AppRadius.sm,
                              ),
                              bottomRight: Radius.circular(
                                isEmployer ? AppRadius.sm : 2,
                              ),
                            ),
                            border: isEmployer
                                ? Border.all(color: context.colors.outline)
                                : null,
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              if (body.isNotEmpty)
                                Semantics(
                                  label: context.l10n.messageSaidSemantics(
                                    isEmployer
                                        ? context.l10n.employerFallbackName
                                        : context.l10n.senderYouLabel,
                                    body,
                                  ),
                                  child: Text(
                                    body,
                                    style: TextStyle(
                                      color: isEmployer
                                          ? context.colors.textPrimary
                                          : Colors.white,
                                      fontSize: 14,
                                    ),
                                  ),
                                ),
                              if (attachmentFile != null) ...[
                                if (body.isNotEmpty) const SizedBox(height: 6),
                                InkWell(
                                  onTap: () => _openAttachment(attachmentFile),
                                  child: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Icon(
                                        Icons.attach_file_rounded,
                                        size: 16,
                                        color: isEmployer
                                            ? context.colors.tertiary
                                            : Colors.white,
                                      ),
                                      const SizedBox(width: 4),
                                      Text(
                                        context.l10n.attachmentLinkLabel,
                                        style: TextStyle(
                                          fontSize: 13,
                                          decoration: TextDecoration.underline,
                                          color: isEmployer
                                              ? context.colors.tertiary
                                              : Colors.white,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                              if (!isEmployer) ...[
                                const SizedBox(height: 4),
                                Text(
                                  isRead ? context.l10n.messageStatusRead : context.l10n.messageStatusSent,
                                  style: TextStyle(
                                    fontSize: 10,
                                    color: Colors.white.withValues(alpha: 0.75),
                                  ),
                                ),
                              ],
                            ],
                          ),
                        ),
                      );
                    },
                  ),
          ),
          SafeArea(
            child: Container(
              padding: const EdgeInsets.all(AppSpacing.sm),
              decoration: BoxDecoration(
                color: context.colors.surface,
                border: Border(top: BorderSide(color: context.colors.outline)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (_pendingAttachment != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Row(
                        children: [
                          Icon(
                            Icons.attach_file_rounded,
                            size: 16,
                            color: context.colors.textSecondary,
                          ),
                          const SizedBox(width: 4),
                          Expanded(
                            child: Text(
                              _pendingAttachment!.name,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ),
                          IconButton(
                            iconSize: 16,
                            visualDensity: VisualDensity.compact,
                            icon: const Icon(Icons.close_rounded),
                            onPressed: () =>
                                setState(() => _pendingAttachment = null),
                          ),
                        ],
                      ),
                    ),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      IconButton(
                        tooltip: context.l10n.attachAFileTooltip,
                        icon: Icon(
                          Icons.attach_file_rounded,
                          color: context.colors.textSecondary,
                        ),
                        onPressed: _pickAttachment,
                      ),
                      Expanded(
                        child: Semantics(
                          label: context.l10n.typeAMessageSemantics,
                          child: TextField(
                            controller: _bodyController,
                            maxLength: 2000,
                            minLines: 1,
                            maxLines: 4,
                            decoration: InputDecoration(
                              hintText: context.l10n.typeAMessageHint,
                              counterText: "",
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(width: AppSpacing.sm),
                      _sending
                          ? const Padding(
                              padding: EdgeInsets.all(12),
                              child: SizedBox(
                                width: 22,
                                height: 22,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2.2,
                                ),
                              ),
                            )
                          : Container(
                              decoration: BoxDecoration(
                                color: context.colors.tertiary,
                                shape: BoxShape.circle,
                              ),
                              child: IconButton(
                                tooltip: context.l10n.sendMessageTooltip,
                                icon: const Icon(
                                  Icons.send_rounded,
                                  color: Colors.white,
                                  size: 20,
                                ),
                                onPressed: _send,
                              ),
                            ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

Map<String, dynamic>? jsonDecodeSafe(String body) {
  try {
    final v = json.decode(body);
    return v is Map<String, dynamic> ? v : null;
  } catch (_) {
    return null;
  }
}
