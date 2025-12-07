import 'dart:async';
import 'dart:convert';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:socket_io_client/socket_io_client.dart' as IO;

import 'my_applications_screen.dart';
import 'passport_screen.dart';
import 'profile_cv_screen.dart';


class JobScreen extends StatefulWidget {
  final int userId;
  final VoidCallback? onLoggedOut; // optional hook to clear saved auth

  const JobScreen({super.key, required this.userId, this.onLoggedOut});

  @override
  JobScreenState createState() => JobScreenState();
}

class JobScreenState extends State<JobScreen> {
  static const _base = "http://127.0.0.1:5000";
  static const _timeout = Duration(seconds: 30);

  List jobs = [];
  Set<int> appliedJobIds = {};
  bool isLoading = true;
  bool isUploading = false;

  IO.Socket? _socket;

  @override
  void initState() {
    super.initState();
    _initialLoad();
    _connectSocket();
  }

  @override
  void dispose() {
    try {
      _socket?.off('connect');
      _socket?.off('disconnect');
      _socket?.off('connect_error');
      _socket?.off('error');
      _socket?.off('job_created');
      _socket?.off('application_created');
      _socket?.off('application_status_changed');
      _socket?.disconnect();
      _socket?.dispose();
    } catch (_) {}
    super.dispose();
  }

  Future<void> _initialLoad() async {
    setState(() => isLoading = true);
    await Future.wait([fetchJobs(), fetchAppliedJobs()]);
    if (!mounted) return;
    setState(() => isLoading = false);
  }

  // ---------------- Socket.IO (Realtime) ----------------
  void _connectSocket() {
    // IMPORTANT: Flask-SocketIO default path is "/socket.io" (no trailing slash)
    _socket = IO.io(
      _base,
      IO.OptionBuilder()
          .setPath('/socket.io')
          .setTransports(['websocket', 'polling'])
          .enableReconnection()
          .setReconnectionAttempts(1 << 20)
          .setReconnectionDelay(800)
          .build(),
    );

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

    // Status change (accept / reject) – for future use
    _socket!.on('application_status_changed', (data) async {
      debugPrint('[socket] application_status_changed received');
      // You could refresh MyApplications here later if you want live status.
    });
  }

  // ---------------- API calls ----------------

