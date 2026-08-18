import 'package:flutter/material.dart';

/// YouthChain's design system — a deliberate palette, type scale, and
/// component theme, replacing the single-seed-color ColorScheme the app
/// started with. No custom font package is used on purpose: font files
/// fetched at runtime (as most Flutter font packages do by default) would
/// work against the offline-first design elsewhere in this app (BL-37) —
/// the polish here comes from restraint, consistent spacing, and a real
/// multi-hue system, not a downloaded typeface.
///
/// The three brand hues already existed informally, one per feature area
/// (green for jobs, purple for the credential passport, blue for
/// applications/messages) — this formalizes that into a real system so
/// every screen draws from the same named colors instead of ad hoc
/// `Colors.green[700]` scattered through each file.
///
/// Dark mode (BL-49): colors are no longer `static const` -- Dart can't
/// vary a compile-time constant at runtime, so a real dark theme (not
/// just a dark AppBar over an otherwise-light app) needs every screen's
/// color lookup to be theme-aware. This class is now a
/// `ThemeExtension<AppColors>`, registered on both AppTheme.light() and
/// AppTheme.dark(); screens read it via `context.colors.primary` (see the
/// AppColorsContext extension below) instead of `AppColors.primary`.
/// Every existing call site across the app was migrated to this shape —
/// see the individual screen files for the mechanical rename.
///
/// Brand accent colors (primary/primaryDark/secondary/secondaryDark/
/// tertiary/tertiaryDark/passport/passportDark/success/warning/error/
/// onChain) are deliberately IDENTICAL in both themes: they're already
/// dark/saturated enough to carry white text as a fill color (AppBar,
/// buttons, FABs) regardless of what's around them, and keeping them
/// fixed avoids re-litigating contrast on every one of those roles
/// individually. What genuinely changes for dark mode is the neutral
/// scaffold (background/surface/outline/text) and the "Light" tint
/// variants (primaryLight etc.), which go from pale washes to dark,
/// desaturated ones -- the same light-vs-dark split already proven out
/// on the web portal's dark theme.
@immutable
class AppColors extends ThemeExtension<AppColors> {
  const AppColors({
    required this.primary,
    required this.primaryDark,
    required this.primaryLight,
    required this.secondary,
    required this.secondaryDark,
    required this.secondaryLight,
    required this.tertiary,
    required this.tertiaryDark,
    required this.tertiaryLight,
    required this.passport,
    required this.passportDark,
    required this.passportLight,
    required this.surface,
    required this.background,
    required this.outline,
    required this.textPrimary,
    required this.textSecondary,
    required this.textMuted,
    required this.success,
    required this.successBg,
    required this.warning,
    required this.warningBg,
    required this.error,
    required this.errorBg,
    required this.onChain,
  });

  final Color primary;
  final Color primaryDark;
  final Color primaryLight;
  final Color secondary;
  final Color secondaryDark;
  final Color secondaryLight;
  final Color tertiary;
  final Color tertiaryDark;
  final Color tertiaryLight;
  final Color passport;
  final Color passportDark;
  final Color passportLight;
  final Color surface;
  final Color background;
  final Color outline;
  final Color textPrimary;
  final Color textSecondary;
  final Color textMuted;
  final Color success;
  final Color successBg;
  final Color warning;
  final Color warningBg;
  final Color error;
  final Color errorBg;
  final Color onChain;

  static const light = AppColors(
    // Brand: jobs, primary actions, growth/opportunity.
    primary: Color(0xFF0F7A5C),
    primaryDark: Color(0xFF0B5D46),
    primaryLight: Color(0xFFE3F5EE),
    // Credentials / Passport: trust, verification.
    secondary: Color(0xFF6D28D9),
    secondaryDark: Color(0xFF5320A8),
    secondaryLight: Color(0xFFF1EBFC),
    // Applications / Messages.
    tertiary: Color(0xFF1D63C9),
    tertiaryDark: Color(0xFF15499A),
    tertiaryLight: Color(0xFFE9F1FC),
    // Passport / verified credentials — a deep teal, deliberately NOT the
    // same purple as `secondary` (see the original docstring history in
    // git blame for the full reasoning: avoids clashing with Applications'
    // blue and with the pending-status amber).
    passport: Color(0xFF0E7C86),
    passportDark: Color(0xFF0A5E66),
    passportLight: Color(0xFFE1F2F3),
    // Neutrals.
    surface: Color(0xFFFFFFFF),
    background: Color(0xFFF6F8F9),
    outline: Color(0xFFE3E8E8),
    textPrimary: Color(0xFF15211E),
    textSecondary: Color(0xFF5B6B67),
    textMuted: Color(0xFF8B9895),
    // Semantic status — used consistently for application/verification state.
    success: Color(0xFF1E8E5A),
    successBg: Color(0xFFE6F6EE),
    warning: Color(0xFFB4740E),
    warningBg: Color(0xFFFCF3E3),
    error: Color(0xFFC22A2A),
    errorBg: Color(0xFFFBEAEA),
    onChain: Color(0xFF0F7A5C),
  );

