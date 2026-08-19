import 'package:flutter/widgets.dart';

import 'app_localizations.dart';

// Re-exported so a screen only needs `import '../l10n/l10n_context.dart';`
// to get both the `context.l10n` shorthand below AND the AppLocalizations
// type itself (needed for typing a helper method's parameter, e.g. a
// screen's own `_buildStep(AppLocalizations l10n)`).
export 'app_localizations.dart';

/// `context.l10n.welcomeBack` instead of `AppLocalizations.of(context)!` at
/// every call site -- same shorthand-extension convention as
/// `context.colors` in theme/app_theme.dart (see AppColorsContext there).
extension AppLocalizationsContext on BuildContext {
  AppLocalizations get l10n => AppLocalizations.of(this);
}
