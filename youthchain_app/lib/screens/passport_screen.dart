import 'dart:convert';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

import '../services/api_client.dart';

Map<String, dynamic>? jsonDecodeSafe(String body) {
  try {
    final v = json.decode(body);
    return v is Map<String, dynamic> ? v : null;
  } catch (_) {
    return null;
  }
}

class PassportScreen extends StatefulWidget {
  final int userId;
  const PassportScreen({super.key, required this.userId});

  @override
  PassportScreenState createState() => PassportScreenState();
}

class PassportScreenState extends State<PassportScreen> {
  List credentials = [];
  bool isLoading = true;
  bool _submitting = false;

  @override
  void initState() {
    super.initState();
    fetchCredentials();
  }

  Future<void> fetchCredentials() async {
    setState(() => isLoading = true);
    try {
      final res = await ApiClient.instance.get("/passport/${widget.userId}");
      if (!mounted) return;
      if (res.statusCode == 200) {
        setState(() {
          credentials = json.decode(res.body);
          isLoading = false;
        });
      } else {
        setState(() {
          credentials = [];
          isLoading = false;
        });
      }
    } catch (_) {
      if (!mounted) return;
      setState(() {
        credentials = [];
        isLoading = false;
      });
    }
  }

  Future<void> _openVerifyPage(int credId, String title) async {
    // /verify/<id> is a deliberately public, unauthenticated route (anyone
    // holding a physical certificate should be able to check it), so no
    // token is attached here.
    final uri = ApiClient.instance.uri("/verify/$credId");
    final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!ok && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Could not open verification page for $title")),
      );
    }
  }

  Future<void> _showAddCredentialSheet() async {
    final titleCtrl = TextEditingController();
    final issuerCtrl = TextEditingController();
    XFile? file;

    const docsGroup = XTypeGroup(
      label: 'Certificate',
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
            Future<void> pickFile() async {
              final res = await openFile(acceptedTypeGroups: const [docsGroup]);
              if (res != null) setSheetState(() => file = res);
            }

            Future<void> submit() async {
              if (titleCtrl.text.trim().isEmpty || issuerCtrl.text.trim().isEmpty) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text("Title and issuer are required.")),
                );
                return;
              }
              if (file == null) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text("Select the certificate file.")),
                );
                return;
              }

              Navigator.of(ctx).pop();
              setState(() => _submitting = true);
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(
                  content: Text(
                    "Uploading and registering on-chain — this can take up to a minute...",
                  ),
                  duration: Duration(seconds: 8),
                ),
              );

              try {
                final req = await ApiClient.instance.multipartRequest("/issue_credential");
                req.fields["title"] = titleCtrl.text.trim();
                req.fields["issuer"] = issuerCtrl.text.trim();

                if (file!.path.isNotEmpty) {
                  req.files.add(await http.MultipartFile.fromPath("file", file!.path));
                } else {
                  final bytes = await file!.readAsBytes();
                  req.files.add(
                    http.MultipartFile.fromBytes("file", bytes, filename: file!.name),
                  );
                }

                // The backend's on-chain write is a synchronous subprocess
                // call with its own ~90s ceiling (see Phase 4/6 of the
                // engineering review — BL-18 tracks moving this to a
                // background job); give the client enough headroom to not
                // time out before the backend does.
                final resp = await req.send().timeout(const Duration(seconds: 100));
                final body = await resp.stream.bytesToString();

                if (!mounted) return;

                if (resp.statusCode == 201 || resp.statusCode == 200) {
                  final data = jsonDecodeSafe(body);
                  final onChain = data?["onchain_tx"] != null;
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                      content: Text(
                        onChain
                            ? "✅ Credential issued and verified on-chain"
                            : "✅ Credential issued (on-chain write pending — try reopening this later)",
                      ),
                    ),
                  );
                  await fetchCredentials();
                } else {
                  final data = jsonDecodeSafe(body);
                  final msg = (data?["error"] as String?) ?? "Failed to issue credential";
                  ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
                }
              } catch (_) {
                if (!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text("Network error issuing credential")),
                );
              } finally {
                if (mounted) setState(() => _submitting = false);
              }
            }

            return Padding(
              padding: EdgeInsets.only(
                left: 16,
                right: 16,
                bottom: MediaQuery.of(ctx).viewInsets.bottom + 16,
                top: 16,
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    "Add a Credential",
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 4),
                  const Text(
                    "Upload a certificate you already earned. YouthChain hashes it and "
                    "registers the hash on-chain so employers can verify it hasn't been altered.",
                    style: TextStyle(fontSize: 12, color: Colors.black54),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: titleCtrl,
                    decoration: const InputDecoration(labelText: "Certificate title"),
                  ),
                  const SizedBox(height: 8),
                  TextField(
                    controller: issuerCtrl,
                    decoration: const InputDecoration(labelText: "Issued by"),
                  ),
                  const SizedBox(height: 8),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.attach_file),
                    title: Text(file?.name ?? "No file selected"),
                    trailing: OutlinedButton(onPressed: pickFile, child: const Text("Pick file")),
                  ),
                  const SizedBox(height: 12),
                  SizedBox(
                    width: double.infinity,
                    child: ElevatedButton.icon(
                      icon: const Icon(Icons.upload),
                      label: const Text("Submit"),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: Colors.purple[700],
                        minimumSize: const Size(double.infinity, 44),
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.purple[50],
      appBar: AppBar(
        title: const Text("Employment Passport"),
        backgroundColor: Colors.purple[700],
        centerTitle: true,
        actions: [
          IconButton(
            tooltip: "Refresh",
            icon: const Icon(Icons.refresh),
            onPressed: fetchCredentials,
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: Colors.purple[700],
        icon: _submitting
            ? const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
              )
            : const Icon(Icons.add),
        label: Text(_submitting ? "Uploading..." : "Add Credential"),
        onPressed: _submitting ? null : _showAddCredentialSheet,
      ),
      body: RefreshIndicator(
        onRefresh: fetchCredentials,
        child: isLoading
            ? const Center(child: CircularProgressIndicator())
            : credentials.isEmpty
            ? ListView(
                children: [
                  const SizedBox(height: 220),
                  Center(
                    child: Text(
                      "No credentials yet.\nComplete internships or training to earn certificates.",
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 16, color: Colors.black54),
                    ),
                  ),
                ],
              )
            : ListView.builder(
                padding: const EdgeInsets.all(16),
                itemCount: credentials.length,
                itemBuilder: (context, index) {
                  final cred = credentials[index];
                  final bool onChain =
                      (cred["onchain_tx"] as String?)?.isNotEmpty == true;

                  final String title = cred["title"] ?? "";
                  final int credId = (cred["id"] as num).toInt();

                  return Card(
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(16),
                    ),
                    elevation: 6,
                    margin: const EdgeInsets.symmetric(vertical: 10),
                    child: InkWell(
                      borderRadius: BorderRadius.circular(16),
                      onTap: () => _openVerifyPage(credId, title),
                      child: Padding(
                        padding: const EdgeInsets.all(16.0),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                const Icon(
                                  Icons.verified,
                                  color: Colors.green,
                                  size: 30,
                                ),
                                const SizedBox(width: 10),
                                Expanded(
                                  child: Text(
                                    cred["title"] ?? "",
                                    style: TextStyle(
                                      fontSize: 18,
                                      fontWeight: FontWeight.bold,
                                      color: Colors.purple[900],
                                    ),
                                  ),
                                ),
                                Column(
                                  crossAxisAlignment: CrossAxisAlignment.end,
                                  children: [
                                    Chip(
                                      label: Text(
                                        (cred["year"] ?? "").toString(),
                                        style: const TextStyle(
                                          color: Colors.white,
                                        ),
                                      ),
                                      backgroundColor: Colors.purple[700],
                                    ),
                                    const SizedBox(height: 4),
                                    if (onChain)
                                      Chip(
                                        label: const Text(
                                          "On-chain",
                                          style: TextStyle(
                                            color: Colors.white,
                                            fontSize: 12,
                                          ),
                                        ),
                                        backgroundColor: Colors.green[700],
                                      ),
                                  ],
                                ),
                              ],
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
