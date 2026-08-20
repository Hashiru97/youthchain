import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_client.dart';

/// A job application queued while offline, waiting to be submitted once a
/// connection is available.
///
/// Closes the scope explicitly deferred in BL-37 (offline read-caching):
/// that work covered read-through caching only and stated plainly that a
/// correct offline write-queue for binary attachments (CV/supporting
/// documents) was a bigger feature deserving its own pass. This is that
/// pass. File bytes are copied into this app's own documents directory
/// (not just held as an XFile path) because a file picker's returned path
/// is not guaranteed to remain valid after the picker sheet closes or the
/// app restarts — the whole point of a durable queue is surviving both.
class PendingApplication {
  final String id;
  // Whichever account's ApiClient.getUserId() was current at enqueue()
  // time — see PendingApplicationsQueue's own docstring on why every
  // read/sync path filters by this against the CURRENTLY logged-in user
  // rather than operating on the raw on-disk list directly. Nullable only
  // for defensive backward-compat with an item persisted before this
  // field existed; such an item can never match a real logged-in user's
  // id again and is effectively orphaned (safe default: excluded from
  // every account's view rather than guessed into one).
  final int? userId;
  final int jobId;
  final String jobTitle;
  final String cvFileName;
  final String cvLocalPath;
  final String? supportingFileName;
  final String? supportingLocalPath;
  final DateTime queuedAt;
  final String status; // 'pending' | 'failed'
  final String? lastError;

  const PendingApplication({
    required this.id,
    required this.userId,
    required this.jobId,
    required this.jobTitle,
    required this.cvFileName,
    required this.cvLocalPath,
    this.supportingFileName,
    this.supportingLocalPath,
    required this.queuedAt,
    this.status = 'pending',
    this.lastError,
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'userId': userId,
        'jobId': jobId,
        'jobTitle': jobTitle,
        'cvFileName': cvFileName,
        'cvLocalPath': cvLocalPath,
        'supportingFileName': supportingFileName,
        'supportingLocalPath': supportingLocalPath,
        'queuedAt': queuedAt.toIso8601String(),
        'status': status,
        'lastError': lastError,
      };

  factory PendingApplication.fromJson(Map<String, dynamic> json) => PendingApplication(
        id: json['id'] as String,
        userId: (json['userId'] as num?)?.toInt(),
        jobId: (json['jobId'] as num).toInt(),
        jobTitle: json['jobTitle'] as String? ?? '',
        cvFileName: json['cvFileName'] as String,
        cvLocalPath: json['cvLocalPath'] as String,
        supportingFileName: json['supportingFileName'] as String?,
        supportingLocalPath: json['supportingLocalPath'] as String?,
        queuedAt: DateTime.tryParse(json['queuedAt'] as String? ?? '') ?? DateTime.now(),
        status: json['status'] as String? ?? 'pending',
        lastError: json['lastError'] as String?,
      );

  PendingApplication copyWith({String? status, String? lastError}) => PendingApplication(
        id: id,
        userId: userId,
        jobId: jobId,
        jobTitle: jobTitle,
        cvFileName: cvFileName,
        cvLocalPath: cvLocalPath,
        supportingFileName: supportingFileName,
        supportingLocalPath: supportingLocalPath,
        queuedAt: queuedAt,
        status: status ?? this.status,
        lastError: lastError,
      );
}

class PendingApplicationsQueue {
  PendingApplicationsQueue._();
  static final PendingApplicationsQueue instance = PendingApplicationsQueue._();

  static const _storageKey = 'yc_pending_applications';

  final _controller = StreamController<List<PendingApplication>>.broadcast();
  Stream<List<PendingApplication>> get changes => _controller.stream;

  bool _syncing = false;
  StreamSubscription<List<ConnectivityResult>>? _connectivitySub;

