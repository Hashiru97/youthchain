import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'app_localizations.dart';

/// The one, single localizationsDelegates list every MaterialApp in this
/// codebase must use -- production (main.dart) AND every widget test that
/// pumps its own MaterialApp. This is deliberately the ONLY place this
/// list is written out: the Krio crash this file exists to fix (see the
/// three delegates below) was invisible to the entire test suite for
/// exactly the reason a second, simpler, hand-copied list existed in
/// test files (AppLocalizations.localizationsDelegates, missing the Kri
/// fallbacks) -- every test happened to render at the default 'en'
/// locale, so nothing ever actually exercised 'kri' against a
/// BottomNavigationBar or similar Material-localization-requiring widget
/// until a real device caught it. One shared constant makes that
/// specific class of drift structurally impossible going forward.
const List<LocalizationsDelegate<dynamic>> appLocalizationsDelegates = [
  AppLocalizations.delegate,
  KriMaterialLocalizationsFallback(),
  KriWidgetsLocalizationsFallback(),
  KriCupertinoLocalizationsFallback(),
  GlobalMaterialLocalizations.delegate,
  GlobalWidgetsLocalizations.delegate,
  GlobalCupertinoLocalizations.delegate,
];

// Flutter's own Material/Widgets/Cupertino chrome strings ("OK", "Cancel",
// a RefreshIndicator's semantic label, BottomNavigationBar's internals,
// etc.) ship with translations for a large but FIXED set of locales --
// Krio isn't one of them, unlike our own AppLocalizations (app_kri.arb),
// which we wrote ourselves. Setting MaterialApp.locale directly to
// Locale('kri') (see main.dart) skips Flutter's usual "fall back to a
// supported locale" resolution entirely -- it forces exactly that locale
// on every delegate, and GlobalMaterialLocalizations.delegate has nothing
// to return for it. Confirmed on a real device, not theoretical: choosing
// Krio crashed instantly with "No MaterialLocalizations found" the moment
// a BottomNavigationBar (HomeShell's bottom nav) tried to look one up.
//
// Fix: three tiny delegates that claim support ONLY for 'kri' and, when
// asked to load it, hand back the real English Material/Widgets/Cupertino
// localizations instead. Placed BEFORE the Global*Delegate entries in
// main.dart's localizationsDelegates list -- Localizations resolves each
// type to the first delegate in the list whose isSupported() returns true,
// so these only ever intercept 'kri' and never touch 'en' or any locale
// Flutter genuinely does support.
class KriMaterialLocalizationsFallback extends LocalizationsDelegate<MaterialLocalizations> {
  const KriMaterialLocalizationsFallback();
  @override
  bool isSupported(Locale locale) => locale.languageCode == 'kri';
  @override
  Future<MaterialLocalizations> load(Locale locale) =>
      GlobalMaterialLocalizations.delegate.load(const Locale('en'));
  @override
  bool shouldReload(KriMaterialLocalizationsFallback old) => false;
}

class KriWidgetsLocalizationsFallback extends LocalizationsDelegate<WidgetsLocalizations> {
  const KriWidgetsLocalizationsFallback();
  @override
  bool isSupported(Locale locale) => locale.languageCode == 'kri';
  @override
  Future<WidgetsLocalizations> load(Locale locale) =>
      GlobalWidgetsLocalizations.delegate.load(const Locale('en'));
  @override
  bool shouldReload(KriWidgetsLocalizationsFallback old) => false;
}

class KriCupertinoLocalizationsFallback extends LocalizationsDelegate<CupertinoLocalizations> {
  const KriCupertinoLocalizationsFallback();
  @override
  bool isSupported(Locale locale) => locale.languageCode == 'kri';
  @override
  Future<CupertinoLocalizations> load(Locale locale) =>
      GlobalCupertinoLocalizations.delegate.load(const Locale('en'));
  @override
  bool shouldReload(KriCupertinoLocalizationsFallback old) => false;
}
