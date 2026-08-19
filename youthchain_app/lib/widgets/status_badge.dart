import 'package:flutter/material.dart';

import '../l10n/l10n_context.dart';
import '../theme/app_theme.dart';

/// A single, consistent pill-badge component for every status shown across
/// the app (application status, on-chain verification, unread markers) —
/// previously each screen invented its own ad hoc color-switch/Container.
///
/// Every factory now takes `BuildContext` as its first argument (BL-49) —
/// these colors have to come from `context.colors` for dark mode to reach
/// them, and a factory constructor has no `context` of its own the way a
/// widget's `build` method does.
class StatusBadge extends StatelessWidget {
  final String label;
  final Color color;
  final Color background;
  final IconData? icon;
  final bool dense;

  const StatusBadge({
    super.key,
    required this.label,
    required this.color,
    required this.background,
    this.icon,
    this.dense = false,
  });

  factory StatusBadge.applicationStatus(BuildContext context, String status) {
    final s = status.toLowerCase();
    final colors = context.colors;
    if (s == 'accepted') {
      return StatusBadge(
        label: status,
        color: colors.success,
        background: colors.successBg,
        icon: Icons.check_circle_rounded,
      );
    }
    if (s == 'rejected') {
      return StatusBadge(
        label: status,
        color: colors.error,
        background: colors.errorBg,
        icon: Icons.cancel_rounded,
      );
    }
    // Gig/hire-based jobs only (see Job.job_type in app.py) — the formal
    // job lifecycle never reaches this status.
    if (s == 'completed') {
      return StatusBadge(
        label: status,
        color: colors.secondary,
        background: colors.secondaryLight,
        icon: Icons.task_alt_rounded,
      );
    }
    return StatusBadge(
      label: status,
      color: colors.warning,
      background: colors.warningBg,
      icon: Icons.hourglass_top_rounded,
    );
  }

  /// "Formal" vs "Gig / hire-based" (Job.job_type) shown on a job card —
  /// reuses `secondary` (see app_theme.dart's note on that hue being
  /// otherwise unused since Passport moved to its own teal), keeping the
  /// gig-work identity visually distinct from every other feature area.
  factory StatusBadge.jobType(BuildContext context, String jobType) {
    final colors = context.colors;
    if (jobType == 'gig') {
      return StatusBadge(
        label: context.l10n.jobTypeGig,
        color: colors.secondary,
        background: colors.secondaryLight,
        icon: Icons.handshake_rounded,
        dense: true,
      );
    }
    return StatusBadge(
      label: context.l10n.jobTypeFormal,
      color: colors.textSecondary,
      background: colors.background,
      icon: Icons.description_rounded,
      dense: true,
    );
  }

  /// [revoked] real gap found via live end-to-end testing of the
  /// credential flow on a physical device: this factory only ever
  /// distinguished verified/pending, never revoked -- a credential an
  /// admin had revoked (Credential.revoked_at set, see
  /// admin_revoke_credential/YouthChainRegistry.sol's revokeCredential())
  /// still showed as a trustworthy green "On-chain" badge on the mobile
  /// Passport screen, reproduced live, not hypothetical. Takes priority
  /// over [verified] since a hash can be both registered AND revoked at
  /// once (that's exactly what revocation means -- see isValid() vs
  /// isRegistered() in the contract).
  factory StatusBadge.onChain(
    BuildContext context,
    bool verified, {
    bool revoked = false,
  }) {
    final colors = context.colors;
    if (revoked) {
      return StatusBadge(
        label: context.l10n.credentialRevoked,
        color: colors.error,
        background: colors.errorBg,
        icon: Icons.gpp_bad_rounded,
      );
    }
    return verified
        ? StatusBadge(
            label: context.l10n.credentialOnChain,
            color: colors.onChain,
            background: colors.primaryLight,
            icon: Icons.verified_rounded,
          )
        : StatusBadge(
            label: context.l10n.credentialPending,
            color: colors.warning,
            background: colors.warningBg,
            icon: Icons.schedule_rounded,
          );
  }

