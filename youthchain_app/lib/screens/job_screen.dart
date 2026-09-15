import 'dart:async';
import 'dart:convert';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/foundation.dart' show kDebugMode;
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:socket_io_client/socket_io_client.dart' as sio;

import '../l10n/l10n_context.dart';
import '../services/api_client.dart';
import '../services/pending_applications.dart';
import '../services/push_notification_service.dart';
import '../services/theme_controller.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';
import '../widgets/language_picker.dart';
import '../widgets/name_with_badge.dart';
import '../widgets/save_job_button.dart';
import '../widgets/status_badge.dart';
import 'devices_screen.dart';
import 'job_detail_screen.dart';
import 'my_applications_screen.dart';
import 'notifications_screen.dart';
import 'passport_screen.dart';
import 'profile_cv_screen.dart';
import 'saved_jobs_screen.dart';
import 'work_history_screen.dart';

enum _JobScreenMenuAction { profile, savedJobs, devices, language, logout, deleteAccount }

class JobScreen extends StatefulWidget {
  final int userId;
  final VoidCallback? onLoggedOut; // optional hook to clear saved auth

  const JobScreen({super.key, required this.userId, this.onLoggedOut});

  @override
  JobScreenState createState() => JobScreenState();
}

class JobScreenState extends State<JobScreen> {
  static String get _base => ApiClient.baseUrl;
  static const _timeout = ApiClient.timeout;

  List jobs = [];
  Set<int> appliedJobIds = {};
  Set<int> savedJobIds = {};
  bool isLoading = true;
  bool isUploading = false;
  bool _showingCachedJobs = false;
  int _unreadNotifications = 0;

  // BL-36: search/filter state.
  final _searchController = TextEditingController();
  final _locationController = TextEditingController();
  String _searchQuery = "";
  String _locationFilter = "";
  Timer? _searchDebounce;

  // Formal vs gig/hire-based (see Job.job_type in app.py) -- "" means both.
  String _jobTypeFilter = "";

  // Real layout bug found live on a physical device, not a simulator: the
  // three FloatingActionButton.extended widgets below used to be
  // permanently expanded, stacked in a Column. Since a floating action
  // button sits at a fixed screen position regardless of scroll, that
  // meant they permanently covered part of whichever job cards happened
  // to render underneath them -- not just the last cards in the list (a
  // bottom-padding fix alone doesn't touch this), at every scroll
  // position. Collapsed behind one toggle FAB by default instead, the
  // standard Flutter "expandable FAB" pattern -- frees the screen by
  // default, same three destinations still one tap away.
  bool _fabExpanded = false;

  sio.Socket? _socket;

  @override
  void initState() {
    super.initState();
    _initialLoad();
    _connectSocket();
    // Singleton, app-lifetime listener — safe to call on every JobScreen
    // mount (e.g. after logout/login), idempotent past the first call.
    PendingApplicationsQueue.instance.startAutoSync();
    PendingApplicationsQueue.instance.trySyncAll();
  }

  @override
  void dispose() {
    _searchDebounce?.cancel();
    _searchController.dispose();
    _locationController.dispose();
    try {
      _socket?.off('connect');
      _socket?.off('disconnect');
      _socket?.off('connect_error');
      _socket?.off('error');
      _socket?.off('job_created');
      _socket?.off('application_created');
      _socket?.off('application_status_changed');
      _socket?.off('notification_created');
      _socket?.disconnect();
      _socket?.dispose();
    } catch (_) {}
    super.dispose();
  }

  void _onSearchChanged(String _) {
    _searchDebounce?.cancel();
    _searchDebounce = Timer(const Duration(milliseconds: 400), () {
      setState(() {
        _searchQuery = _searchController.text.trim();
        _locationFilter = _locationController.text.trim();
      });
      fetchJobs();
    });
  }

  void _clearFilters() {
    _searchController.clear();
    _locationController.clear();
    setState(() {
      _searchQuery = "";
      _locationFilter = "";
      _jobTypeFilter = "";
    });
    fetchJobs();
  }

  void _onJobTypeFilterChanged(String value) {
    setState(() => _jobTypeFilter = value);
    fetchJobs();
  }

  Future<void> _initialLoad() async {
    setState(() => isLoading = true);
    await Future.wait([
      fetchJobs(),
      fetchAppliedJobs(),
      _fetchUnreadNotifications(),
      fetchSavedJobIds(),
    ]);
    if (!mounted) return;
    setState(() => isLoading = false);
  }

  Future<void> fetchSavedJobIds() async {
    try {
      final res = await ApiClient.instance.get("/api/saved_jobs");
      if (!mounted || res.statusCode != 200) return;
      final List saved = json.decode(res.body) as List;
      setState(() {
        savedJobIds = saved.map<int>((j) => ((j as Map)["id"] as num).toInt()).toSet();
      });
    } catch (_) {
      // Best-effort, same reasoning as DiscoverScreen's own version --
      // every SaveJobButton works standalone even without this.
    }
  }

  Future<void> _fetchUnreadNotifications() async {
    try {
      final res = await ApiClient.instance.get('/api/notifications');
      if (!mounted) return;
      if (res.statusCode == 200) {
        final body = json.decode(res.body);
        setState(
          () => _unreadNotifications =
              (body['unread_count'] as num?)?.toInt() ?? 0,
        );
      }
    } catch (_) {
      // Non-critical — the badge just won't update this cycle.
    }
  }

