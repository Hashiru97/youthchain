import 'dart:convert';

import 'package:flutter/material.dart';

import '../services/api_client.dart';
import '../theme/app_theme.dart';

class ProfileCvScreen extends StatefulWidget {
  final int userId;
  const ProfileCvScreen({super.key, required this.userId});

  @override
  State<ProfileCvScreen> createState() => _ProfileCvScreenState();
}

class _ProfileCvScreenState extends State<ProfileCvScreen> {
  final _formKey = GlobalKey<FormState>();

  final _nameCtrl = TextEditingController();
  final _emailCtrl = TextEditingController();
  final _locationCtrl = TextEditingController();
  final _skillsCtrl = TextEditingController();
  final _bioCtrl = TextEditingController();

  bool _saving = false;
  bool _loadingCv = false;
  bool _loadingProfile = true;
  String? _cvText;

  // Industry-matching preference (see GET /api/industries, POST
  // /api/candidate "preferred_industries") — fetched once so the checkbox
  // options can never drift from the backend's canonical list.
  List<String> _industryOptions = [];
  final Set<String> _selectedIndustries = {};

  @override
  void initState() {
    super.initState();
    _loadExistingProfile();
    _loadIndustries();
  }

  Future<void> _loadIndustries() async {
    try {
      final res = await ApiClient.instance.get('/api/industries');
      if (!mounted) return;
      if (res.statusCode == 200) {
        final body = json.decode(res.body);
        final industries = body is Map ? body['industries'] : null;
        if (industries is List) {
          setState(() {
            _industryOptions = industries.map((e) => e.toString()).toList();
          });
        }
      }
    } catch (_) {
      // Non-critical — the section just won't offer any options this cycle.
    }
  }

  Future<void> _loadExistingProfile() async {
    try {
      final res = await ApiClient.instance.get('/api/candidate/me');
      if (!mounted) return;
      if (res.statusCode == 200) {
        final body = json.decode(res.body);
        final candidate = body is Map ? body['candidate'] : null;
        if (candidate is Map) {
          if (candidate['id'] != null) {
            await ApiClient.instance.saveCandidateId(
              (candidate['id'] as num).toInt(),
            );
          }
          _nameCtrl.text = (candidate['name'] ?? '').toString();
          _emailCtrl.text = (candidate['email'] ?? '').toString();
          _locationCtrl.text = (candidate['location'] ?? '').toString();
          _skillsCtrl.text = (candidate['skills'] ?? '').toString();
          _bioCtrl.text = (candidate['bio'] ?? '').toString();
          final preferredIndustries = candidate['preferred_industries'];
          if (preferredIndustries is String) {
            setState(() {
              _selectedIndustries
                ..clear()
                ..addAll(
                  preferredIndustries
                      .split(',')
                      .map((e) => e.trim())
                      .where((e) => e.isNotEmpty),
                );
            });
          }
        }
      }
    } catch (_) {
      // No existing profile yet, or a transient network error — the form
      // just starts blank either way, same as before this screen existed.
    } finally {
      if (mounted) setState(() => _loadingProfile = false);
    }
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _emailCtrl.dispose();
    _locationCtrl.dispose();
    _skillsCtrl.dispose();
    _bioCtrl.dispose();
    super.dispose();
  }