  /// Starts listening for connectivity changes and attempts a sync the
  /// moment a connection reappears — the "sync on reconnect" half of
  /// BL-37's original scope, not just "queue and hope someone pulls to
  /// refresh." Call once (e.g. from JobScreen's initState); safe to call
  /// more than once, later calls are no-ops.
  void startAutoSync() {
    if (_connectivitySub != null) return;
    _connectivitySub = Connectivity().onConnectivityChanged.listen((results) {
      if (results.any((r) => r != ConnectivityResult.none)) {
        trySyncAll();
      }
    });
  }

  void dispose() {
    _connectivitySub?.cancel();
    _connectivitySub = null;
  }

  Future<Directory> _queueDir() async {
    final docs = await getApplicationDocumentsDirectory();
    final dir = Directory('${docs.path}/pending_applications');
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  /// Raw, unfiltered contents of the on-disk queue -- every account that
  /// has ever queued an application on this device, not just the one
  /// currently logged in. Kept unfiltered specifically because
  /// _saveAll()'s read-modify-write callers (enqueue/_removeAndCleanup/
  /// _updateStatus) round-trip through this list and must never silently
  /// drop another account's still-queued entries when they persist their
  /// own change -- see loadForCurrentUser() for the account-scoped view
  /// every UI/sync call site outside this class should actually use.
  Future<List<PendingApplication>> loadAll() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_storageKey);
    if (raw == null) return [];
    try {
      final list = json.decode(raw) as List;
      return list
          .map((e) => PendingApplication.fromJson(e as Map<String, dynamic>))
          .toList();
    } catch (_) {
      return [];
    }
  }

  /// The queue as it should ever be shown to, or synced on behalf of, a
  /// human: only entries queued by whichever account is CURRENTLY logged
  /// in. Real gap found via a live audit: this queue used to be a single
  /// on-disk list with no per-account scoping at all, and this app has no
  /// per-user storage namespace elsewhere to lean on for it (ApiClient's
  /// own token/session keys are already single-slot by design, since only
  /// one account is ever logged in on a device at once) -- meaning on a
  /// shared/family device (the norm this app is built for, not an edge
  /// case), a user who queued an application offline, then logged out
  /// and handed the phone to someone else, would have had their still-
  /// pending job application -- CV attached -- silently surfaced to, and
  /// on reconnect auto-submitted as, the NEXT person who logs in. Scoping
  /// by userId here (rather than clearing the whole queue on logout)
  /// preserves the original queuer's own pending application for when
  /// THEY log back in, while making it invisible and unsyncable to
  /// anyone else in the meantime.
  Future<List<PendingApplication>> loadForCurrentUser() async {
    final uid = await ApiClient.instance.getUserId();
    final all = await loadAll();
    return all.where((e) => e.userId != null && e.userId == uid).toList();
  }