  // ---------------- Socket.IO (Realtime) ----------------
  Future<void> _connectSocket() async {
    // The server only joins this connection to this user's private
    // notification/message room (see _socketio_connect in app.py) if a
    // valid JWT is presented in the connect handshake's `auth` payload --
    // a WebSocket connection has no Authorization header the way a REST
    // call does, so the token has to be sent this way instead. Without
    // this, the app would still connect and still receive genuinely
    // public events (job_created), just never any of the private ones
    // (notification_created, message_created, application_status_changed)
    // that were previously (insecurely) broadcast to every connection
    // regardless of identity.
    final token = await ApiClient.instance.getToken();

    // IMPORTANT: Flask-SocketIO default path is "/socket.io" (no trailing slash)
    final socketOptions = sio.OptionBuilder()
        .setPath('/socket.io')
        .setTransports(['websocket', 'polling'])
        .enableReconnection()
        .setReconnectionAttempts(1 << 20)
        .setReconnectionDelay(800)
        .setAuth(token != null ? {'token': token} : {})
        .build();
    // OptionBuilder has no setter for these, but Manager reads them
    // straight out of the options map -- caps the retry interval so a
    // permanently-down backend doesn't retry at a flat 800ms forever
    // (real exponential backoff up to 8s, with Manager's default jitter).
    socketOptions['reconnectionDelayMax'] = 8000;
    _socket = sio.io(_base, socketOptions);

    _socket!.onConnect((_) {
      if (kDebugMode) debugPrint('[socket] connected to $_base');
    });

    _socket!.onDisconnect((_) {
      if (kDebugMode) debugPrint('[socket] disconnected');
    });

    _socket!.onConnectError((data) {
      if (kDebugMode) debugPrint('[socket] connect_error: $data');
    });

    _socket!.onError((data) {
      if (kDebugMode) debugPrint('[socket] error: $data');
    });

    // When employer posts a new job, we just refetch the match list
    _socket!.on('job_created', (data) async {
      if (kDebugMode) {
        debugPrint('[socket] job_created received → refreshing jobs');
      }
      if (!mounted) return;
      await fetchJobs();
    });

    // When an application is created (from any client), refresh applied list
    _socket!.on('application_created', (data) async {
      if (kDebugMode) {
        debugPrint(
          '[socket] application_created received → refreshing applications',
        );
      }
      if (!mounted) return;
      await fetchAppliedJobs();
    });

    // Status change (accept / reject): refresh the notification badge and
    // let the user know right away if the app happens to be open. This
    // handler used to be a no-op (BL-38 closes that gap).
    _socket!.on('application_status_changed', (data) async {
      if (kDebugMode) {
        debugPrint('[socket] application_status_changed received');
      }
      if (!mounted) return;
      await _fetchUnreadNotifications();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(context.l10n.applicationUpdateNotice),
        ),
      );
    });

    _socket!.on('notification_created', (data) async {
      if (!mounted) return;
      await _fetchUnreadNotifications();
      if (!mounted) return;
      // The unread badge alone wasn't "easily noticed" per live user
      // feedback — surface a clearly-visible, eye-catching (red/error
      // accent) toast the instant a notification arrives while the app is
      // open, in addition to the existing badge-count refresh above.
      String title = context.l10n.newNotificationDefaultTitle;
      String? body;
      if (data is Map) {
        final t = data['title'];
        if (t is String && t.isNotEmpty) title = t;
        final b = data['body'];
        if (b is String && b.isNotEmpty) body = b;
      }
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: context.colors.error,
          behavior: SnackBarBehavior.floating,
          duration: const Duration(seconds: 4),
          content: Row(
            children: [
              const Icon(
                Icons.notifications_active_rounded,
                color: Colors.white,
                size: 20,
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      title,
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    if (body != null)
                      Text(
                        body,
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 12.5,
                        ),
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
    });
  }

  // ---------------- API calls ----------------

  Future<void> fetchJobs() async {
    try {
      String path;
      final hasFilters =
          _searchQuery.isNotEmpty ||
          _locationFilter.isNotEmpty ||
          _jobTypeFilter.isNotEmpty;
      if (hasFilters) {
        // BL-36: an active search/filter uses the plain, filterable /jobs
        // endpoint — skill-score matching and free-text search don't mix,
        // so an explicit search intentionally shows unranked results. A
        // job-type filter (formal vs gig, see Job.job_type) joins the same
        // bucket for the same reason -- /api/match_jobs doesn't know about
        // job_type either.
        final params = <String, String>{
          if (_searchQuery.isNotEmpty) "q": _searchQuery,
          if (_locationFilter.isNotEmpty) "location": _locationFilter,
          if (_jobTypeFilter.isNotEmpty) "job_type": _jobTypeFilter,
        };
        path = Uri(path: "/jobs", queryParameters: params).toString();
      } else {
        // Candidate.id is a separate primary key from User.id — resolve it
        // rather than assuming they coincide (they only did by accident for
        // the very first user, which is what the original code assumed).
        final candidateId = await ApiClient.instance.resolveCandidateId();
        path = candidateId != null
            ? "/api/match_jobs/$candidateId"
            : "/jobs"; // no profile yet: fall back to the plain, unranked list
      }
      // BL-37: falls back to the last successfully loaded copy for this
      // exact path (candidate id and any active search/filter included) if
      // the network call fails — a real Hardhat/backend timeout no longer
      // means an empty screen if there's something cached to show instead.
      final result = await ApiClient.instance.getWithCache(path);

      if (!mounted) return;

      final decoded = json.decode(result.body);
      // /api/match_jobs returns {"jobs": [...]}; /jobs returns a bare
      // array — these differ, and treating them the same (as this code
      // used to) throws at runtime whenever the bare-array path is hit
      // (e.g. a brand new user with no candidate profile yet, or any
      // search/filter request).
      jobs = decoded is Map
          ? (decoded["jobs"] as List? ?? [])
          : (decoded as List);
      _showingCachedJobs = result.fromCache;
      setState(() {});
    } on NoCachedDataException {
      if (!mounted) return;
      jobs = [];
      _showingCachedJobs = false;
      setState(() {});
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(context.l10n.noConnectionNoPreviousJobs),
        ),
      );
    } catch (_) {
      if (!mounted) return;
      jobs = [];
      _showingCachedJobs = false;
      setState(() {});
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.l10n.networkErrorLoadingJobs)),
      );
    }
  }

  Future<void> fetchAppliedJobs() async {
    try {
      final res = await ApiClient.instance
          .get("/my_applications/${widget.userId}")
          .timeout(_timeout);
      if (!mounted) return;

      if (res.statusCode == 200) {
        final List list = json.decode(res.body);
        appliedJobIds = list
            .map<int>((e) => (e["job_id"] as num).toInt())
            .toSet();
        setState(() {});
      } else {
        appliedJobIds = {};
        setState(() {});
      }
    } catch (_) {
      if (!mounted) return;
      appliedJobIds = {};
      setState(() {});
    }
  }

  // ---------------- Apply modal ----------------

  /// Real gap found via a full mobile-UX review: a youth tapping "Apply
  /// with CV" directly on a job card (the fast path — no need to open
  /// the full detail screen first) landed in a sheet titled just "Apply
  /// to job", with no job title, employer name, or anything else
  /// confirming what they were about to submit to. On a list of several
  /// similar-looking cards, a misplaced tap could submit a real
  /// application to the wrong job with zero chance to notice before
  /// hitting Submit. The sheet now always names the job and, when the
  /// posting has a linked employer account, who's actually hiring —
  /// matching what JobDetailScreen already showed before this fix, so
  /// both entry points into applying now carry the same context.
  Future<void> _showApplySheet(Map job) async {
    final int jobId = (job["id"] as num).toInt();
    final String jobTitle = (job["title"] as String?) ?? context.l10n.thisJobFallback;
    final employer = (job["employer"] as Map?)?.cast<String, dynamic>();
    final String? employerName = employer?["name"] as String?;
    // Gig/hire-based jobs (see Job.job_type in app.py) don't require a CV
    // to apply -- trust there comes from completed gigs and ratings
    // instead, not a document (see the work-history screen).
    final bool cvRequired = (job["job_type"] as String?) != "gig";

    XFile? cvFile;
    XFile? supportFile;

    const docsGroup = XTypeGroup(
      label: 'Documents',
      extensions: ['pdf', 'doc', 'docx', 'png', 'jpg', 'jpeg'],
    );

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: context.colors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadius.lg)),
      ),
      builder: (ctx) {
        return StatefulBuilder(
          builder: (ctx, setSheetState) {
            Future<void> pickCV() async {
              final res = await openFile(acceptedTypeGroups: const [docsGroup]);
              if (res != null) setSheetState(() => cvFile = res);
            }

            Future<void> pickSupport() async {
              final res = await openFile(acceptedTypeGroups: const [docsGroup]);
              if (res != null) setSheetState(() => supportFile = res);
            }

            Future<void> queueOffline() async {
              // BL-37 write-queue follow-up: a network/timeout failure while
              // submitting is exactly the case a purely-online app would
              // just fail on. Persist the picked files into this app's own
              // storage and queue the application for automatic retry the
              // moment connectivity returns (PendingApplicationsQueue
              // listens for that), rather than losing the user's completed
              // work over a dropped connection.
              //
              // PendingApplicationsQueue was built around the CV-required
              // flow and always persists a CV file -- a gig application
              // with no CV attached at all has nothing to persist that way,
              // so it just surfaces a plain "try again" message instead of
              // silently extending that queue's schema for a case it was
              // never designed to hold.
              if (cvFile == null) {
                if (!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(
                    content: Text(context.l10n.networkErrorTrySubmittingAgain),
                  ),
                );
                return;
              }
              try {
                final cvBytes = await cvFile!.readAsBytes();
                List<int>? supportBytes;
                if (supportFile != null) {
                  supportBytes = await supportFile!.readAsBytes();
                }
                await PendingApplicationsQueue.instance.enqueue(
                  jobId: jobId,
                  jobTitle: jobTitle,
                  cvFileName: cvFile!.name,
                  cvBytes: cvBytes,
                  supportingFileName: supportFile?.name,
                  supportingBytes: supportBytes,
                );
                if (!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(
                    content: Text(context.l10n.offlineApplicationQueued),
                    duration: const Duration(seconds: 4),
                  ),
                );
              } catch (_) {
                if (!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(
                    content: Text(context.l10n.couldNotSaveApplicationOffline),
                  ),
                );
              }
            }

            Future<void> submit() async {
              if (cvRequired && cvFile == null) {
                if (!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text(context.l10n.cvIsRequired)),
                );
                return;
              }

              Navigator.of(ctx).pop();
              setState(() => isUploading = true);

              try {
                // user_id is derived server-side from the auth token, not
                // sent from the client (see backend /apply).
                final req = await ApiClient.instance.multipartRequest("/apply");
                req.fields["job_id"] = jobId.toString();

                if (cvFile != null) {
                  if (cvFile!.path.isNotEmpty) {
                    req.files.add(
                      await http.MultipartFile.fromPath("cv", cvFile!.path),
                    );
                  } else {
                    final bytes = await cvFile!.readAsBytes();
                    req.files.add(
                      http.MultipartFile.fromBytes(
                        "cv",
                        bytes,
                        filename: cvFile!.name,
                      ),
                    );
                  }
                }

                if (supportFile != null) {
                  if (supportFile!.path.isNotEmpty) {
                    req.files.add(
                      await http.MultipartFile.fromPath(
                        "supporting",
                        supportFile!.path,
                      ),
                    );
                  } else {
                    final bytes = await supportFile!.readAsBytes();
                    req.files.add(
                      http.MultipartFile.fromBytes(
                        "supporting",
                        bytes,
                        filename: supportFile!.name,
                      ),
                    );
                  }
                }

                final resp = await req.send().timeout(_timeout);
                final body = await resp.stream.bytesToString();

                if (!mounted) return;

                if (resp.statusCode == 201) {
                  await fetchAppliedJobs();
                  if (!mounted) return;
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(content: Text(context.l10n.applicationSubmitted)),
                  );
                } else {
                  String msg = context.l10n.failedToSubmitApplication;
                  if (resp.statusCode == 413) {
                    msg = context.l10n.fileTooLarge;
                  } else {
                    try {
                      final m = json.decode(body);
                      if (m is Map && m["error"] is String) {
                        msg = m["error"] as String;
                      }
                    } catch (_) {}
                  }
                  ScaffoldMessenger.of(
                    context,
                  ).showSnackBar(SnackBar(content: Text(msg)));
                }
              } on TimeoutException {
                await queueOffline();
              } catch (_) {
                await queueOffline();
              } finally {
                if (mounted) setState(() => isUploading = false);
              }
            }

            return Padding(
              padding: EdgeInsets.only(
                left: AppSpacing.md,
                right: AppSpacing.md,
                bottom:
                    MediaQuery.of(context).viewInsets.bottom + AppSpacing.md,
                top: AppSpacing.md,
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Center(
                    child: Container(
                      height: 4,
                      width: 40,
                      margin: const EdgeInsets.only(bottom: AppSpacing.md),
                      decoration: BoxDecoration(
                        color: context.colors.outline,
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                  ),
                  Text(
                    context.l10n.applyToJobTitle(jobTitle),
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  if (employerName != null) ...[
                    const SizedBox(height: 2),
                    Text(
                      context.l10n.atEmployerName(employerName),
                      style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: context.colors.textSecondary,
                      ),
                    ),
                  ],
                  if (!cvRequired) ...[
                    Container(
                      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
                      padding: const EdgeInsets.all(AppSpacing.sm),
                      decoration: BoxDecoration(
                        color: context.colors.secondaryLight,
                        borderRadius: BorderRadius.circular(AppRadius.sm),
                      ),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                            Icons.handshake_rounded,
                            size: 18,
                            color: context.colors.secondary,
                          ),
                          const SizedBox(width: AppSpacing.sm),
                          Expanded(
                            child: Text(
                              context.l10n.noCvNeededGigNotice,
                              style: Theme.of(context).textTheme.bodySmall
                                  ?.copyWith(color: context.colors.secondaryDark),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                  const SizedBox(height: AppSpacing.md),
                  _DocPickerTile(
                    icon: Icons.description_outlined,
                    title: cvRequired ? context.l10n.cvDocLabel : context.l10n.proofOfPastWorkLabel,
                    required: cvRequired,
                    fileName: cvFile?.name,
                    onPick: pickCV,
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  _DocPickerTile(
                    icon: Icons.attach_file_rounded,
                    title: context.l10n.supportingDocumentLabel,
                    required: false,
                    fileName: supportFile?.name,
                    onPick: pickSupport,
                  ),
                  const SizedBox(height: AppSpacing.lg),
                  SizedBox(
                    width: double.infinity,
                    height: 50,
                    child: ElevatedButton.icon(
                      icon: const Icon(Icons.send_rounded, size: 18),
                      label: Text(context.l10n.submitApplicationButton),
                      onPressed: submit,
                    ),
                  ),
                ],
              ),
            );
          },
        );
      },
    );
  }

  // Quick-switch entry point for BL-49 feedback that language was too
  // hard to find buried inside Profile & CV -- reuses the exact same
  // LanguagePicker (and LocaleController underneath) as that screen, so
  // there's exactly one place the English/Krio/device-default choice is
  // ever implemented, just two places it can be reached from.
  void _showLanguageDialog(BuildContext context) {
    showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(context.l10n.languageMenuItem),
        content: const LanguagePicker(showLabel: false),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: Text(context.l10n.closeButton),
          ),
        ],
      ),
    );
  }

  Future<void> _logout() async {
    // Real gap found via a full OWASP Top 10 (A07) review of the backend:
    // logging out here used to only ever clear the token locally --
    // the token itself stayed fully valid server-side for whatever
    // remained of its 12h lifetime, unlike the employer/admin portals'
    // session cookies, which already revoke immediately on logout. The
    // new POST /logout blocklists this exact token server-side. Must run
    // BEFORE clearSession() (which is what the Authorization header for
    // this very request is built from), and its own failure (e.g.
    // logging out while offline) must never block the local logout --
    // the user should always be able to log out of this device, with or
    // without connectivity; server-side revocation is a real, additional
    // protection on top of that, not a precondition for it.
    try {
      await ApiClient.instance.postJson("/logout", {});
    } catch (_) {
      // Offline or a network error -- local logout still proceeds below.
    }
    await PushNotificationService.clearToken();
    await ApiClient.instance.clearSession();
    widget.onLoggedOut?.call();
    if (!mounted) return;
    Navigator.of(context).pushNamedAndRemoveUntil('/login', (_) => false);
  }

  /// Self-service counterpart to POST /api/account/erase (see
  /// _erase_user_data()'s own docstring in app.py for what this actually
  /// does) -- real gap found via a full-codebase audit: the privacy
  /// policy has always promised this is reachable "from within the app",
  /// and the backend route has existed, been tested, and been
  /// rate-limited against password guessing for a while, but nothing in
  /// this app -- the actual primary product surface -- ever exposed it.
  /// Requires re-entering the password (same reasoning as the backend
  /// route's own docstring: a still-valid session token alone shouldn't
  /// be enough to trigger something this irreversible), and surfaces the
  /// backend's own specific hold reason (an open appeal/report/dispute)
  /// verbatim rather than a generic failure, since that message is
  /// actionable ("resolve that first") in a way a generic error isn't.
  Future<void> _confirmAndEraseAccount() async {
    final passwordController = TextEditingController();
    String? error;
    bool submitting = false;
    bool obscure = true;
    // Deliberately swaps this SAME dialog's own content in place on
    // success rather than popping it and opening a second showDialog()
    // right after -- chaining two modal route transitions back-to-back
    // like that turned out to be genuinely flaky under flutter_test's
    // frame-synchronous pumping (a real, reproduced pumpAndSettle
    // timeout/build-scope assertion, not just a style preference), and a
    // single dialog that transitions is simpler for a real user too (no
    // close-then-reopen flicker).
    bool succeeded = false;
    final l10n = context.l10n;

    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => StatefulBuilder(
        builder: (dialogContext, setDialogState) {
          Future<void> submit() async {
            final password = passwordController.text;
            if (password.isEmpty) {
              setDialogState(() => error = l10n.incorrectPasswordError);
              return;
            }
            setDialogState(() {
              submitting = true;
              error = null;
            });
            try {
              final res = await ApiClient.instance.postJson(
                '/api/account/erase',
                {'password': password},
              );
              if (res.statusCode == 200) {
                setDialogState(() {
                  submitting = false;
                  succeeded = true;
                });
                return;
              }
              String message;
              if (res.statusCode == 429) {
                message = l10n.tooManyAttemptsTryLater;
              } else if (res.statusCode == 403) {
                message = l10n.incorrectPasswordError;
              } else {
                // 409 (an open appeal/report/dispute) carries the
                // backend's own specific, actionable reason in `error` --
                // show it verbatim rather than a generic failure.
                Map<String, dynamic>? data;
                try {
                  final decoded = json.decode(res.body);
                  if (decoded is Map<String, dynamic>) data = decoded;
                } catch (_) {}
                message = (data?['error'] as String?) ?? l10n.deleteAccountFailedGeneric;
              }
              setDialogState(() {
                submitting = false;
                error = message;
              });
            } catch (_) {
              setDialogState(() {
                submitting = false;
                error = l10n.networkErrorGeneric;
              });
            }
          }

          if (succeeded) {
            return AlertDialog(
              title: Text(l10n.accountDeletedTitle),
              content: Text(l10n.accountDeletedMessage),
              actions: [
                TextButton(
                  onPressed: () => Navigator.of(dialogContext).pop(),
                  child: Text(l10n.okButton),
                ),
              ],
            );
          }

          return AlertDialog(
            title: Text(l10n.deleteAccountDialogTitle),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l10n.deleteAccountWarning),
                const SizedBox(height: AppSpacing.sm),
                TextField(
                  controller: passwordController,
                  obscureText: obscure,
                  autofocus: true,
                  enabled: !submitting,
                  decoration: InputDecoration(
                    labelText: l10n.passwordLabel,
                    suffixIcon: IconButton(
                      tooltip: obscure ? l10n.showPassword : l10n.hidePassword,
                      icon: Icon(obscure ? Icons.visibility_outlined : Icons.visibility_off_outlined, size: 20),
                      onPressed: () => setDialogState(() => obscure = !obscure),
                    ),
                  ),
                  onSubmitted: (_) => submitting ? null : submit(),
                ),
                if (error != null) ...[
                  const SizedBox(height: AppSpacing.sm),
                  Text(error!, style: TextStyle(color: context.colors.error, fontSize: 13)),
                ],
              ],
            ),
            actions: [
              TextButton(
                onPressed: submitting ? null : () => Navigator.of(dialogContext).pop(),
                child: Text(l10n.cancelButton),
              ),
              FilledButton(
                style: FilledButton.styleFrom(backgroundColor: context.colors.error),
                onPressed: submitting ? null : submit,
                child: submitting
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2, valueColor: AlwaysStoppedAnimation(Colors.white)),
                      )
                    : Text(l10n.deleteAccountConfirmButton),
              ),
            ],
          );
        },
      ),
    );
    passwordController.dispose();

    if (!succeeded) return;
    if (!mounted) return;

    // The account row is already gone server-side (tombstoned) by this
    // point -- same local cleanup as _logout() above, no POST /logout
    // call first since there is no longer any account for that route to
    // act on.
    await PushNotificationService.clearToken();
    await ApiClient.instance.clearSession();
    widget.onLoggedOut?.call();
    if (!mounted) return;
    Navigator.of(context).pushNamedAndRemoveUntil('/login', (_) => false);
  }

  // ---------------- Helper UI ----------------

  List<String> _parseSkills(String? skills) => (skills ?? "")
      .split(',')
      .map((e) => e.trim())
      .where((e) => e.isNotEmpty)
      .toList();

  Widget _buildJobTypeFilterChips() {
    Widget chip(String label, String value) {
      final selected = _jobTypeFilter == value;
      return ChoiceChip(
        label: Text(label),
        selected: selected,
        onSelected: (_) => _onJobTypeFilterChanged(selected ? "" : value),
      );
    }

    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.md,
        AppSpacing.xs,
        AppSpacing.md,
        0,
      ),
      child: Wrap(
        spacing: 8,
        children: [
          chip(context.l10n.allJobsChip, ""),
          chip(context.l10n.jobTypeFormal, "formal"),
          chip(context.l10n.jobTypeGig, "gig"),
        ],
      ),
    );
  }

  Widget _buildSearchBar() {
    final hasFilters =
        _searchQuery.isNotEmpty ||
        _locationFilter.isNotEmpty ||
        _jobTypeFilter.isNotEmpty;
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.md,
        AppSpacing.sm,
        AppSpacing.md,
        AppSpacing.xs,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                flex: 3,
                child: Semantics(
                  label: context.l10n.searchJobsByTitleSemantics,
                  child: TextField(
                    controller: _searchController,
                    onChanged: _onSearchChanged,
                    textInputAction: TextInputAction.search,
                    decoration: InputDecoration(
                      hintText: context.l10n.searchJobsHint,
                      prefixIcon: const Icon(Icons.search_rounded, size: 20),
                      isDense: true,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                flex: 2,
                child: Semantics(
                  label: context.l10n.filterJobsByLocationSemantics,
                  child: TextField(
                    controller: _locationController,
                    onChanged: _onSearchChanged,
                    textInputAction: TextInputAction.search,
                    decoration: InputDecoration(
                      hintText: context.l10n.locationHint,
                      prefixIcon: const Icon(Icons.location_on_outlined, size: 20),
                      isDense: true,
                    ),
                  ),
                ),
              ),
            ],
          ),
          if (hasFilters)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: _clearFilters,
                  icon: const Icon(Icons.clear_rounded, size: 16),
                  label: Text(context.l10n.clearFiltersButton),
                ),
              ),
            ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        // Name + slogan stacked, leading-aligned (BL-50) -- previously a
        // single centered "YouthChain Jobs" line, which read as
        // off-balance against the leading-aligned actions on the other
        // side. "Connecting youth to work" is the one slogan this platform
        // uses everywhere its name appears (see login_screen.dart and
        // portal_shell.html) -- kept identical here, not a job-screen-
        // specific variant.
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Real gap found via user feedback: the web portal/employer/
            // admin headers all carry the actual brand icon next to
            // "YouthChain" (see portal_shell.html's .portal-brand-mark),
            // but this app bar only ever had the wordmark -- an
            // inconsistency, not a deliberate mobile-specific choice.
            // Backed by an off-white tile (matching login_screen.dart's
            // hero badge) since the icon's navy half would disappear
            // directly against this green app bar.
            Container(
              width: 30,
              height: 30,
              padding: const EdgeInsets.all(5),
              decoration: BoxDecoration(
                color: const Color(0xFFF5F3EE),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Image.asset("assets/img/youthchain_icon.png"),
            ),
            const SizedBox(width: 8),
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Text(
                  "YouthChain",
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, letterSpacing: 0.1),
                ),
                Text(
                  context.l10n.appTagline,
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w500,
                    color: Colors.white.withValues(alpha: 0.85),
                  ),
                ),
              ],
            ),
          ],
        ),
        actions: [
          Stack(
            alignment: Alignment.center,
            children: [
              IconButton(
                tooltip: _unreadNotifications > 0
                    ? context.l10n.notificationsUnreadTooltip(_unreadNotifications)
                    : context.l10n.notificationsTooltip,
                icon: const Icon(Icons.notifications_outlined),
                onPressed: () {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => const NotificationsScreen(),
                    ),
                  ).then((_) async {
                    await _fetchUnreadNotifications();
                  });
                },
              ),
              if (_unreadNotifications > 0)
                Positioned(
                  right: 8,
                  top: 8,
                  child: IgnorePointer(
                    child: Container(
                      padding: const EdgeInsets.all(3),
                      decoration: BoxDecoration(
                        color: context.colors.error,
                        shape: BoxShape.circle,
                        border: Border.all(
                          color: context.colors.primary,
                          width: 1.5,
                        ),
                      ),
                      constraints: const BoxConstraints(
                        minWidth: 16,
                        minHeight: 16,
                      ),
                      child: Text(
                        _unreadNotifications > 9
                            ? "9+"
                            : "$_unreadNotifications",
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 9,
                          fontWeight: FontWeight.w700,
                        ),
                        textAlign: TextAlign.center,
                      ),
                    ),
                  ),
                ),
            ],
          ),
          // Dark mode (BL-49) -- kept as its own always-visible icon, same
          // reasoning as Notifications staying out of the overflow menu:
          // this is state a user might want to check/flip at a glance,
          // not a one-off navigation action like Profile/Devices/Logout.
          // Web portal user feedback specifically flagged a toggle buried
          // in a collapsible nav as "hard to notice" -- same lesson
          // applied here before it became a live complaint on mobile too.
          IconButton(
            tooltip: ThemeController.isDark ? context.l10n.switchToLightMode : context.l10n.switchToDarkMode,
            icon: Icon(
              ThemeController.isDark ? Icons.light_mode_outlined : Icons.dark_mode_outlined,
            ),
            onPressed: () => ThemeController.toggle(),
          ),
          // Real gap found via user feedback: the app bar had grown to 5
          // separate icons (notifications, profile, refresh, devices,
          // logout), which read as cluttered rather than tidy. Notifications
          // stays on its own -- it carries the unread badge, and burying
          // that inside a menu means a youth could miss an application
          // update unless they think to open it. Refresh is dropped
          // entirely: pull-to-refresh already exists on the job list below
          // (RefreshIndicator), this button duplicated it. Everything else
          // (account-management actions, not primary navigation) collapses
          // into one overflow menu.
          PopupMenuButton<_JobScreenMenuAction>(
            tooltip: context.l10n.moreTooltip,
            icon: const Icon(Icons.more_vert_rounded),
            onSelected: (action) {
              switch (action) {
                case _JobScreenMenuAction.profile:
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => ProfileCvScreen(userId: widget.userId),
                    ),
                  ).then((_) async {
                    // if skills changed, refresh matches
                    await _initialLoad();
                  });
                  break;
                case _JobScreenMenuAction.savedJobs:
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => SavedJobsScreen(userId: widget.userId),
                    ),
                  ).then((_) async {
                    await fetchSavedJobIds();
                  });
                  break;
                case _JobScreenMenuAction.devices:
                  Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => const DevicesScreen()),
                  );
                  break;
                case _JobScreenMenuAction.language:
                  _showLanguageDialog(context);
                  break;
                case _JobScreenMenuAction.logout:
                  _logout();
                  break;
                case _JobScreenMenuAction.deleteAccount:
                  _confirmAndEraseAccount();
                  break;
              }
            },
            itemBuilder: (context) => [
              PopupMenuItem(
                value: _JobScreenMenuAction.profile,
                child: ListTile(
                  leading: const Icon(Icons.person_outline_rounded),
                  title: Text(context.l10n.myProfileCvMenuItem),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              PopupMenuItem(
                value: _JobScreenMenuAction.savedJobs,
                child: ListTile(
                  leading: const Icon(Icons.bookmark_outline_rounded),
                  title: Text(context.l10n.savedJobsMenuItem),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              PopupMenuItem(
                value: _JobScreenMenuAction.devices,
                child: ListTile(
                  leading: const Icon(Icons.devices_other_rounded),
                  title: Text(context.l10n.devicesMenuItem),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              PopupMenuItem(
                value: _JobScreenMenuAction.language,
                child: ListTile(
                  leading: const Icon(Icons.language_rounded),
                  title: Text(context.l10n.languageMenuItem),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              PopupMenuItem(
                value: _JobScreenMenuAction.logout,
                child: ListTile(
                  leading: const Icon(Icons.logout_rounded),
                  title: Text(context.l10n.logoutMenuItem),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              // Real gap found via a full-codebase audit: the privacy
              // policy (see backend/templates/privacy_policy.html) has
              // always promised "you can request deletion of your account
              // and data from within the app" -- POST /api/account/erase
              // has existed and been fully tested since BL-XX, but nothing
              // in this app, the actual primary product surface, ever
              // exposed it. Styled distinctly (error color) and placed
              // last -- the one destructive, irreversible action in this
              // menu, as opposed to Logout's fully reversible one.
              PopupMenuItem(
                value: _JobScreenMenuAction.deleteAccount,
                child: ListTile(
                  leading: Icon(Icons.delete_forever_rounded, color: context.colors.error),
                  title: Text(
                    context.l10n.deleteAccountMenuItem,
                    style: TextStyle(color: context.colors.error),
                  ),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
            ],
          ),
        ],
      ),
      body: Column(
        children: [
          _buildSearchBar(),
          _buildJobTypeFilterChips(),
          if (_showingCachedJobs)
            Container(
              width: double.infinity,
              color: context.colors.warningBg,
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.md,
                vertical: AppSpacing.sm,
              ),
              child: Semantics(
                liveRegion: true,
                label: context.l10n.offlineShowingPreviousJobs,
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
                        context.l10n.offlineShowingPreviousJobs,
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
          Expanded(
            child: isLoading
                ? const SkeletonListView()
                // Real gap found via later re-audit: RefreshIndicator
                // previously only wrapped the non-empty branch below, so
                // pull-to-refresh silently did nothing on an empty
                // result (no jobs, or a search/filter with zero
                // matches). That's worse here than on other screens --
                // the AppBar's own manual refresh button was
                // deliberately removed as "redundant" (see the comment
                // above PopupMenuButton) on the assumption pull-to-
                // refresh already covered this, and JobScreen lives
                // inside HomeShell's IndexedStack, which keeps this
                // screen's state alive across tab switches -- so leaving
                // and coming back doesn't re-fetch either. Once a user
                // hit an empty result there was no way to recover short
                // of restarting the app.
                : RefreshIndicator(
                    onRefresh: () =>
                        Future.wait([fetchJobs(), fetchAppliedJobs(), fetchSavedJobIds()]),
                    child: jobs.isEmpty
                        ? ListView(
                            children: [
                              const SizedBox(height: 120),
                              EmptyState(
                                icon: Icons.work_off_outlined,
                                title: _searchQuery.isNotEmpty || _locationFilter.isNotEmpty
                                    ? context.l10n.noJobsMatchSearch
                                    : context.l10n.noJobsAvailable,
                                subtitle:
                                    _searchQuery.isNotEmpty || _locationFilter.isNotEmpty
                                    ? context.l10n.tryDifferentKeywordLocation
                                    : context.l10n.checkBackSoonJobs,
                              ),
                            ],
                          )
                        : ListView.builder(
                      // 96 only cleared roughly one FAB's height -- this
                      // screen docks THREE stacked FloatingActionButton.
                      // extended widgets (myApplications/passport/
                      // workHistory below, ~56 each + 8 gaps between),
                      // so the last couple of cards sat behind them,
                      // genuinely uncoverable/untappable (confirmed live
                      // on a real device: the "Apply" button on the last
                      // visible cards was hidden under the FAB stack).
                      padding: const EdgeInsets.fromLTRB(
                        AppSpacing.md,
                        AppSpacing.sm,
                        AppSpacing.md,
                        216,
                      ),
                      itemCount: jobs.length,
                      itemBuilder: (context, index) {
                        final job = jobs[index];
                        final int jobId = (job["id"] as num).toInt();
                        final bool alreadyApplied = appliedJobIds.contains(
                          jobId,
                        );

                        final String? requiredSkills =
                            (job["required_skills"] as String?)?.trim();
                        final int score = (job["score"] ?? 0) as int;
                        final bool hasScore = job.containsKey("score");
                        final skillList = _parseSkills(requiredSkills);
                        final String jobType =
                            (job["job_type"] as String?) ?? "formal";
                        final bool isGig = jobType == "gig";
                        // Same job["employer"] shape _employer_summary()
                        // puts on every job-listing response (see
                        // job_detail_screen.dart's identical read) --
                        // already present on this dict, just never
                        // rendered on the list card until now. Real
                        // inconsistency found via checking mobile against
                        // portal_dashboard.html: the web youth dashboard
                        // shows the verified badge and earned rating right
                        // on the job list card, but mobile only revealed
                        // it after tapping into the detail screen -- for
                        // an app whose mission includes fighting employer
                        // mistrust, that's the one moment (scanning a
                        // list, deciding what to open) where it matters
                        // most.
                        final employer = (job["employer"] as Map?)
                            ?.cast<String, dynamic>();
                        final int? employerRatingCount =
                            (employer?["rating_count"] as num?)?.toInt();
                        final String? employerTrustTier =
                            employer?["trust_tier"] as String?;

                        return Container(
                          margin: const EdgeInsets.only(bottom: AppSpacing.md),
                          decoration: BoxDecoration(
                            color: context.colors.surface,
                            borderRadius: BorderRadius.circular(AppRadius.md),
                            border: Border.all(color: context.colors.outline),
                            boxShadow: cardShadow,
                          ),
                          child: ClipRRect(
                            borderRadius: BorderRadius.circular(AppRadius.md),
                            child: Material(
                              color: Colors.transparent,
                              child: InkWell(
                                onTap: () async {
                                  await Navigator.push(
                                    context,
                                    MaterialPageRoute(
                                      builder: (_) => JobDetailScreen(
                                        job: job,
                                        alreadyApplied: alreadyApplied,
                                        onApply: () => _showApplySheet(job),
                                        initiallySaved: savedJobIds.contains(jobId),
                                      ),
                                    ),
                                  );
                                  // Catches a toggle made on the detail
                                  // screen's own bookmark button.
                                  if (mounted) fetchSavedJobIds();
                                },
                                child: Padding(
                                  padding: const EdgeInsets.all(AppSpacing.md),
                                  child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Row(
                                        crossAxisAlignment:
                                            CrossAxisAlignment.start,
                                        children: [
                                          Container(
                                            width: 44,
                                            height: 44,
                                            decoration: BoxDecoration(
                                              color: context.colors.primaryLight,
                                              borderRadius:
                                                  BorderRadius.circular(
                                                    AppRadius.sm,
                                                  ),
                                            ),
                                            child: Icon(
                                              Icons.work_outline_rounded,
                                              color: context.colors.primary,
                                              size: 22,
                                            ),
                                          ),
                                          const SizedBox(width: AppSpacing.sm),
                                          Expanded(
                                            child: Column(
                                              crossAxisAlignment:
                                                  CrossAxisAlignment.start,
                                              children: [
                                                Text(
                                                  (job["title"] as String?) ??
                                                      context.l10n.untitledJob,
                                                  style: Theme.of(
                                                    context,
                                                  ).textTheme.titleMedium,
                                                ),
                                                if (employer != null) ...[
                                                  const SizedBox(height: 3),
                                                  // Real bug found via live
                                                  // testing: this row shares
                                                  // the card with the icon
                                                  // box, the match-score
                                                  // badge, and the save
                                                  // button, so almost
                                                  // nothing was left for the
                                                  // Expanded name once the
                                                  // "Verified Business"/
                                                  // "Verified Individual"
                                                  // badge claimed its own
                                                  // width first -- observed
                                                  // live collapsing a real
                                                  // employer name down to
                                                  // "Te...". See
                                                  // NameWithBadge's own
                                                  // docstring for the fix.
                                                  NameWithBadge(
                                                    name:
                                                        (employer["name"]
                                                                as String?) ??
                                                            context.l10n.employerFallbackName,
                                                    style: Theme.of(
                                                      context,
                                                    ).textTheme.bodySmall,
                                                    badge:
                                                        StatusBadge.employerVerification(
                                                      context,
                                                      (employer["verification_status"]
                                                              as String?) ??
                                                          "unverified",
                                                      type:
                                                          employer["verification_type"]
                                                              as String?,
                                                      dense: true,
                                                    ),
                                                  ),
                                                  if (employerRatingCount !=
                                                          null &&
                                                      employerRatingCount >
                                                          0) ...[
                                                    const SizedBox(height: 2),
                                                    Row(
                                                      children: [
                                                        Icon(
                                                          Icons.star_rounded,
                                                          size: 13,
                                                          color: context.colors.secondary,
                                                        ),
                                                        const SizedBox(
                                                          width: 3,
                                                        ),
                                                        Flexible(
                                                          child: Text(
                                                            context.l10n.employerRatingSummary(
                                                              "${employer["avg_rating"]}",
                                                              employerRatingCount,
                                                            ),
                                                            style: Theme.of(
                                                              context,
                                                            ).textTheme.bodySmall,
                                                            overflow: TextOverflow.ellipsis,
                                                          ),
                                                        ),
                                                      ],
                                                    ),
                                                  ],
                                                  // Composite trust score
                                                  // (see _employer_trust_summary
                                                  // in app.py) -- only shown
                                                  // here, unlike the
                                                  // always-shown badge on the
                                                  // job detail screen, when
                                                  // it isn't "good" (the
                                                  // common case for a
                                                  // healthy employer) -- a
                                                  // list of many job cards
                                                  // all showing "Good
                                                  // standing" would be
                                                  // noise, but "Fair" or
                                                  // especially "Use caution"
                                                  // is exactly the signal
                                                  // worth surfacing while a
                                                  // youth is still scanning
                                                  // which jobs to open.
                                                  if (employerTrustTier !=
                                                          null &&
                                                      employerTrustTier !=
                                                          'good') ...[
                                                    const SizedBox(height: 2),
                                                    StatusBadge.employerTrust(
                                                      context,
                                                      employerTrustTier,
                                                    ),
                                                  ],
                                                ],
                                                if (isGig) ...[
                                                  const SizedBox(height: 4),
                                                  StatusBadge.jobType(context, jobType),
                                                ],
                                                const SizedBox(height: 4),
                                                // Same bug class as the
                                                // employer-name row above,
                                                // same live evidence
                                                // (observed "Freeto..." for
                                                // "Freetown"): a plain Row
                                                // gave duration's fixed-
                                                // width Text whatever room
                                                // it needed first, and
                                                // location's Expanded slot
                                                // got only what was left in
                                                // this already-narrow row.
                                                // A bounded LayoutBuilder
                                                // (outside the Wrap, not
                                                // inside -- a Wrap child
                                                // gets unbounded
                                                // constraints, so ellipsis
                                                // would never trigger) gives
                                                // location a real width to
                                                // truncate within only if it
                                                // actually needs to; Wrap
                                                // then lets duration flow to
                                                // its own line instead of
                                                // always sharing one line
                                                // and crushing location.
                                                LayoutBuilder(
                                                  builder: (context, constraints) => Wrap(
                                                    crossAxisAlignment:
                                                        WrapCrossAlignment
                                                            .center,
                                                    spacing: 8,
                                                    runSpacing: 2,
                                                    children: [
                                                      ConstrainedBox(
                                                        constraints: BoxConstraints(
                                                          maxWidth: constraints.maxWidth,
                                                        ),
                                                        child: Row(
                                                          mainAxisSize:
                                                              MainAxisSize.min,
                                                          children: [
                                                            Icon(
                                                              Icons
                                                                  .location_on_outlined,
                                                              size: 15,
                                                              color: context
                                                                  .colors
                                                                  .textMuted,
                                                            ),
                                                            const SizedBox(
                                                              width: 3,
                                                            ),
                                                            Flexible(
                                                              child: Text(
                                                                (job["location"]
                                                                        as String?) ??
                                                                    "",
                                                                style: Theme.of(
                                                                  context,
                                                                ).textTheme.bodySmall,
                                                                overflow:
                                                                    TextOverflow
                                                                        .ellipsis,
                                                              ),
                                                            ),
                                                          ],
                                                        ),
                                                      ),
                                                      Row(
                                                        mainAxisSize:
                                                            MainAxisSize.min,
                                                        children: [
                                                          Icon(
                                                            Icons
                                                                .schedule_outlined,
                                                            size: 15,
                                                            color: context
                                                                .colors
                                                                .textMuted,
                                                          ),
                                                          const SizedBox(
                                                            width: 3,
                                                          ),
                                                          Text(
                                                            (job["duration"]
                                                                    as String?) ??
                                                                "",
                                                            style: Theme.of(
                                                              context,
                                                            ).textTheme.bodySmall,
                                                          ),
                                                        ],
                                                      ),
                                                    ],
                                                  ),
                                                ),
                                              ],
                                            ),
                                          ),
                                          if (hasScore) ...[
                                            const SizedBox(width: 6),
                                            StatusBadge.matchScore(context, score),
                                          ],
                                          SizedBox(
                                            width: 36,
                                            height: 36,
                                            child: SaveJobButton(
                                              jobId: jobId,
                                              initiallySaved: savedJobIds.contains(jobId),
                                              size: 20,
                                              onChanged: (saved) => setState(() {
                                                if (saved) {
                                                  savedJobIds.add(jobId);
                                                } else {
                                                  savedJobIds.remove(jobId);
                                                }
                                              }),
                                            ),
                                          ),
                                        ],
                                      ),
                                      if (requiredSkills != null &&
                                          requiredSkills.isNotEmpty) ...[
                                        const SizedBox(height: AppSpacing.sm),
                                        Wrap(
                                          spacing: 6,
                                          runSpacing: 6,
                                          children: skillList
                                              .map((s) => Chip(label: Text(s)))
                                              .toList(),
                                        ),
                                      ],
                                      const SizedBox(height: AppSpacing.sm),
                                      SizedBox(
                                        width: double.infinity,
                                        height: 44,
                                        child: alreadyApplied
                                            ? OutlinedButton.icon(
                                                onPressed: null,
                                                icon: const Icon(
                                                  Icons.check_rounded,
                                                  size: 18,
                                                ),
                                                label: Text(context.l10n.appliedButtonLabel),
                                              )
                                            : ElevatedButton.icon(
                                                onPressed: isUploading
                                                    ? null
                                                    : () =>
                                                          _showApplySheet(job),
                                                icon: Icon(
                                                  isUploading
                                                      ? Icons
                                                            .hourglass_top_rounded
                                                      : Icons.send_rounded,
                                                  size: 18,
                                                ),
                                                label: Text(
                                                  isUploading
                                                      ? context.l10n.uploadingButtonLabel
                                                      : (isGig
                                                            ? context.l10n.applyButtonLabel
                                                            : context.l10n.applyWithCvButtonLabel),
                                                ),
                                              ),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            ),
                          ),
                        );
                      },
                    ),
                  ),
          ),
        ],
      ),
      floatingActionButtonLocation: FloatingActionButtonLocation.endDocked,
      floatingActionButton: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          if (_fabExpanded) ...[
            FloatingActionButton.extended(
              heroTag: "myApplications",
              backgroundColor: context.colors.tertiary,
              icon: const Icon(Icons.history_rounded),
              label: Text(context.l10n.myApplicationsFab),
              onPressed: () {
                setState(() => _fabExpanded = false);
                Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (context) =>
                        MyApplicationsScreen(userId: widget.userId),
                  ),
                ).then((_) async {
                  await fetchAppliedJobs();
                });
              },
            ),
            const SizedBox(height: AppSpacing.sm),
            FloatingActionButton.extended(
              heroTag: "passport",
              backgroundColor: context.colors.passport,
              icon: const Icon(Icons.card_membership_rounded),
              label: Text(context.l10n.passportFab),
              onPressed: () {
                setState(() => _fabExpanded = false);
                Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (context) => PassportScreen(userId: widget.userId),
                  ),
                );
              },
            ),
            const SizedBox(height: AppSpacing.sm),
            // Gig-work trust record (see Job.job_type) -- deliberately its
            // own entry point rather than folded into Passport, since
            // diploma credentials are on-chain-verified and gig ratings are
            // peer-review-based; merging them would blur a distinction users
            // need to trust both independently.
            FloatingActionButton.extended(
              heroTag: "workHistory",
              backgroundColor: context.colors.secondary,
              icon: const Icon(Icons.star_rounded),
              label: Text(context.l10n.workHistoryFab),
              onPressed: () {
                setState(() => _fabExpanded = false);
                Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (context) =>
                        WorkHistoryScreen(userId: widget.userId),
                  ),
                );
              },
            ),
            const SizedBox(height: AppSpacing.sm),
          ],
          FloatingActionButton(
            heroTag: "fabToggle",
            tooltip: _fabExpanded
                ? context.l10n.closeQuickActionsTooltip
                : context.l10n.openQuickActionsTooltip,
            onPressed: () => setState(() => _fabExpanded = !_fabExpanded),
            child: Icon(_fabExpanded ? Icons.close_rounded : Icons.menu_rounded),
          ),
        ],
      ),
    );
  }
}

class _DocPickerTile extends StatelessWidget {
  final IconData icon;
  final String title;
  final bool required;
  final String? fileName;
  final VoidCallback onPick;

  const _DocPickerTile({
    required this.icon,
    required this.title,
    required this.required,
    required this.fileName,
    required this.onPick,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.sm),
      decoration: BoxDecoration(
        color: context.colors.background,
        borderRadius: BorderRadius.circular(AppRadius.sm),
        border: Border.all(color: context.colors.outline),
      ),
      child: Row(
        children: [
          Icon(icon, color: context.colors.textSecondary, size: 20),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  required ? context.l10n.docRequiredSuffix(title) : context.l10n.docOptionalSuffix(title),
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                    color: context.colors.textPrimary,
                  ),
                ),
                Text(
                  fileName ?? context.l10n.noFileSelected,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          OutlinedButton(onPressed: onPick, child: Text(context.l10n.chooseButton)),
        ],
      ),
    );
  }
}
