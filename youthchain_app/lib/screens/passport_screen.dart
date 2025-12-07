import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

class PassportScreen extends StatefulWidget {
  final int userId;
  const PassportScreen({super.key, required this.userId});

  @override
  PassportScreenState createState() => PassportScreenState();
}

class PassportScreenState extends State<PassportScreen> {
  List credentials = [];
  bool isLoading = true;
  static const _base = 'http://127.0.0.1:5000';

  @override
  void initState() {
    super.initState();
    fetchCredentials();
  }

  Future<void> fetchCredentials() async {
    setState(() => isLoading = true);
    try {
      final res = await http.get(Uri.parse("$_base/passport/${widget.userId}"));
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
    final uri = Uri.parse("$_base/verify/$credId");
    final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!ok && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Could not open verification page for $title")),
      );
    }
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
                                style: const TextStyle(color: Colors.white),
                              ),
                              backgroundColor: Colors.purple[700],
                            ),
                            const SizedBox(height: 4),
                            if (onChain)
                              Chip(
                                label: const Text(
                                  "On-chain",
                                  style: TextStyle(color: Colors.white, fontSize: 12),
                                ),
                                backgroundColor: Colors.green[700],
                              ),
                          ],
                        ),
                      ],
                    ),

              ),
            );
          },
        ),
      ),
    );
  }
}
