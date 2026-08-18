import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// A single, consistent "nothing here yet" component — previously every
/// screen hand-rolled its own bare centered Text widget.
class EmptyState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String? subtitle;
  final Widget? action;

  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    this.subtitle,
    this.action,
  });

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 72,
              height: 72,
              decoration: BoxDecoration(
                color: context.colors.background,
                shape: BoxShape.circle,
                border: Border.all(color: context.colors.outline),
              ),
              child: Icon(icon, size: 32, color: context.colors.textMuted),
            ),
            const SizedBox(height: AppSpacing.md),
            Text(
              title,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.titleMedium,
            ),
            if (subtitle != null) ...[
              const SizedBox(height: 6),
              Text(
                subtitle!,
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ],
            if (action != null) ...[
              const SizedBox(height: AppSpacing.md),
              action!,
            ],
          ],
        ),
      ),
    );
  }
}

/// A subtle shimmering placeholder used instead of a bare spinner while
/// content loads — a small but real signal of polish over the default
/// CircularProgressIndicator-everywhere approach.
class ShimmerBox extends StatefulWidget {
  final double height;
  final double? width;
  final BorderRadius? borderRadius;

  const ShimmerBox({
    super.key,
    required this.height,
    this.width,
    this.borderRadius,
  });

  @override
  State<ShimmerBox> createState() => _ShimmerBoxState();
}

class _ShimmerBoxState extends State<ShimmerBox>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final t = _controller.value;
        return Container(
          height: widget.height,
          width: widget.width,
          decoration: BoxDecoration(
            borderRadius: widget.borderRadius ?? BorderRadius.circular(AppRadius.sm),
            gradient: LinearGradient(
              begin: Alignment(-1 + 2 * t, 0),
              end: Alignment(1 + 2 * t, 0),
              colors: [
                context.colors.outline,
                // The moving highlight must read as lighter than the base
                // in both themes -- lerping toward white (not a fixed
                // literal color) keeps that true regardless of how dark
                // `outline` itself is in the active theme.
                Color.lerp(context.colors.outline, Colors.white, 0.5)!,
                context.colors.outline,
              ],
              stops: const [0.25, 0.5, 0.75],
            ),
          ),
        );
      },
    );
  }
}

/// A card-shaped skeleton loader, matching the job/credential/application
/// card footprint, so a loading list looks like "content about to appear"
/// rather than a blank screen with a spinner in the middle.
class SkeletonListView extends StatelessWidget {
  final int itemCount;
  const SkeletonListView({super.key, this.itemCount = 4});

  @override
  Widget build(BuildContext context) {
    return ListView.builder(
      padding: const EdgeInsets.all(AppSpacing.md),
      itemCount: itemCount,
      itemBuilder: (context, index) => Padding(
        padding: const EdgeInsets.only(bottom: AppSpacing.md),
        child: Container(
          padding: const EdgeInsets.all(AppSpacing.md),
          decoration: BoxDecoration(
            color: context.colors.surface,
            borderRadius: BorderRadius.circular(AppRadius.md),
            border: Border.all(color: context.colors.outline),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const ShimmerBox(height: 16, width: 180),
              const SizedBox(height: 10),
              const ShimmerBox(height: 12, width: 120),
              const SizedBox(height: 14),
              ShimmerBox(
                height: 36,
                width: double.infinity,
                borderRadius: BorderRadius.circular(AppRadius.sm),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
