import 'dart:async';
import 'dart:convert';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:socket_io_client/socket_io_client.dart' as sio;

import '../services/api_client.dart';
import '../services/pending_applications.dart';
import '../services/push_notification_service.dart';
import '../services/theme_controller.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';
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

enum _JobScreenMenuAction { profile, savedJobs, devices, logout }

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
      debugPrint('[socket] connected to $_base');
    });

    _socket!.onDisconnect((_) {
      debugPrint('[socket] disconnected');
    });

    _socket!.onConnectError((data) {
      debugPrint('[socket] connect_error: $data');
    });

    _socket!.onError((data) {
      debugPrint('[socket] error: $data');
    });

    // When employer posts a new job, we just refetch the match list
    _socket!.on('job_created', (data) async {
      debugPrint('[socket] job_created received → refreshing jobs');
      if (!mounted) return;
      await fetchJobs();
    });

    // When an application is created (from any client), refresh applied list
    _socket!.on('application_created', (data) async {
      debugPrint(
        '[socket] application_created received → refreshing applications',
      );
      if (!mounted) return;
      await fetchAppliedJobs();
    });

    // Status change (accept / reject): refresh the notification badge and
    // let the user know right away if the app happens to be open. This
    // handler used to be a no-op (BL-38 closes that gap).
    _socket!.on('application_status_changed', (data) async {
      debugPrint('[socket] application_status_changed received');
      if (!mounted) return;
      await _fetchUnreadNotifications();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('One of your applications has an update.'),
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
      String title = 'New notification';
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
        const SnackBar(
          content: Text("No connection and no previously loaded jobs."),
        ),
      );
    } catch (_) {
      if (!mounted) return;
      jobs = [];
      _showingCachedJobs = false;
      setState(() {});
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Network error loading jobs.")),
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
    final String jobTitle = (job["title"] as String?) ?? "this job";
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
                  const SnackBar(
                    content: Text(
                      "Network error — please try submitting again.",
                    ),
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
                  const SnackBar(
                    content: Text(
                      "You're offline — saved and will submit automatically once you're back online.",
                    ),
                    duration: Duration(seconds: 4),
                  ),
                );
              } catch (_) {
                if (!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text(
                      "Could not save this application offline either — please try again.",
                    ),
                  ),
                );
              }
            }

            Future<void> submit() async {
              if (cvRequired && cvFile == null) {
                if (!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text("CV is required.")),
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
                    const SnackBar(content: Text("Application submitted")),
                  );
                } else {
                  String msg = "Failed to submit application";
                  if (resp.statusCode == 413) {
                    msg = "File too large (max 16 MB)";
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
                    "Apply to $jobTitle",
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  if (employerName != null) ...[
                    const SizedBox(height: 2),
                    Text(
                      "at $employerName",
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
                              "No CV needed for gig/hire-based work — your trust here comes from completed gigs and ratings.",
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
                    title: cvRequired ? "CV" : "Proof of past work",
                    required: cvRequired,
                    fileName: cvFile?.name,
                    onPick: pickCV,
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  _DocPickerTile(
                    icon: Icons.attach_file_rounded,
                    title: "Supporting document",
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
                      label: const Text("Submit application"),
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
          chip("All jobs", ""),
          chip("Formal", "formal"),
          chip("Gig / hire-based", "gig"),
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
                  label: "Search jobs by title",
                  child: TextField(
                    controller: _searchController,
                    onChanged: _onSearchChanged,
                    textInputAction: TextInputAction.search,
                    decoration: const InputDecoration(
                      hintText: "Search jobs...",
                      prefixIcon: Icon(Icons.search_rounded, size: 20),
                      isDense: true,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                flex: 2,
                child: Semantics(
                  label: "Filter jobs by location",
                  child: TextField(
                    controller: _locationController,
                    onChanged: _onSearchChanged,
                    textInputAction: TextInputAction.search,
                    decoration: const InputDecoration(
                      hintText: "Location",
                      prefixIcon: Icon(Icons.location_on_outlined, size: 20),
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
                  label: const Text("Clear filters — showing unranked results"),
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
                  "Connecting youth to work",
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
                    ? "Notifications, $_unreadNotifications unread"
                    : "Notifications",
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
            tooltip: ThemeController.isDark ? "Switch to light mode" : "Switch to dark mode",
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
            tooltip: "More",
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
                case _JobScreenMenuAction.logout:
                  _logout();
                  break;
              }
            },
            itemBuilder: (context) => const [
              PopupMenuItem(
                value: _JobScreenMenuAction.profile,
                child: ListTile(
                  leading: Icon(Icons.person_outline_rounded),
                  title: Text("My Profile & CV"),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              PopupMenuItem(
                value: _JobScreenMenuAction.savedJobs,
                child: ListTile(
                  leading: Icon(Icons.bookmark_outline_rounded),
                  title: Text("Saved Jobs"),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              PopupMenuItem(
                value: _JobScreenMenuAction.devices,
                child: ListTile(
                  leading: Icon(Icons.devices_other_rounded),
                  title: Text("Devices"),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              PopupMenuItem(
                value: _JobScreenMenuAction.logout,
                child: ListTile(
                  leading: Icon(Icons.logout_rounded),
                  title: Text("Logout"),
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
                label: "You're offline. Showing previously loaded jobs.",
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
                        "You're offline. Showing previously loaded jobs.",
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
                                    ? "No jobs match your search"
                                    : "No jobs available right now",
                                subtitle:
                                    _searchQuery.isNotEmpty || _locationFilter.isNotEmpty
                                    ? "Try a different keyword or location."
                                    : "Check back soon — new opportunities are posted regularly.",
                              ),
                            ],
                          )
                        : ListView.builder(
                      padding: const EdgeInsets.fromLTRB(
                        AppSpacing.md,
                        AppSpacing.sm,
                        AppSpacing.md,
                        96,
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
                                                      "Untitled job",
                                                  style: Theme.of(
                                                    context,
                                                  ).textTheme.titleMedium,
                                                ),
                                                if (employer != null) ...[
                                                  const SizedBox(height: 3),
                                                  Row(
                                                    children: [
                                                      Expanded(
                                                        child: Text(
                                                          (employer["name"]
                                                                  as String?) ??
                                                              "Employer",
                                                          style: Theme.of(
                                                            context,
                                                          ).textTheme.bodySmall,
                                                          overflow: TextOverflow
                                                              .ellipsis,
                                                        ),
                                                      ),
                                                      const SizedBox(width: 4),
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
                                                    ],
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
                                                        Text(
                                                          "${employer["avg_rating"]} · $employerRatingCount rating${employerRatingCount == 1 ? '' : 's'} from past workers",
                                                          style: Theme.of(
                                                            context,
                                                          ).textTheme.bodySmall,
                                                        ),
                                                      ],
                                                    ),
                                                  ],
                                                ],
                                                if (isGig) ...[
                                                  const SizedBox(height: 4),
                                                  StatusBadge.jobType(context, jobType),
                                                ],
                                                const SizedBox(height: 4),
                                                Row(
                                                  children: [
                                                    Icon(
                                                      Icons
                                                          .location_on_outlined,
                                                      size: 15,
                                                      color:
                                                          context.colors.textMuted,
                                                    ),
                                                    const SizedBox(width: 3),
                                                    Expanded(
                                                      child: Text(
                                                        (job["location"]
                                                                as String?) ??
                                                            "",
                                                        style: Theme.of(
                                                          context,
                                                        ).textTheme.bodySmall,
                                                        overflow: TextOverflow
                                                            .ellipsis,
                                                      ),
                                                    ),
                                                    const SizedBox(width: 8),
                                                    Icon(
                                                      Icons.schedule_outlined,
                                                      size: 15,
                                                      color:
                                                          context.colors.textMuted,
                                                    ),
                                                    const SizedBox(width: 3),
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
                                                label: const Text("Applied"),
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
                                                      ? "Uploading..."
                                                      : (isGig
                                                            ? "Apply"
                                                            : "Apply with CV"),
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
        children: [
          FloatingActionButton.extended(
            heroTag: "myApplications",
            backgroundColor: context.colors.tertiary,
            icon: const Icon(Icons.history_rounded),
            label: const Text("My Applications"),
            onPressed: () {
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
            label: const Text("Passport"),
            onPressed: () {
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
            label: const Text("Work History"),
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (context) =>
                      WorkHistoryScreen(userId: widget.userId),
                ),
              );
            },
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
                  required ? "$title (required)" : "$title (optional)",
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                    color: context.colors.textPrimary,
                  ),
                ),
                Text(
                  fileName ?? "No file selected",
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          OutlinedButton(onPressed: onPick, child: const Text("Choose")),
        ],
      ),
    );
  }
}