  Future<void> fetchJobs() async {
    try {
      final res = await http
          .get(Uri.parse("$_base/api/match_jobs/${widget.userId}"))
          .timeout(_timeout);

      if (!mounted) return;

      if (res.statusCode == 200) {
        final body = json.decode(res.body);
        jobs = (body["jobs"] as List? ?? []);
        setState(() {});
      } else {
        jobs = [];
        setState(() {});
      }
    } on TimeoutException {
      if (!mounted) return;
      jobs = [];
      setState(() {});
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text("Timeout loading jobs.")));
    } catch (_) {
      if (!mounted) return;
      jobs = [];
      setState(() {});
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Network error loading jobs.")),
      );
    }
  }

  Future<void> fetchAppliedJobs() async {
    try {
      final res = await http
          .get(Uri.parse("$_base/my_applications/${widget.userId}"))
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

  Future<void> _showApplySheet(int jobId) async {
    XFile? cvFile;
    XFile? supportFile;

    const docsGroup = XTypeGroup(
      label: 'Documents',
      extensions: ['pdf', 'doc', 'docx', 'png', 'jpg', 'jpeg'],
    );

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
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

            Future<void> submit() async {
              if (cvFile == null) {
                if (!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text("CV is required.")),
                );
                return;
              }

              Navigator.of(ctx).pop();
              setState(() => isUploading = true);

              try {
                final uri = Uri.parse("$_base/apply");
                final req = http.MultipartRequest("POST", uri);
                req.fields["user_id"] = widget.userId.toString();
                req.fields["job_id"] = jobId.toString();

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
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text("✅ Application submitted")),
                  );
                } else {
                  String msg = "❌ Failed to submit application";
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
                if (!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text("Timeout while uploading.")),
                );
              } catch (_) {
                if (!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text("Network error submitting application"),
                  ),
                );
              } finally {
                if (mounted) setState(() => isUploading = false);
              }
            }

            return Padding(
              padding: EdgeInsets.only(
                left: 16,
                right: 16,
                bottom: MediaQuery.of(context).viewInsets.bottom + 16,
                top: 16,
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Center(
                    child: Container(
                      height: 4,
                      width: 44,
                      margin: const EdgeInsets.only(bottom: 12),
                      decoration: BoxDecoration(
                        color: Colors.black26,
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                  ),
                  const Text(
                    "Apply to job",
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 12),
                  ListTile(
                    leading: const Icon(Icons.description),
                    title: const Text("CV (required)"),
                    subtitle: Text(
                      cvFile?.name ?? "No file selected",
                      overflow: TextOverflow.ellipsis,
                    ),
                    trailing: OutlinedButton(
                      onPressed: pickCV,
                      child: const Text("Pick CV"),
                    ),
                  ),
                  const SizedBox(height: 8),
                  ListTile(
                    leading: const Icon(Icons.attach_file),
                    title: const Text("Supporting doc (optional)"),
                    subtitle: Text(
                      supportFile?.name ?? "No file selected",
                      overflow: TextOverflow.ellipsis,
                    ),
                    trailing: OutlinedButton(
                      onPressed: pickSupport,
                      child: const Text("Pick file"),
                    ),
                  ),
                  const SizedBox(height: 16),
                  SizedBox(
                    width: double.infinity,
                    child: ElevatedButton.icon(
                      icon: const Icon(Icons.send),
                      label: const Text("Submit Application"),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: Colors.green[700],
                        minimumSize: const Size(double.infinity, 44),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
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

  void _logout() {
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

  Color _scoreColor(int s) =>
      s >= 70 ? Colors.green : (s >= 40 ? Colors.orange : Colors.red);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.green[50],
      appBar: AppBar(
        title: const Text("YouthChain Jobs"),
        backgroundColor: Colors.green[700],
        centerTitle: true,
        actions: [
          IconButton(
            tooltip: "My Profile & CV",
            icon: const Icon(Icons.person, color: Colors.white),
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => ProfileCvScreen(userId: widget.userId),
                ),
              ).then((_) async {
                // if skills changed, refresh matches
                await _initialLoad();
              });
            },
          ),
          IconButton(
            tooltip: "Refresh",
            icon: const Icon(Icons.refresh, color: Colors.white),
            onPressed: () async {
              await Future.wait([fetchJobs(), fetchAppliedJobs()]);
            },
          ),
          IconButton(
            tooltip: "Logout",
            icon: const Icon(Icons.logout, color: Colors.white),
            onPressed: _logout,
          ),
        ],

      body: isLoading
          ? const Center(child: CircularProgressIndicator())
          : jobs.isEmpty
          ? const Center(
              child: Text(
                "No jobs available right now.",
                style: TextStyle(fontSize: 16, color: Colors.black54),
              ),
            )
          : ListView.builder(
              padding: const EdgeInsets.all(12),
              itemCount: jobs.length,
              itemBuilder: (context, index) {
                final job = jobs[index];
                final int jobId = (job["id"] as num).toInt();
                final bool alreadyApplied = appliedJobIds.contains(jobId);

                final String? requiredSkills =
                    (job["required_skills"] as String?)?.trim();
                final int score = (job["score"] ?? 0) as int;
                final skillList = _parseSkills(requiredSkills);

                return Card(
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(16),
                  ),
                  elevation: 6,
                  margin: const EdgeInsets.symmetric(vertical: 10),
                  child: Padding(
                    padding: const EdgeInsets.all(16.0),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Icon(
                              Icons.work,
                              color: Colors.green,
                              size: 26,
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    job["title"],
                                    style: TextStyle(
                                      fontSize: 18,
                                      fontWeight: FontWeight.bold,
                                      color: Colors.green[900],
                                    ),
                                  ),
                                  const SizedBox(height: 4),
                                  Row(
                                    children: [
                                      const Icon(
                                        Icons.location_on,
                                        size: 18,
                                        color: Colors.redAccent,
                                      ),
                                      const SizedBox(width: 4),
                                      Expanded(
                                        child: Text(
                                          job["location"],
                                          style: const TextStyle(fontSize: 13),
                                        ),
                                      ),
                                    ],
                                  ),
                                  const SizedBox(height: 2),
                                  Row(
                                    children: [
                                      const Icon(
                                        Icons.schedule,
                                        size: 16,
                                        color: Colors.blueGrey,
                                      ),
                                      const SizedBox(width: 4),
                                      Text(
                                        job["duration"],
                                        style: const TextStyle(fontSize: 13),
                                      ),
                                    ],
                                  ),
                                ],
                              ),
                            ),
                            Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 10,
                                vertical: 4,
                              ),
                              decoration: BoxDecoration(
                                color: _scoreColor(score),
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: Text(
                                "$score% Match",
                                style: const TextStyle(
                                  fontSize: 12,
                                  color: Colors.white,
                                ),
                              ),
                            ),
                          ],
                        ),
                        if (requiredSkills != null &&
                            requiredSkills.isNotEmpty) ...[
                          const SizedBox(height: 10),
                          const Text(
                            "Required skills:",
                            style: TextStyle(
                              fontSize: 13,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          const SizedBox(height: 4),
                          Wrap(
                            spacing: 6,
                            runSpacing: -4,
                            children: skillList
                                .map(
                                  (s) => Chip(
                                    label: Text(
                                      s,
                                      style: const TextStyle(fontSize: 12),
                                    ),
                                    backgroundColor: Colors.blue[50],
                                  ),
                                )
                                .toList(),
                          ),
                        ],
                        const SizedBox(height: 12),
                        ElevatedButton.icon(
                          onPressed: (isUploading || alreadyApplied)
                              ? null
                              : () => _showApplySheet(jobId),
                          style: ElevatedButton.styleFrom(
                            backgroundColor: alreadyApplied
                                ? Colors.grey
                                : Colors.green[700],
                            minimumSize: const Size(double.infinity, 45),
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(12),
                            ),
                          ),
                          icon: Icon(
                            alreadyApplied ? Icons.check : Icons.send,
                            color: Colors.white,
                          ),
                          label: Text(
                            alreadyApplied
                                ? "Applied"
                                : (isUploading
                                      ? "Uploading..."
                                      : "Apply with CV"),
                          ),
                        ),
                      ],
                    ),
                  ),
                );
              },
            ),
      floatingActionButtonLocation: FloatingActionButtonLocation.endDocked,
      floatingActionButton: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          FloatingActionButton.extended(
            backgroundColor: Colors.blue[700],
            icon: const Icon(Icons.history),
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
          const SizedBox(height: 12),
          FloatingActionButton.extended(
            backgroundColor: Colors.purple,
            icon: const Icon(Icons.card_membership),
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
        ],
      ),
    );
  }
}