  Future<void> _saveProfile() async {
    if (!_formKey.currentState!.validate()) return;

    setState(() => _saving = true);
    try {
      final res = await ApiClient.instance.postJson("/api/candidate", {
        "name": _nameCtrl.text.trim(),
        "email": _emailCtrl.text.trim(),
        "location": _locationCtrl.text.trim(),
        "skills": _skillsCtrl.text.trim(), // comma-separated
        "bio": _bioCtrl.text.trim(),
        "preferred_industries": _selectedIndustries.toList(),
      });

      if (!mounted) return;
      if (res.statusCode == 200 || res.statusCode == 201) {
        final body = json.decode(res.body);
        if (body is Map && body['candidate_id'] != null) {
          await ApiClient.instance.saveCandidateId(
            (body['candidate_id'] as num).toInt(),
          );
        }
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text("Profile saved & indexed for matching")),
        );
      } else {
        final body = json.decode(res.body);
        final msg = body is Map && body["error"] is String
            ? body["error"] as String
            : "Failed to save profile";
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(msg)));
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Network error while saving profile")),
      );
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _generateCv() async {
    setState(() {
      _loadingCv = true;
      _cvText = null;
    });
    try {
      final candidateId = await ApiClient.instance.resolveCandidateId();
      if (candidateId == null) {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              "Save your profile first so we can build a CV from it.",
            ),
          ),
        );
        return;
      }

      final res = await ApiClient.instance.get('/api/generate_cv/$candidateId');

      if (!mounted) return;

      if (res.statusCode == 200) {
        final data = json.decode(res.body);
        setState(() {
          _cvText = data["cv"] as String?;
        });
      } else {
        final body = json.decode(res.body);
        final msg = body is Map && body["error"] is String
            ? body["error"] as String
            : "Could not generate CV";
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(msg)));
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Network error while generating CV")),
      );
    } finally {
      if (mounted) setState(() => _loadingCv = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: context.colors.background,
      appBar: AppBar(title: const Text("My Profile & CV")),
      body: SafeArea(
        child: _loadingProfile
            ? const Center(child: CircularProgressIndicator())
            : SingleChildScrollView(
                padding: const EdgeInsets.all(AppSpacing.md),
                child: Column(
                  children: [
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(AppSpacing.md),
                      decoration: BoxDecoration(
                        color: context.colors.surface,
                        borderRadius: BorderRadius.circular(AppRadius.md),
                        border: Border.all(color: context.colors.outline),
                        boxShadow: cardShadow,
                      ),
                      child: Form(
                        key: _formKey,
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Container(
                                  width: 40,
                                  height: 40,
                                  decoration: BoxDecoration(
                                    color: context.colors.primaryLight,
                                    shape: BoxShape.circle,
                                  ),
                                  child: Icon(
                                    Icons.person_outline_rounded,
                                    color: context.colors.primary,
                                    size: 20,
                                  ),
                                ),
                                const SizedBox(width: AppSpacing.sm),
                                Text(
                                  "Tell us about you",
                                  style: Theme.of(context).textTheme.titleLarge,
                                ),
                              ],
                            ),
                            const SizedBox(height: 6),
                            Text(
                              "YouthChain uses this to match you to jobs and build a clean CV.",
                              style: Theme.of(context).textTheme.bodyMedium,
                            ),
                            const SizedBox(height: AppSpacing.md),
                            TextFormField(
                              controller: _nameCtrl,
                              decoration: const InputDecoration(
                                labelText: "Full Name",
                                prefixIcon: Icon(Icons.badge_outlined),
                              ),
                              validator: (v) => (v == null || v.trim().isEmpty)
                                  ? "Required"
                                  : null,
                            ),
                            const SizedBox(height: AppSpacing.sm),
                            TextFormField(
                              controller: _emailCtrl,
                              decoration: const InputDecoration(
                                labelText: "Email",
                                prefixIcon: Icon(Icons.email_outlined),
                              ),
                              keyboardType: TextInputType.emailAddress,
                              validator: (v) => (v == null || v.trim().isEmpty)
                                  ? "Required"
                                  : null,
                            ),
                            const SizedBox(height: AppSpacing.sm),
                            TextFormField(
                              controller: _locationCtrl,
                              decoration: const InputDecoration(
                                labelText: "Location",
                                hintText: "e.g. Freetown",
                                prefixIcon: Icon(Icons.location_on_outlined),
                              ),
                            ),
                            const SizedBox(height: AppSpacing.sm),
                            TextFormField(
                              controller: _skillsCtrl,
                              decoration: const InputDecoration(
                                labelText: "Skills (comma-separated)",
                                hintText: "python, flutter, customer support",
                                prefixIcon: Icon(Icons.build_outlined),
                              ),
                            ),
                            const SizedBox(height: AppSpacing.sm),
                            TextFormField(
                              controller: _bioCtrl,
                              maxLines: 3,
                              decoration: const InputDecoration(
                                labelText: "Short bio",
                                hintText:
                                    "Tell employers what kind of work you want and your strengths.",
                                alignLabelWithHint: true,
                              ),
                            ),
                            const SizedBox(height: AppSpacing.md),
                            Text(
                              "Preferred Industries (optional)",
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                            const SizedBox(height: 4),
                            Text(
                              "Optional — jobs from these industries get a small "
                              "visibility boost in your matches.",
                              style: Theme.of(context).textTheme.bodyMedium,
                            ),
                            const SizedBox(height: AppSpacing.sm),
                            if (_industryOptions.isEmpty)
                              Text(
                                "No industries available right now.",
                                style: Theme.of(context).textTheme.bodySmall,
                              )
                            else
                              Wrap(
                                spacing: 6,
                                runSpacing: 6,
                                children: _industryOptions.map((industry) {
                                  final selected = _selectedIndustries.contains(
                                    industry,
                                  );
                                  return FilterChip(
                                    label: Text(industry),
                                    selected: selected,
                                    onSelected: (value) {
                                      setState(() {
                                        if (value) {
                                          _selectedIndustries.add(industry);
                                        } else {
                                          _selectedIndustries.remove(industry);
                                        }
                                      });
                                    },
                                  );
                                }).toList(),
                              ),
                            const SizedBox(height: AppSpacing.md),
                            SizedBox(
                              width: double.infinity,
                              height: 48,
                              child: ElevatedButton.icon(
                                icon: _saving
                                    ? const SizedBox(
                                        width: 16,
                                        height: 16,
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                          color: Colors.white,
                                        ),
                                      )
                                    : const Icon(Icons.save_outlined, size: 18),
                                label: Text(
                                  _saving ? "Saving..." : "Save Profile",
                                ),
                                onPressed: _saving ? null : _saveProfile,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: AppSpacing.md),
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(AppSpacing.md),
                      decoration: BoxDecoration(
                        color: context.colors.surface,
                        borderRadius: BorderRadius.circular(AppRadius.md),
                        border: Border.all(color: context.colors.outline),
                        boxShadow: cardShadow,
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Container(
                                width: 40,
                                height: 40,
                                decoration: BoxDecoration(
                                  color: context.colors.tertiaryLight,
                                  shape: BoxShape.circle,
                                ),
                                child: Icon(
                                  Icons.description_outlined,
                                  color: context.colors.tertiary,
                                  size: 20,
                                ),
                              ),
                              const SizedBox(width: AppSpacing.sm),
                              Text(
                                "CV Preview",
                                style: Theme.of(context).textTheme.titleLarge,
                              ),
                            ],
                          ),
                          const SizedBox(height: 6),
                          Text(
                            "YouthChain stitches your profile and verified certificates "
                            "into a clean CV you can share with employers.",
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                          const SizedBox(height: AppSpacing.md),
                          SizedBox(
                            width: double.infinity,
                            height: 46,
                            child: OutlinedButton.icon(
                              icon: _loadingCv
                                  ? const SizedBox(
                                      width: 16,
                                      height: 16,
                                      child: CircularProgressIndicator(
                                        strokeWidth: 2,
                                      ),
                                    )
                                  : const Icon(
                                      Icons.auto_awesome_outlined,
                                      size: 18,
                                    ),
                              label: Text(
                                _loadingCv ? "Generating..." : "Generate CV",
                              ),
                              onPressed: _loadingCv ? null : _generateCv,
                            ),
                          ),
                          if (_cvText != null) ...[
                            const SizedBox(height: AppSpacing.md),
                            Container(
                              width: double.infinity,
                              padding: const EdgeInsets.all(AppSpacing.sm),
                              decoration: BoxDecoration(
                                color: context.colors.background,
                                borderRadius: BorderRadius.circular(
                                  AppRadius.sm,
                                ),
                                border: Border.all(color: context.colors.outline),
                              ),
                              child: SelectableText(
                                _cvText!,
                                style: TextStyle(
                                  fontFamily: "monospace",
                                  fontSize: 13,
                                  color: context.colors.textPrimary,
                                ),
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ],
                ),
              ),
      ),
    );
  }
}
