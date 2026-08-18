// Widget coverage for the Discover feed's detail screen -- deliberately
// its own screen, not a reuse of JobDetailScreen (see its own docstring
// for why: a scraped listing's "apply" action opens an external URL, not
// this platform's in-app CV-upload flow).

import 'package:flutter_test/flutter_test.dart';

import 'package:youthchain_app/screens/discover_job_detail_screen.dart';
import 'package:youthchain_app/theme/app_theme.dart';
import 'package:flutter/material.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  final fullJob = {
    "id": 10,
    "title": "Junior Developer",
    "location": "Freetown",
    "company_name": "Acme SL",
    "company_verified": true,
    "source_name": "Careers.sl",
    "salary": "Le 2,000,000/month",
    "employment_type": "Full-time",
    "description": "Build and maintain internal tools.",
    "apply_url": "https://careers.sl/jobs/123",
  };

  final minimalJob = {
    "id": 11,
    "title": "Mystery Listing",
    "location": null,
    "company_name": null,
    "company_verified": false,
    "source_name": null,
    "salary": null,
    "employment_type": null,
    "description": null,
    "apply_url": null,
  };

  testWidgets('renders every field when fully populated', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        home: DiscoverJobDetailScreen(job: fullJob),
      ),
    );

    expect(find.text('Junior Developer'), findsOneWidget);
    expect(find.text('Careers.sl'), findsOneWidget);
    expect(find.text('Freetown'), findsOneWidget);
    expect(find.text('Le 2,000,000/month'), findsOneWidget);
    expect(find.text('Full-time'), findsOneWidget);
    expect(find.text('Acme SL'), findsOneWidget);
    expect(find.text('Verified'), findsOneWidget);
    expect(find.text('Build and maintain internal tools.'), findsOneWidget);
    expect(find.text('Apply'), findsOneWidget);
  });

  testWidgets('degrades gracefully with only a title, no crash on null fields', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        home: DiscoverJobDetailScreen(job: minimalJob),
      ),
    );

    expect(find.text('Mystery Listing'), findsOneWidget);
    expect(find.text('Scraped listing'), findsOneWidget); // source_name fallback
    expect(find.text('Verified'), findsNothing);
    expect(find.text('Full-time'), findsNothing); // employment_type absent -- no row, no crash
    // No apply_url -- no Apply button, and no crash building the bottomNavigationBar.
    expect(find.text('Apply'), findsNothing);
  });

  testWidgets('shows no company card at all when company_name is absent', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        home: DiscoverJobDetailScreen(job: minimalJob),
      ),
    );
    expect(find.text('Company'), findsNothing);
  });

  testWidgets('shows the safety-notes banner for an unverified employer', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        home: DiscoverJobDetailScreen(job: minimalJob),
      ),
    );
    expect(find.text("This employer isn't verified — stay safe"), findsOneWidget);
    expect(find.textContaining("Never pay to apply"), findsOneWidget);
    expect(find.textContaining("meet in a public place"), findsOneWidget);
  });

  testWidgets('hides the safety-notes banner once the employer is verified', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        home: DiscoverJobDetailScreen(job: fullJob),
      ),
    );
    expect(find.text("This employer isn't verified — stay safe"), findsNothing);
  });

  testWidgets('shows an upcoming deadline as "Apply by ..."', (tester) async {
    final job = {...fullJob, "application_deadline": "2026-09-01", "is_expired": false};
    await tester.pumpWidget(
      MaterialApp(theme: AppTheme.light(), home: DiscoverJobDetailScreen(job: job)),
    );
    expect(find.text('Apply by 1 Sep 2026'), findsOneWidget);
    expect(find.text('Expired'), findsNothing);
  });

  testWidgets('shows a passed deadline as expired, with a flag not "Apply by"', (tester) async {
    final job = {...fullJob, "application_deadline": "2026-01-01", "is_expired": true};
    await tester.pumpWidget(
      MaterialApp(theme: AppTheme.light(), home: DiscoverJobDetailScreen(job: job)),
    );
    expect(find.text('Deadline was 1 Jan 2026'), findsOneWidget);
    expect(find.text('Expired'), findsOneWidget);
    expect(find.text('Apply by 1 Jan 2026'), findsNothing);
  });

  testWidgets('the report icon opens the listing-report sheet for this job', (tester) async {
    await tester.pumpWidget(
      MaterialApp(theme: AppTheme.light(), home: DiscoverJobDetailScreen(job: fullJob)),
    );
    await tester.tap(find.byIcon(Icons.flag_outlined));
    await tester.pumpAndSettle();
    expect(find.text('Report this listing'), findsOneWidget);
  });
}
