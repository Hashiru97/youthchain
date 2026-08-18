import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import 'discover_screen.dart';
import 'job_screen.dart';

/// The app's first real tab navigation — previously JobScreen was the
/// sole post-login top-level screen, reached via AuthGate/the '/home'
/// route directly. Wraps it (unchanged) alongside the new DiscoverScreen
/// rather than rewriting JobScreen itself: JobScreen is already a
/// complete, self-contained Scaffold (its own AppBar and three docked
/// FloatingActionButtons, see job_screen.dart:1323+), and nested
/// Scaffolds inside an IndexedStack is a standard, working Flutter
/// pattern — each tab keeps its own AppBar/FAB, and IndexedStack keeps
/// both tabs' state alive when switching between them instead of
/// rebuilding from scratch every tap.
class HomeShell extends StatefulWidget {
  final int userId;

  const HomeShell({super.key, required this.userId});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(
        index: _tab,
        children: [
          JobScreen(userId: widget.userId),
          DiscoverScreen(userId: widget.userId),
        ],
      ),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _tab,
        onTap: (index) => setState(() => _tab = index),
        backgroundColor: context.colors.surface,
        selectedItemColor: context.colors.primary,
        unselectedItemColor: context.colors.textMuted,
        items: const [
          BottomNavigationBarItem(icon: Icon(Icons.work_outline_rounded), label: "Home"),
          BottomNavigationBarItem(icon: Icon(Icons.travel_explore_rounded), label: "Discover"),
        ],
      ),
    );
  }
}
