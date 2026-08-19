// Coverage for the real layout bug found via live testing: a plain
// Row([Expanded(Text(name)), Badge()]) lets a badge with real width
// (e.g. "Verified Business") claim its own space first, leaving the
// Expanded name almost nothing once that row also shares its outer Row
// with other fixed-size siblings (an icon box, a score badge, a save
// button) -- observed live collapsing a real employer name down to
// "Te...". NameWithBadge fixes this by giving the badge room to wrap to
// its own line instead of always sharing one line with the name.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:youthchain_app/widgets/name_with_badge.dart';

void main() {
  testWidgets('name and badge share one line when there is room for both', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 400,
            child: NameWithBadge(
              name: 'Acme SL',
              badge: SizedBox(width: 80, height: 20, key: const Key('badge')),
            ),
          ),
        ),
      ),
    );

    final nameTop = tester.getTopLeft(find.text('Acme SL')).dy;
    final badgeTop = tester.getTopLeft(find.byKey(const Key('badge'))).dy;
    expect(nameTop, badgeTop); // same row
  });

  testWidgets('badge wraps to its own line when the name needs the full width', (tester) async {
    // A long name in a narrow box, exactly the real-world squeeze: an
    // employer name sharing a card with other fixed-size siblings.
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 140,
            child: NameWithBadge(
              name: 'International Development Cooperation Network',
              badge: SizedBox(width: 80, height: 20, key: const Key('badge')),
            ),
          ),
        ),
      ),
    );

    final nameTop = tester.getTopLeft(find.text('International Development Cooperation Network')).dy;
    final badgeTop = tester.getTopLeft(find.byKey(const Key('badge'))).dy;
    // The badge dropped to a new line instead of squeezing the name --
    // this is the actual fix: previously (a plain Row) both were always
    // forced onto the same line no matter how little room was left.
    expect(badgeTop, greaterThan(nameTop));
  });

  testWidgets('the name text still receives the full available width, not zero', (tester) async {
    // The other half of the bug: even when there IS room, a Row's
    // Expanded could still end up with near-zero width if a sibling in
    // the OUTER row had already consumed most of the space before this
    // widget was ever reached. Confirms the Text's own render box is
    // sized against the real constraint, not squeezed to a sliver.
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 200,
            child: NameWithBadge(name: 'Test Employer Co'),
          ),
        ),
      ),
    );

    final size = tester.getSize(find.text('Test Employer Co'));
    expect(size.width, greaterThan(100)); // nowhere near the "Te..." collapse
  });

  testWidgets('renders with no badge at all', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: NameWithBadge(name: 'No Badge Here')),
      ),
    );

    expect(find.text('No Badge Here'), findsOneWidget);
  });
}
