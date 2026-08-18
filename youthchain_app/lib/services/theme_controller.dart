import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Persisted light/dark preference (BL-49) -- the mobile counterpart to
/// the web portal's `localStorage["yc-theme"]` + `theme_init.js`. Unlike
/// the web version, there's no "OS preference, no explicit choice yet"
/// third state worth distinguishing here: Flutter's own `ThemeMode.system`
/// already covers that by tracking the platform brightness live, so the
/// three real states this app needs (light / dark / follow system) map
/// directly onto Flutter's own enum instead of reinventing one.
///
/// A plain ValueNotifier (not a full ChangeNotifier class+Provider setup)
/// is deliberate: this is the one piece of truly global, rarely-changing
/// state in the app, read by exactly one widget (the MaterialApp root via
/// ValueListenableBuilder in main.dart) -- reaching for a state management
/// package for a single on/off/system switch would be the abstraction
/// this codebase's own conventions elsewhere warn against.
class ThemeController {
  ThemeController._();

  static const _prefsKey = 'yc_theme_mode'; // 'light' | 'dark' | 'system'

  static final ValueNotifier<ThemeMode> mode = ValueNotifier(ThemeMode.system);

  /// Loads the persisted choice before the first frame -- called once from
  /// main() so the app never flashes the wrong theme and then swaps.
  static Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(_prefsKey);
    mode.value = switch (saved) {
      'light' => ThemeMode.light,
      'dark' => ThemeMode.dark,
      _ => ThemeMode.system,
    };
  }

  /// Cycles light -> dark -> light on tap. "System" is a real, reachable
  /// state (a fresh install starts there), but the in-app toggle only
  /// needs to offer the two the user can actually see change -- once
  /// someone has tapped it once, they've made an explicit choice, same as
  /// the web toggle's own `data-theme` override.
  static Future<void> toggle() async {
    final next = _resolvedIsDark() ? ThemeMode.light : ThemeMode.dark;
    mode.value = next;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKey, next == ThemeMode.dark ? 'dark' : 'light');
  }

  /// What's actually on screen right now, resolving ThemeMode.system
  /// against the real platform brightness -- used to decide which icon
  /// the toggle button shows.
  static bool _resolvedIsDark() {
    if (mode.value == ThemeMode.dark) return true;
    if (mode.value == ThemeMode.light) return false;
    final platformBrightness =
        WidgetsBinding.instance.platformDispatcher.platformBrightness;
    return platformBrightness == Brightness.dark;
  }

  static bool get isDark => _resolvedIsDark();
}