  // Dark mode: neutrals invert (dark, faintly green-tinted background
  // rather than pure black -- consistent with the brand hue, same
  // reasoning as the web portal's dark palette) and every pale "Light"
  // wash becomes a dark, desaturated one instead. Brand/status accent
  // colors are unchanged from `light` -- see the class docstring.
  static const dark = AppColors(
    primary: Color(0xFF0F7A5C),
    primaryDark: Color(0xFF0B5D46),
    primaryLight: Color(0xFF163529),
    secondary: Color(0xFF6D28D9),
    secondaryDark: Color(0xFF5320A8),
    secondaryLight: Color(0xFF241A38),
    tertiary: Color(0xFF1D63C9),
    tertiaryDark: Color(0xFF15499A),
    tertiaryLight: Color(0xFF162A42),
    passport: Color(0xFF0E7C86),
    passportDark: Color(0xFF0A5E66),
    passportLight: Color(0xFF123230),
    surface: Color(0xFF17211D),
    background: Color(0xFF0E1613),
    outline: Color(0xFF2C3A35),
    textPrimary: Color(0xFFEDF3F1),
    textSecondary: Color(0xFFA9B8B3),
    textMuted: Color(0xFF748680),
    success: Color(0xFF1E8E5A),
    successBg: Color(0xFF15291F),
    warning: Color(0xFFB4740E),
    warningBg: Color(0xFF2E2412),
    error: Color(0xFFC22A2A),
    errorBg: Color(0xFF2E1917),
    onChain: Color(0xFF0F7A5C),
  );

  @override
  AppColors copyWith({
    Color? primary,
    Color? primaryDark,
    Color? primaryLight,
    Color? secondary,
    Color? secondaryDark,
    Color? secondaryLight,
    Color? tertiary,
    Color? tertiaryDark,
    Color? tertiaryLight,
    Color? passport,
    Color? passportDark,
    Color? passportLight,
    Color? surface,
    Color? background,
    Color? outline,
    Color? textPrimary,
    Color? textSecondary,
    Color? textMuted,
    Color? success,
    Color? successBg,
    Color? warning,
    Color? warningBg,
    Color? error,
    Color? errorBg,
    Color? onChain,
  }) {
    return AppColors(
      primary: primary ?? this.primary,
      primaryDark: primaryDark ?? this.primaryDark,
      primaryLight: primaryLight ?? this.primaryLight,
      secondary: secondary ?? this.secondary,
      secondaryDark: secondaryDark ?? this.secondaryDark,
      secondaryLight: secondaryLight ?? this.secondaryLight,
      tertiary: tertiary ?? this.tertiary,
      tertiaryDark: tertiaryDark ?? this.tertiaryDark,
      tertiaryLight: tertiaryLight ?? this.tertiaryLight,
      passport: passport ?? this.passport,
      passportDark: passportDark ?? this.passportDark,
      passportLight: passportLight ?? this.passportLight,
      surface: surface ?? this.surface,
      background: background ?? this.background,
      outline: outline ?? this.outline,
      textPrimary: textPrimary ?? this.textPrimary,
      textSecondary: textSecondary ?? this.textSecondary,
      textMuted: textMuted ?? this.textMuted,
      success: success ?? this.success,
      successBg: successBg ?? this.successBg,
      warning: warning ?? this.warning,
      warningBg: warningBg ?? this.warningBg,
      error: error ?? this.error,
      errorBg: errorBg ?? this.errorBg,
      onChain: onChain ?? this.onChain,
    );
  }

  @override
  AppColors lerp(ThemeExtension<AppColors>? other, double t) {
    // Theme transitions in this app are an instant toggle, not an
    // animated crossfade -- a real per-field Color.lerp would work too,
    // but isn't worth the verbosity for a switch nobody watches animate.
    if (other is! AppColors) return this;
    return t < 0.5 ? this : other;
  }
}

/// `context.colors.primary` instead of `Theme.of(context).extension<AppColors>()!.primary`
/// at every one of the ~230 call sites across the app.
extension AppColorsContext on BuildContext {
  AppColors get colors => Theme.of(this).extension<AppColors>()!;
}

class AppRadius {
  AppRadius._();
  static const sm = 10.0;
  static const md = 16.0;
  static const lg = 22.0;
  static const pill = 999.0;
}

class AppSpacing {
  AppSpacing._();
  static const xs = 4.0;
  static const sm = 8.0;
  static const md = 16.0;
  static const lg = 24.0;
  static const xl = 32.0;
}

class AppTheme {
  AppTheme._();

  static ThemeData light() => _build(AppColors.light, Brightness.light);

  static ThemeData dark() => _build(AppColors.dark, Brightness.dark);

