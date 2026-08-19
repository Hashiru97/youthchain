import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Persisted language preference (Krio-friendly copy) -- same shape as
/// ThemeController (see its own docstring for why a plain ValueNotifier,
/// not a ChangeNotifier+Provider setup, is the right amount of state
/// management for one rarely-changing, globally-read value). `null` means
/// "follow the device's own locale" -- Flutter's own locale resolution
/// already picks Krio when the device is set to it and falls back to
/// English otherwise (see AppLocalizations.supportedLocales in main.dart),
/// so there's no separate "system" enum value to track the way
/// ThemeMode.system already exists as a real enum member for theme.
class LocaleController {
  LocaleController._();

  static const _prefsKey = 'yc_locale'; // 'en' | 'kri' | absent (device default)

  static final ValueNotifier<Locale?> locale = ValueNotifier(null);

  /// Loads the persisted choice before the first frame, same "never flash
  /// the wrong one" reasoning as ThemeController.load().
  static Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(_prefsKey);
    locale.value = switch (saved) {
      'en' => const Locale('en'),
      'kri' => const Locale('kri'),
      _ => null,
    };
  }

  static Future<void> setLocale(Locale? value) async {
    locale.value = value;
    final prefs = await SharedPreferences.getInstance();
    if (value == null) {
      await prefs.remove(_prefsKey);
    } else {
      await prefs.setString(_prefsKey, value.languageCode);
    }
  }
}
