import 'package:flutter/material.dart';

/// A name/title that shares its row with a fixed-size badge, truncating
/// gracefully instead of being crushed by the badge's own width.
///
/// Real bug found via live testing on JobScreen's employer-name row: a
/// plain `Row([Expanded(Text(...)), Badge()])` gives the badge whatever
/// width it needs first, and the Expanded name gets only what's left —
/// fine on its own, but that row also shared its outer Row with an icon
/// box, a match-score badge, and a save button, so almost nothing was
/// left for the name at all. A real employer name collapsed down to
/// "Te...". This widget fixes that by giving the name a real *bounded*
/// width to ellipsize within (via [LayoutBuilder], which must wrap the
/// [Wrap] rather than sit inside one of its children — a Wrap child gets
/// unbounded constraints, so [TextOverflow.ellipsis] would never
/// actually trigger there), then lets the badge flow to its own line
/// whenever the name needs that whole width, instead of both being
/// forced onto one line and crushing the name.
class NameWithBadge extends StatelessWidget {
  final String name;
  final TextStyle? style;
  final Widget? badge;
  final double spacing;

  const NameWithBadge({
    super.key,
    required this.name,
    this.style,
    this.badge,
    this.spacing = 6,
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) => Wrap(
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: spacing,
        runSpacing: 2,
        children: [
          ConstrainedBox(
            constraints: BoxConstraints(maxWidth: constraints.maxWidth),
            child: Text(name, style: style, overflow: TextOverflow.ellipsis),
          ),
          if (badge != null) badge!,
        ],
      ),
    );
  }
}