  static ThemeData _build(AppColors colors, Brightness brightness) {
    final colorScheme = ColorScheme.fromSeed(
      seedColor: colors.primary,
      brightness: brightness,
      primary: colors.primary,
      secondary: colors.secondary,
      tertiary: colors.tertiary,
      surface: colors.surface,
      error: colors.error,
    );

    final base = ThemeData(useMaterial3: true, colorScheme: colorScheme, brightness: brightness);

    return base.copyWith(
      extensions: [colors],
      scaffoldBackgroundColor: colors.background,
      textTheme: _textTheme(base.textTheme, colors),
      appBarTheme: AppBarTheme(
        elevation: 0,
        scrolledUnderElevation: 1,
        // Leading-aligned, not centered (BL-50) -- Material 3's own
        // default on most platforms, and what a two-line brand lockup
        // (name + slogan, see JobScreen's app bar) needs to read
        // correctly rather than looking centered-by-accident.
        centerTitle: false,
        backgroundColor: colors.primary,
        foregroundColor: Colors.white,
        titleTextStyle: const TextStyle(
          color: Colors.white,
          fontSize: 18,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.1,
        ),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        color: colors.surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.md),
          side: BorderSide(color: colors.outline, width: 1),
        ),
        margin: EdgeInsets.zero,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: colors.background,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: 14,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppRadius.sm),
          borderSide: BorderSide(color: colors.outline),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppRadius.sm),
          borderSide: BorderSide(color: colors.outline),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppRadius.sm),
          borderSide: BorderSide(color: colors.primary, width: 1.6),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppRadius.sm),
          borderSide: BorderSide(color: colors.error),
        ),
        labelStyle: TextStyle(color: colors.textSecondary),
        hintStyle: TextStyle(color: colors.textMuted),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: colors.primary,
          foregroundColor: Colors.white,
          disabledBackgroundColor: colors.outline,
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.lg,
            vertical: 14,
          ),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.sm),
          ),
          textStyle: const TextStyle(
            fontWeight: FontWeight.w700,
            fontSize: 15,
            letterSpacing: 0.1,
          ),
          elevation: 0,
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: colors.primary,
          side: BorderSide(color: colors.outline),
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.lg,
            vertical: 14,
          ),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.sm),
          ),
          textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: colors.primary,
          textStyle: const TextStyle(fontWeight: FontWeight.w600),
        ),
      ),
      chipTheme: base.chipTheme.copyWith(
        backgroundColor: colors.background,
        side: BorderSide(color: colors.outline),
        labelStyle: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w600,
          color: colors.textSecondary,
        ),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.pill),
        ),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      ),
      // Real gap found live: every FAB in this app (My Applications,
      // Passport, Work History on JobScreen) sets its own dark, saturated
      // backgroundColor but never a foregroundColor, so the label/icon
      // fell back to Material 3's default FAB foreground -- not white,
      // and low-contrast against these specific colors. Fixed once here
      // rather than on each FAB, since every current and future FAB in
      // this app uses a custom dark backgroundColor the same way.
      floatingActionButtonTheme: const FloatingActionButtonThemeData(
        elevation: 2,
        highlightElevation: 4,
        foregroundColor: Colors.white,
      ),
      dividerTheme: DividerThemeData(
        color: colors.outline,
        thickness: 1,
        space: 1,
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: colors.textPrimary,
        contentTextStyle: TextStyle(color: colors.surface, fontSize: 14),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.sm),
        ),
      ),
      listTileTheme: ListTileThemeData(
        iconColor: colors.textSecondary,
        textColor: colors.textPrimary,
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(
        color: colors.primary,
      ),
    );
  }

  static TextTheme _textTheme(TextTheme base, AppColors colors) {
    return base
        .copyWith(
          displaySmall: TextStyle(
            fontSize: 30,
            fontWeight: FontWeight.w800,
            letterSpacing: -0.5,
            color: colors.textPrimary,
            height: 1.15,
          ),
          headlineSmall: TextStyle(
            fontSize: 22,
            fontWeight: FontWeight.w800,
            letterSpacing: -0.3,
            color: colors.textPrimary,
            height: 1.2,
          ),
          titleLarge: TextStyle(
            fontSize: 18,
            fontWeight: FontWeight.w700,
            color: colors.textPrimary,
            height: 1.3,
          ),
          titleMedium: TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.w700,
            color: colors.textPrimary,
          ),
          bodyLarge: TextStyle(
            fontSize: 15,
            color: colors.textPrimary,
            height: 1.45,
          ),
          bodyMedium: TextStyle(
            fontSize: 13.5,
            color: colors.textSecondary,
            height: 1.4,
          ),
          bodySmall: TextStyle(fontSize: 12, color: colors.textMuted),
          labelLarge: const TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.1,
          ),
        )
        .apply(fontFamily: base.bodyMedium?.fontFamily);
  }
}

/// A soft, layered shadow used in place of Material's default elevation —
/// deliberately subtle (premium UI tends toward restraint, not heavy drop
/// shadows). Kept theme-independent (a black shadow reads correctly on
/// both a light and a dark surface at this low an opacity), unlike the
/// AppColors palette above.
List<BoxShadow> get cardShadow => [
  BoxShadow(
    color: Colors.black.withValues(alpha: 0.04),
    blurRadius: 12,
    offset: const Offset(0, 4),
  ),
];