  /// Employer verification status (see Employer.verification_status in
  /// app.py) shown as a normal colored pill — for contexts with a plain
  /// background. The messages screen uses its own transparent/white
  /// variant instead since it sits over a colored AppBar; this one is for
  /// everywhere else (e.g. the job detail screen).
  ///
  /// [type] is Employer.verification_type ("business" | "individual") --
  /// real gap found and closed after this first shipped: an individual
  /// hiring informally (no business to register) has no path to a
  /// business-document verification, so once verified they get a
  /// deliberately distinct "Verified Individual" label, not the same
  /// "Verified" badge shown to a registered business — a youth deciding
  /// whether to work for someone deserves to know which one they're
  /// looking at.
  factory StatusBadge.employerVerification(
    BuildContext context,
    String status, {
    String? type,
    bool dense = false,
  }) {
    final colors = context.colors;
    final l10n = context.l10n;
    switch (status) {
      case 'verified':
        return StatusBadge(
          label: type == 'individual'
              ? l10n.employerVerifiedIndividual
              : l10n.employerVerifiedBusiness,
          color: colors.success,
          background: colors.successBg,
          icon: Icons.verified_rounded,
          dense: dense,
        );
      case 'pending':
        return StatusBadge(
          label: l10n.employerPendingReview,
          color: colors.warning,
          background: colors.warningBg,
          icon: Icons.schedule_rounded,
          dense: dense,
        );
      case 'rejected':
        return StatusBadge(
          label: l10n.employerRejected,
          color: colors.error,
          background: colors.errorBg,
          icon: Icons.cancel_rounded,
          dense: dense,
        );
      default:
        return StatusBadge(
          label: l10n.employerUnverified,
          color: colors.textMuted,
          background: colors.background,
          icon: Icons.help_outline_rounded,
          dense: dense,
        );
    }
  }

  /// Scam-signal caution badge for a scraped Discover listing (see
  /// Job.scam_signals / scanner.scam_signals in the backend — fee
  /// requests, a personal-email-only contact, or vague/missing pay).
  /// Deliberately the same error (red) color as StatusBadge.onChain's
  /// [revoked] case, not warning (amber/yellow) — this is a real safety
  /// caution about a listing that may be trying to extract money or
  /// personal contact from a job seeker, not a routine "pending" status.
  factory StatusBadge.scamWarning(BuildContext context) {
    final colors = context.colors;
    return StatusBadge(
      label: context.l10n.scamWarningBadge,
      color: colors.error,
      background: colors.errorBg,
      icon: Icons.warning_amber_rounded,
      dense: true,
    );
  }

  /// Employer.trust_tier (see _employer_trust_summary in app.py) -- a
  /// composite 0-100 score blending verification, open trust-&-safety
  /// reports, ratings from past workers, and whether the employer
  /// actually responds to applicants. Deliberately shown regardless of
  /// whether ratings exist yet, unlike StatusBadge.employerVerification's
  /// neighboring rating-count row -- a brand-new employer with zero
  /// ratings previously showed NO trust signal at all beyond the
  /// verification badge; the composite score is meaningful (a neutral
  /// "fair") even at that cold start, so hiding it the same way the
  /// ratings-only row still correctly does would throw away real signal.
  factory StatusBadge.employerTrust(BuildContext context, String tier) {
    final colors = context.colors;
    final l10n = context.l10n;
    switch (tier) {
      case 'good':
        return StatusBadge(
          label: l10n.employerTrustGood,
          color: colors.success,
          background: colors.successBg,
          icon: Icons.shield_rounded,
          dense: true,
        );
      case 'caution':
        return StatusBadge(
          label: l10n.employerTrustCaution,
          color: colors.error,
          background: colors.errorBg,
          icon: Icons.gpp_maybe_rounded,
          dense: true,
        );
      default:
        return StatusBadge(
          label: l10n.employerTrustFair,
          color: colors.warning,
          background: colors.warningBg,
          icon: Icons.shield_outlined,
          dense: true,
        );
    }
  }

  factory StatusBadge.matchScore(BuildContext context, int score) {
    final colors = context.colors;
    final color = score >= 70
        ? colors.success
        : (score >= 40 ? colors.warning : colors.textMuted);
    final bg = score >= 70
        ? colors.successBg
        : (score >= 40 ? colors.warningBg : colors.background);
    return StatusBadge(label: context.l10n.matchScoreLabel(score), color: color, background: bg);
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: dense ? 8 : 10,
        vertical: dense ? 3 : 5,
      ),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(AppRadius.pill),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: dense ? 12 : 14, color: color),
            const SizedBox(width: 4),
          ],
          Text(
            label,
            style: TextStyle(
              color: color,
              fontSize: dense ? 11 : 12,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}
