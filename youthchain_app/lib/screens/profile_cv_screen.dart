import 'dart:convert';

import 'package:flutter/material.dart';

import '../services/api_client.dart';

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

  @override
  void initState() {
    super.initState();
    _loadExistingProfile();
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
            await ApiClient.instance.saveCandidateId((candidate['id'] as num).toInt());
          }
          _nameCtrl.text = (candidate['name'] ?? '').toString();
          _emailCtrl.text = (candidate['email'] ?? '').toString();
          _locationCtrl.text = (candidate['location'] ?? '').toString();
          _skillsCtrl.text = (candidate['skills'] ?? '').toString();
          _bioCtrl.text = (candidate['bio'] ?? '').toString();
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
      });

      if (!mounted) return;
      if (res.statusCode == 200 || res.statusCode == 201) {
        final body = json.decode(res.body);
        if (body is Map && body['candidate_id'] != null) {
          await ApiClient.instance.saveCandidateId((body['candidate_id'] as num).toInt());
        }
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text("✅ Profile saved & indexed for matching")),
        );
      } else {
        final body = json.decode(res.body);
        final msg = body is Map && body["error"] is String
        ? body["error"] as String
        : "Failed to save profile";
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(msg)),
        );
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
            content: Text("Save your profile first so we can build a CV from it."),
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
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(msg)),
        );
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
      backgroundColor: Colors.indigo[50],
      appBar: AppBar(
        title: const Text("My Profile & CV"),
        backgroundColor: Colors.indigo[700],
      ),
      body: SafeArea(
        child: _loadingProfile
            ? const Center(child: CircularProgressIndicator())
            : SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Column(
            children: [
              Card(
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(18),
                ),
                elevation: 6,
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Form(
                    key: _formKey,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          "Tell us about you",
                          style: TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: 8),
                        const Text(
                          "YouthChain uses this to match you to jobs and build a clean CV.",
                          style: TextStyle(fontSize: 13, color: Colors.black54),
                        ),
                        const SizedBox(height: 16),
                        TextFormField(
                          controller: _nameCtrl,
                          decoration: const InputDecoration(
                            labelText: "Full Name",
                            prefixIcon: Icon(Icons.person),
                          ),
                          validator: (v) =>
                          (v == null || v.trim().isEmpty) ? "Required" : null,
                        ),
                        const SizedBox(height: 12),
                        TextFormField(
                          controller: _emailCtrl,
                          decoration: const InputDecoration(
                            labelText: "Email",
                            prefixIcon: Icon(Icons.email),
                          ),
                          keyboardType: TextInputType.emailAddress,
                          validator: (v) =>
                          (v == null || v.trim().isEmpty) ? "Required" : null,
                        ),
                        const SizedBox(height: 12),
                        TextFormField(
                          controller: _locationCtrl,
                          decoration: const InputDecoration(
                            labelText: "Location",
                            hintText: "e.g. Freetown",
                            prefixIcon: Icon(Icons.location_on),
                          ),
                        ),
                        const SizedBox(height: 12),
                        TextFormField(
                          controller: _skillsCtrl,
                          decoration: const InputDecoration(
                            labelText: "Skills (comma-separated)",
                            hintText: "python, flutter, customer support",
                            prefixIcon: Icon(Icons.build),
                          ),
                        ),
                        const SizedBox(height: 12),
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
                        const SizedBox(height: 16),
                        SizedBox(
                          width: double.infinity,
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
                            : const Icon(Icons.save),
                            label: Text(_saving ? "Saving..." : "Save Profile"),
                            style: ElevatedButton.styleFrom(
                              backgroundColor: Colors.indigo[700],
                              minimumSize: const Size(double.infinity, 46),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(12),
                              ),
                            ),
                            onPressed: _saving ? null : _saveProfile,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              Card(
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(18),
                ),
                elevation: 6,
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        "CV Preview",
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 8),
                      const Text(
                        "YouthChain stitches your profile and verified certificates "
                        "into a clean CV you can share with employers.",
                        style: TextStyle(fontSize: 13, color: Colors.black54),
                      ),
                      const SizedBox(height: 12),
                      SizedBox(
                        width: double.infinity,
                        child: OutlinedButton.icon(
                          icon: _loadingCv
                          ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                          : const Icon(Icons.description),
                          label: Text(_loadingCv ? "Generating..." : "Generate CV"),
                          onPressed: _loadingCv ? null : _generateCv,
                        ),
                      ),
                      const SizedBox(height: 12),
                      if (_cvText != null)
                        Container(
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            color: Colors.grey[100],
                            borderRadius: BorderRadius.circular(12),
                            border: Border.all(color: Colors.grey.shade300),
                          ),
                          child: SelectableText(
                            _cvText!,
                            style: const TextStyle(
                              fontFamily: "monospace",
                              fontSize: 13,
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
