import 'package:flutter/material.dart';

import '../l10n/l10n_context.dart';
import '../services/locale_controller.dart';
import '../theme/app_theme.dart';

/// English/Krio picker, backed by LocaleController (see its own docstring
/// for why `null` means "follow the device"). The two language names are
/// deliberately shown in their own language regardless of which is
/// currently active -- a language switcher that only speaks one language
/// is the one place in the app where NOT localizing the label is correct,
/// same reasoning app store listings render every language's own name in
/// that language, not translated.
///
/// Shared (not private to one screen) so it can appear both inline on the
/// Profile & CV screen and in the overflow menu's quick-switch dialog --
/// user feedback was that burying language choice inside Profile & CV made
/// it hard to find in the moment a youth actually wanted to switch.
class LanguagePicker extends StatelessWidget {
  final bool showLabel;

  const LanguagePicker({super.key, this.showLabel = true});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<Locale?>(
      valueListenable: LocaleController.locale,
      builder: (context, current, _) {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            if (showLabel) ...[
              Text(
                context.l10n.languageSectionLabel,
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: AppSpacing.sm),
            ],
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                ChoiceChip(
                  label: const Text('English'),
                  selected: current?.languageCode == 'en',
                  onSelected: (_) => LocaleController.setLocale(const Locale('en')),
                ),
                ChoiceChip(
                  label: const Text('Krio'),
                  selected: current?.languageCode == 'kri',
                  onSelected: (_) => LocaleController.setLocale(const Locale('kri')),
                ),
                ChoiceChip(
                  label: Text(context.l10n.languageSystemDefault),
                  selected: current == null,
                  onSelected: (_) => LocaleController.setLocale(null),
                ),
              ],
            ),
          ],
        );
      },
    );
  }
}