  Future<void> _saveAll(List<PendingApplication> items) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _storageKey,
      json.encode(items.map((e) => e.toJson()).toList()),
    );
    _controller.add(items);
  }

  /// Copies the picked file bytes into this app's own storage and adds the
  /// application to the queue. Called when a live /apply submission fails
  /// with what looks like a connectivity problem (see job_screen.dart).
  Future<void> enqueue({
    required int jobId,
    required String jobTitle,
    required String cvFileName,
    required List<int> cvBytes,
    String? supportingFileName,
    List<int>? supportingBytes,
  }) async {
    final dir = await _queueDir();
    final id = '${DateTime.now().microsecondsSinceEpoch}';

    final cvLocalPath = '${dir.path}/${id}_cv_$cvFileName';
    await File(cvLocalPath).writeAsBytes(cvBytes);

    String? supportingLocalPath;
    if (supportingFileName != null && supportingBytes != null) {
      supportingLocalPath = '${dir.path}/${id}_support_$supportingFileName';
      await File(supportingLocalPath).writeAsBytes(supportingBytes);
    }

    final items = await loadAll();
    items.add(PendingApplication(
      id: id,
      userId: await ApiClient.instance.getUserId(),
      jobId: jobId,
      jobTitle: jobTitle,
      cvFileName: cvFileName,
      cvLocalPath: cvLocalPath,
      supportingFileName: supportingFileName,
      supportingLocalPath: supportingLocalPath,
      queuedAt: DateTime.now(),
    ));
    await _saveAll(items);
  }

  Future<void> _removeAndCleanup(PendingApplication item) async {
    final items = await loadAll();
    items.removeWhere((e) => e.id == item.id);
    await _saveAll(items);
    try {
      final cv = File(item.cvLocalPath);
      if (await cv.exists()) await cv.delete();
      if (item.supportingLocalPath != null) {
        final support = File(item.supportingLocalPath!);
        if (await support.exists()) await support.delete();
      }
    } catch (_) {
      // Best-effort cleanup — a leftover file in app-private storage is a
      // minor disk-space cost, not a correctness problem (the queue entry
      // itself, the source of truth, is already gone).
    }
  }

  /// Attempts to submit every queued application belonging to the
  /// CURRENTLY logged-in account (see loadForCurrentUser() -- syncing the
  /// raw, unscoped list here would submit another account's still-queued
  /// application, CV attached, under whichever account happens to be
  /// logged in when connectivity returns). Items that fail again (still
  /// offline, or a real server error) stay queued with an updated status/
  /// error for the UI to show; items that succeed are removed and their
  /// local files cleaned up. Safe to call repeatedly/concurrently —
  /// re-entrant calls are no-ops while a sync is already running.
  Future<void> trySyncAll() async {
    if (_syncing) return;
    _syncing = true;
    try {
      final items = await loadForCurrentUser();
      for (final item in items) {
        await _trySyncOne(item);
      }
    } finally {
      _syncing = false;
    }
  }

  Future<void> _trySyncOne(PendingApplication item) async {
    try {
      final cvFile = File(item.cvLocalPath);
      if (!await cvFile.exists()) {
        // The local file is gone (e.g. storage was cleared) — this entry
        // can never succeed; drop it rather than retry forever.
        await _removeAndCleanup(item);
        return;
      }

      final req = await ApiClient.instance.multipartRequest('/apply');
      req.fields['job_id'] = item.jobId.toString();
      req.files.add(await http.MultipartFile.fromPath('cv', item.cvLocalPath, filename: item.cvFileName));
      if (item.supportingLocalPath != null) {
        final supportFile = File(item.supportingLocalPath!);
        if (await supportFile.exists()) {
          req.files.add(await http.MultipartFile.fromPath(
            'supporting',
            item.supportingLocalPath!,
            filename: item.supportingFileName,
          ));
        }
      }

      final resp = await ApiClient.instance.sendMultipart(req).timeout(ApiClient.timeout);

      if (resp.statusCode == 201) {
        await _removeAndCleanup(item);
        return;
      }

      // A real server response (validation error, already-applied, etc.)
      // means this will never succeed by retrying — surface it and stop
      // retrying automatically, same reasoning as _removeAndCleanup above,
      // but keep the entry visible so the user knows their application
      // did NOT go through (rather than silently dropping it).
      final body = await resp.stream.bytesToString();
      String message = 'Submission failed (${resp.statusCode})';
      try {
        final m = json.decode(body);
        if (m is Map && m['error'] is String) message = m['error'] as String;
      } catch (_) {}
      await _updateStatus(item, status: 'failed', lastError: message);
    } catch (_) {
      // Network/timeout failure — leave queued as 'pending' for the next
      // connectivity-change or manual retry, no error shown (this is the
      // expected, ordinary "still offline" case, not a real failure).
      await _updateStatus(item, status: 'pending', lastError: null);
    }
  }

  Future<void> _updateStatus(PendingApplication item, {required String status, String? lastError}) async {
    final items = await loadAll();
    final idx = items.indexWhere((e) => e.id == item.id);
    if (idx == -1) return;
    items[idx] = items[idx].copyWith(status: status, lastError: lastError);
    await _saveAll(items);
  }

  Future<void> retry(PendingApplication item) => _trySyncOne(item);

  Future<void> discard(PendingApplication item) => _removeAndCleanup(item);
}
