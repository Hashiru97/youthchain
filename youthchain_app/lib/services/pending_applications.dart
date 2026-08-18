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

  /// Attempts to submit every queued application. Items that fail again
  /// (still offline, or a real server error) stay queued with an updated
  /// status/error for the UI to show; items that succeed are removed and
  /// their local files cleaned up. Safe to call repeatedly/concurrently —
  /// re-entrant calls are no-ops while a sync is already running.
  Future<void> trySyncAll() async {
    if (_syncing) return;
    _syncing = true;
    try {
      final items = await loadAll();
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
