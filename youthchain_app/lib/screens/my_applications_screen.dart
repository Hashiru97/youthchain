import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../services/api_client.dart';

class MyApplicationsScreen extends StatefulWidget {
  final int userId;
  const MyApplicationsScreen({super.key, required this.userId});

  @override
  MyApplicationsScreenState createState() => MyApplicationsScreenState();
}

class MyApplicationsScreenState extends State<MyApplicationsScreen> {
  List applications = [];
  bool isLoading = true;

  @override
  void initState() {
    super.initState();
    fetchApplications();
  }

  Future<void> fetchApplications() async {
    setState(() => isLoading = true);
    try {
      final res = await ApiClient.instance.get(
        "/my_applications/${widget.userId}",
      );
      if (!mounted) return;

      if (res.statusCode == 200) {
        setState(() {
          applications = json.decode(res.body);
          isLoading = false;
        });
      } else {
        setState(() {
          applications = [];
          isLoading = false;
        });
      }
    } catch (_) {
      if (!mounted) return;
      setState(() {
        applications = [];
        isLoading = false;
      });
    }
  }

  Color _statusColor(String s) {
    switch (s.toLowerCase()) {
      case 'accepted':
        return Colors.green;
      case 'rejected':
        return Colors.red;
      default:
        return Colors.orange;
    }
  }

  IconData _statusIcon(String s) {
    switch (s.toLowerCase()) {
      case 'accepted':
        return Icons.check_circle;
      case 'rejected':
        return Icons.cancel;
      default:
        return Icons.hourglass_top;
    }
  }

  Future<void> _openFile(String filename) async {
    // Opened via an external app/browser, which can't carry our
    // Authorization header — the token is passed as a query param instead
    // (backend explicitly supports this for download routes only).
    final token = await ApiClient.instance.getToken();
    final uri = ApiClient.instance
        .uri("/application_file/$filename")
        .replace(queryParameters: {if (token != null) "token": token});
    final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!ok && mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text("Could not open file")));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text("My Applications"),
        backgroundColor: Colors.blue[700],
        centerTitle: true,
        actions: [
          IconButton(
            tooltip: "Refresh",
            icon: const Icon(Icons.refresh),
            onPressed: fetchApplications,
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: fetchApplications,
        child: isLoading
            ? const Center(child: CircularProgressIndicator())
            : applications.isEmpty
            ? ListView(
                children: const [
                  SizedBox(height: 220),
                  Center(child: Text("No applications yet")),
                ],
              )
            : ListView.builder(
                padding: const EdgeInsets.all(12),
                itemCount: applications.length,
                itemBuilder: (context, index) {
                  final app = applications[index];
                  final status = (app["status"] ?? "Pending").toString();

                  final title = (app["job_title"] as String?)?.trim();
                  final location = (app["job_location"] as String?)?.trim();
                  final duration = (app["job_duration"] as String?)?.trim();

                  final leadingTitle = (title != null && title.isNotEmpty)
                      ? title
                      : "Job ID: ${app["job_id"]}";

                  final hasCV = (app["cv_file"] as String?)?.isNotEmpty == true;
                  final hasSupport =
                      (app["supporting_file"] as String?)?.isNotEmpty == true;

                  return Card(
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                    elevation: 4,
                    margin: const EdgeInsets.symmetric(vertical: 8),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 8,
                        vertical: 6,
                      ),
                      child: ListTile(
                        title: Text(
                          leadingTitle,
                          style: const TextStyle(fontWeight: FontWeight.w600),
                        ),
                        subtitle: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            if (location != null && location.isNotEmpty)
                              Row(
                                children: [
                                  const Icon(
                                    Icons.location_on,
                                    size: 16,
                                    color: Colors.redAccent,
                                  ),
                                  const SizedBox(width: 4),
                                  Flexible(child: Text(location)),
                                ],
                              ),
                            if (duration != null && duration.isNotEmpty)
                              Padding(
                                padding: const EdgeInsets.only(top: 2),
                                child: Row(
                                  children: [
                                    const Icon(
                                      Icons.schedule,
                                      size: 16,
                                      color: Colors.blueGrey,
                                    ),
                                    const SizedBox(width: 4),
                                    Text(duration),
                                  ],
                                ),
                              ),
                            Padding(
                              padding: const EdgeInsets.only(top: 6),
                              child: Row(
                                children: [
                                  Icon(
                                    _statusIcon(status),
                                    size: 16,
                                    color: _statusColor(status),
                                  ),
                                  const SizedBox(width: 6),
                                  Text("Status: $status"),
                                ],
                              ),
                            ),
                          ],
                        ),
                        trailing: Wrap(
                          spacing: 6,
                          children: [
                            if (hasCV)
                              IconButton(
                                tooltip: "Open CV",
                                icon: const Icon(Icons.picture_as_pdf),
                                onPressed: () => _openFile(app["cv_file"]),
                              ),
                            if (hasSupport)
                              IconButton(
                                tooltip: "Open supporting doc",
                                icon: const Icon(Icons.attach_file),
                                onPressed: () =>
                                    _openFile(app["supporting_file"]),
                              ),
                          ],
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
