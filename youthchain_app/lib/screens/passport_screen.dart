import 'dart:convert';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

import '../services/api_client.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';
import '../widgets/status_badge.dart';

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
  bool _showingCachedData = false;

  @override
  void initState() {
    super.initState();
    fetchCredentials();
  }

  Future<void> fetchCredentials() async {
    setState(() => isLoading = true);
    try {
      // BL-37: falls back to the last successfully loaded copy when offline.
      final result = await ApiClient.instance.getWithCache(
        "/passport/${widget.userId}",
      );
      if (!mounted) return;
      setState(() {
        credentials = json.decode(result.body);
        _showingCachedData = result.fromCache;
        isLoading = false;
      });
    } on NoCachedDataException {
      if (!mounted) return;
      setState(() {
        credentials = [];
        isLoading = false;
      });
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
    try {
      final uri = ApiClient.instance.uri("/verify/$credId");
      final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
      if (!ok && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Could not open verification page for $title")),
        );
      }
    } catch (_) {
      if (!mounted) return;
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
      backgroundColor: context.colors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadius.lg)),
      ),
      builder: (ctx) {
        return StatefulBuilder(
          builder: (ctx, setSheetState) {
            Future<void> pickFile() async {
              final res = await openFile(acceptedTypeGroups: const [docsGroup]);
              if (res != null) setSheetState(() => file = res);
            }

            Future<void> submit() async {
              if (titleCtrl.text.trim().isEmpty ||
                  issuerCtrl.text.trim().isEmpty) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text("Title and issuer are required."),
                  ),
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
                  content: Text("Uploading certificate..."),
                  duration: Duration(seconds: 5),
                ),
              );

              try {
                final req = await ApiClient.instance.multipartRequest(
                  "/issue_credential",
                );
                req.fields["title"] = titleCtrl.text.trim();
                req.fields["issuer"] = issuerCtrl.text.trim();

                if (file!.path.isNotEmpty) {
                  req.files.add(
                    await http.MultipartFile.fromPath("file", file!.path),
                  );
                } else {
                  final bytes = await file!.readAsBytes();
                  req.files.add(
                    http.MultipartFile.fromBytes(
                      "file",
                      bytes,
                      filename: file!.name,
                    ),
                  );
                }

                // The backend's on-chain write now happens in a background
                // thread after this request already has its answer (BL-18
                // — it used to be a synchronous subprocess call with its
                // own ~90s ceiling, which this timeout used to have to
                // cover). This request only waits on the upload + the
                // credential's own DB row being created, both fast; a
                // generous timeout here is just normal network-hiccup
                // headroom now, not a blockchain-write budget.
                final resp = await req.send().timeout(
                  const Duration(seconds: 30),
                );
                final body = await resp.stream.bytesToString();

                if (!mounted) return;

                if (resp.statusCode == 201 || resp.statusCode == 200) {
                  final data = jsonDecodeSafe(body);
                  final onChainStatus = data?["onchain_status"] as String?;
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                      content: Text(
                        onChainStatus == "confirmed"
                            ? "Credential issued and verified on-chain"
                            : "Credential issued — verifying on-chain in the background, "
                                  "we'll notify you when it's confirmed",
                      ),
                    ),
                  );
                  await fetchCredentials();
                } else if (resp.statusCode == 413) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text("File too large (max 16 MB)")),
                  );
                } else {
                  final data = jsonDecodeSafe(body);
                  final msg =
                      (data?["error"] as String?) ??
                      "Failed to issue credential";
                  ScaffoldMessenger.of(
                    context,
                  ).showSnackBar(SnackBar(content: Text(msg)));
                }
              } catch (_) {
                if (!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text("Network error issuing credential"),
                  ),
                );
              } finally {
                if (mounted) setState(() => _submitting = false);
              }
            }

            return Padding(
              padding: EdgeInsets.only(
                left: AppSpacing.md,
                right: AppSpacing.md,
                bottom: MediaQuery.of(ctx).viewInsets.bottom + AppSpacing.md,
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
                  Row(
                    children: [
                      Container(
                        width: 40,
                        height: 40,
                        decoration: BoxDecoration(
                          color: context.colors.passportLight,
                          shape: BoxShape.circle,
                        ),
                        child: Icon(
                          Icons.add_moderator_rounded,
                          color: context.colors.passport,
                          size: 20,
                        ),
                      ),
                      const SizedBox(width: AppSpacing.sm),
                      Text(
                        "Add a credential",
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                    ],
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  Text(
                    "Upload a certificate you already earned. YouthChain hashes it and "
                    "registers the hash on-chain so employers can verify it hasn't been altered.",
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                  const SizedBox(height: AppSpacing.md),
                  TextField(
                    controller: titleCtrl,
                    decoration: const InputDecoration(
                      labelText: "Certificate title",
                    ),
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  TextField(
                    controller: issuerCtrl,
                    decoration: const InputDecoration(labelText: "Issued by"),
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  Container(
                    padding: const EdgeInsets.all(AppSpacing.sm),
                    decoration: BoxDecoration(
                      color: context.colors.background,
                      borderRadius: BorderRadius.circular(AppRadius.sm),
                      border: Border.all(color: context.colors.outline),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          Icons.attach_file_rounded,
                          color: context.colors.textSecondary,
                          size: 20,
                        ),
                        const SizedBox(width: AppSpacing.sm),
                        Expanded(
                          child: Text(
                            file?.name ?? "No file selected",
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                        ),
                        OutlinedButton(
                          onPressed: pickFile,
                          child: const Text("Pick file"),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: AppSpacing.lg),
                  SizedBox(
                    width: double.infinity,
                    height: 50,
                    child: ElevatedButton.icon(
                      icon: const Icon(Icons.upload_rounded, size: 18),
                      label: const Text("Submit"),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: context.colors.passport,
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
      backgroundColor: context.colors.background,
      appBar: AppBar(
        title: const Text("Employment Passport"),
        backgroundColor: context.colors.passport,
        actions: [
          IconButton(
            tooltip: "Refresh",
            icon: const Icon(Icons.refresh_rounded),
            onPressed: fetchCredentials,
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: context.colors.passport,
        icon: _submitting
            ? const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: Colors.white,
                ),
              )
            : const Icon(Icons.add_rounded),
        label: Text(_submitting ? "Uploading..." : "Add Credential"),
        onPressed: _submitting ? null : _showAddCredentialSheet,
      ),
      body: Column(
        children: [
          if (_showingCachedData)
            Container(
              width: double.infinity,
              color: context.colors.warningBg,
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.md,
                vertical: AppSpacing.sm,
              ),
              child: Semantics(
                liveRegion: true,
                label: "You're offline. Showing previously loaded credentials.",
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
                        "You're offline. Showing previously loaded credentials.",
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
          Expanded(child: _buildCredentialsList()),
        ],
      ),
    );
  }

  Widget _buildCredentialsList() {
    return RefreshIndicator(
      onRefresh: fetchCredentials,
      child: isLoading
          ? const SkeletonListView()
          : credentials.isEmpty
          ? ListView(
              children: [
                const SizedBox(height: 120),
                EmptyState(
                  icon: Icons.workspace_premium_outlined,
                  title: "No credentials yet",
                  subtitle:
                      "Complete internships or training to earn verifiable certificates.",
                ),
              ],
            )
          : ListView.builder(
              padding: const EdgeInsets.all(AppSpacing.md),
              itemCount: credentials.length,
              itemBuilder: (context, index) {
                final cred = credentials[index];
                final bool onChain =
                    (cred["onchain_tx"] as String?)?.isNotEmpty == true;
                final bool revoked = cred["revoked_at"] != null;

                final String title = cred["title"] ?? "";
                final String issuer = (cred["issuer"] as String?) ?? "";
                final int credId = (cred["id"] as num).toInt();

                return Container(
                  margin: const EdgeInsets.only(bottom: AppSpacing.md),
                  // A colored left-accent stripe combined with a rounded
                  // corner needs a uniform-color Border on the decoration
                  // itself -- Flutter's Border painter asserts "A
                  // borderRadius can only be given on borders with uniform
                  // colors" and throws at paint time otherwise (real,
                  // reproduced crash caught via WorkHistoryScreen's widget
                  // test, which shares this same card-accent pattern). The
                  // accent is drawn as a separate slim child instead,
                  // clipped to the same rounded rect via clipBehavior, with
                  // a plain uniform outline border on the decoration (which
                  // radius is fine with).
                  clipBehavior: Clip.antiAlias,
                  decoration: BoxDecoration(
                    color: context.colors.surface,
                    borderRadius: BorderRadius.circular(AppRadius.md),
                    border: Border.all(color: context.colors.outline),
                    boxShadow: cardShadow,
                  ),
                  // IntrinsicHeight: a plain Row(crossAxisAlignment:
                  // stretch) needs a bounded height to stretch its children
                  // to, but this Container sits directly in a
                  // ListView.builder, which gives each item unbounded
                  // height -- real, reproduced crash ("BoxConstraints
                  // forces an infinite height") caught via
                  // WorkHistoryScreen's widget test, which shares this same
                  // pattern. IntrinsicHeight makes the Row size itself from
                  // its tallest child first, giving `stretch` something
                  // finite to stretch the accent stripe to.
                  child: IntrinsicHeight(
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        // Same real-legibility fix as My Applications' cards: a
                        // 4px colored accent (this screen's own teal) instead
                        // of relying on a near-invisible grey outline + faint
                        // shadow alone to separate a white card from a
                        // near-white background. Full teal once on-chain
                        // writing is confirmed, a muted version while still
                        // pending — carries the same signal
                        // StatusBadge.onChain already gives, just also
                        // visible at a glance without reading the badge text.
                        Container(
                          width: 4,
                          color: revoked
                              ? context.colors.error
                              : (onChain
                                    ? context.colors.passport
                                    : context.colors.outline),
                        ),
                        Expanded(
                          child: Semantics(
                            button: true,
                            label: revoked
                                ? "$title, revoked by a YouthChain administrator. Double tap to view verification details."
                                : (onChain
                                      ? "$title, verified on-chain. Double tap to view verification details."
                                      : "$title, not yet confirmed on-chain. Double tap to view verification details."),
                            child: InkWell(
                              onTap: () => _openVerifyPage(credId, title),
                              child: Padding(
                                padding: const EdgeInsets.all(AppSpacing.md),
                                child: Row(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Container(
                                      width: 44,
                                      height: 44,
                                      decoration: BoxDecoration(
                                        color: context.colors.passportLight,
                                        borderRadius: BorderRadius.circular(
                                          AppRadius.sm,
                                        ),
                                      ),
                                      child: Icon(
                                        Icons.workspace_premium_rounded,
                                        color: context.colors.passport,
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
                                            title,
                                            style: Theme.of(
                                              context,
                                            ).textTheme.titleMedium,
                                          ),
                                          if (issuer.isNotEmpty) ...[
                                            const SizedBox(height: 2),
                                            Text(
                                              issuer,
                                              style: Theme.of(
                                                context,
                                              ).textTheme.bodySmall,
                                            ),
                                          ],
                                          const SizedBox(height: AppSpacing.sm),
                                          Wrap(
                                            spacing: 6,
                                            runSpacing: 6,
                                            children: [
                                              if ((cred["year"] ?? "")
                                                  .toString()
                                                  .isNotEmpty)
                                                StatusBadge(
                                                  label: (cred["year"])
                                                      .toString(),
                                                  color:
                                                      context.colors.textSecondary,
                                                  background:
                                                      context.colors.background,
                                                  dense: true,
                                                ),
                                              StatusBadge.onChain(context, onChain, revoked: revoked),
                                            ],
                                          ),
                                        ],
                                      ),
                                    ),
                                    Icon(
                                      Icons.chevron_right_rounded,
                                      color: context.colors.textMuted,
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                );
              },
            ),
    );
  }
}
