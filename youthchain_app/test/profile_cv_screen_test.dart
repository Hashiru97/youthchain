// Real widget coverage for the "Preferred Industries" section added
// alongside the employer-industry/matching-bonus work on the backend.
// Exercises both GET /api/industries (chip options) and GET/POST
// /api/candidate (existing preference pre-selection, and what actually
// gets submitted) via ApiClient.testClient.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:youthchain_app/l10n/app_localizations.dart';
import 'package:youthchain_app/l10n/kri_material_fallback.dart';
import 'package:youthchain_app/screens/profile_cv_screen.dart';
import 'package:youthchain_app/services/api_client.dart';
import 'package:youthchain_app/theme/app_theme.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const secureChannel = MethodChannel(
    'plugins.it_nomads.com/flutter_secure_storage',
  );
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(secureChannel, (call) async => null);

  setUp(() {
    ApiClient.baseUrl = 'http://127.0.0.1:5000';
    ApiClient.testClient = null;
  });

  tearDown(() {
    ApiClient.testClient = null;
  });

  // The "Tell us about you" card grew two SwitchListTiles (job alerts, SMS
  // alerts) below its fields -- the default 800x600 test surface no longer
  // has room for "Save Profile" to land on-screen after ensureVisible's
  // scroll, which made tap() flaky. Tests that interact with that card
  // call this first instead of fighting scroll-position math.
  Future<void> useTallSurface(WidgetTester tester) async {
    await tester.binding.setSurfaceSize(const Size(800, 1400));
    addTearDown(() => tester.binding.setSurfaceSize(null));
  }

  http.Response route(
    http.Request request, {
    required Map<String, dynamic> candidate,
  }) {
    if (request.url.path == '/api/industries') {
      return http.Response(
        jsonEncode({
          "success": true,
          "industries": ["Agriculture", "Fisheries", "Healthcare"],
        }),
        200,
      );
    }
    if (request.url.path == '/api/candidate/me') {
      return http.Response(
        jsonEncode({"success": true, "candidate": candidate}),
        200,
      );
    }
    return http.Response(
      jsonEncode({"success": false, "error": "not found"}),
      404,
    );
  }

  testWidgets('renders an industry chip per option fetched from the backend', (
    tester,
  ) async {
    ApiClient.testClient = MockClient(
      (request) async => route(request, candidate: {}),
    );

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: ProfileCvScreen(userId: 1),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Preferred Industries (optional)'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Agriculture'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Fisheries'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Healthcare'), findsOneWidget);
  });

  testWidgets(
    'pre-selects chips from the candidate\'s existing preferred_industries',
    (tester) async {
      ApiClient.testClient = MockClient(
        (request) async => route(
          request,
          candidate: {
            "name": "Zeus",
            "email": "zeus@test.com",
            "preferred_industries": "Agriculture,Fisheries",
          },
        ),
      );

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: ProfileCvScreen(userId: 1),
        ),
      );
      await tester.pumpAndSettle();

      final agriculture = tester.widget<FilterChip>(
        find.widgetWithText(FilterChip, 'Agriculture'),
      );
      final fisheries = tester.widget<FilterChip>(
        find.widgetWithText(FilterChip, 'Fisheries'),
      );
      final healthcare = tester.widget<FilterChip>(
        find.widgetWithText(FilterChip, 'Healthcare'),
      );
      expect(agriculture.selected, isTrue);
      expect(fisheries.selected, isTrue);
      expect(healthcare.selected, isFalse);
    },
  );

  testWidgets('tapping a chip toggles its selection', (tester) async {
    ApiClient.testClient = MockClient(
      (request) async => route(request, candidate: {}),
    );

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: ProfileCvScreen(userId: 1),
      ),
    );
    await tester.pumpAndSettle();

    expect(
      tester
          .widget<FilterChip>(find.widgetWithText(FilterChip, 'Healthcare'))
          .selected,
      isFalse,
    );

    await tester.ensureVisible(find.widgetWithText(FilterChip, 'Healthcare'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilterChip, 'Healthcare'));
    await tester.pump();

    expect(
      tester
          .widget<FilterChip>(find.widgetWithText(FilterChip, 'Healthcare'))
          .selected,
      isTrue,
    );
  });

  testWidgets(
    'saving the profile submits exactly the currently-selected industries',
    (tester) async {
      http.Request? capturedSave;
      ApiClient.testClient = MockClient((request) async {
        if (request.method == 'POST' && request.url.path == '/api/candidate') {
          capturedSave = request;
          return http.Response(
            jsonEncode({"success": true, "candidate_id": 7}),
            200,
          );
        }
        return route(request, candidate: {"preferred_industries": "Fisheries"});
      });

      await useTallSurface(tester);
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: ProfileCvScreen(userId: 1),
        ),
      );
      await tester.pumpAndSettle();

      // Starts pre-selected with Fisheries only; add Healthcare, remove
      // nothing -- the save must reflect exactly {Fisheries, Healthcare}.
      await tester.ensureVisible(find.widgetWithText(FilterChip, 'Healthcare'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilterChip, 'Healthcare'));
      await tester.pump();

      // Name and email are both required by the form's validators -- leaving
      // either blank makes validate() fail silently and the save never even
      // reaches postJson, which is exactly what happened before this fix.
      await tester.enterText(find.byType(TextField).at(0), 'Zeus');
      await tester.enterText(find.byType(TextField).at(1), 'zeus@test.com');
      await tester.ensureVisible(find.text('Save Profile'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save Profile'));
      await tester.pumpAndSettle();

      expect(capturedSave, isNotNull);
      final body = jsonDecode(capturedSave!.body) as Map;
      final submitted = Set<String>.from(body['preferred_industries'] as List);
      expect(submitted, {'Fisheries', 'Healthcare'});
    },
  );

  testWidgets(
    'pre-selects the job alerts switch from the candidate\'s existing job_alerts_enabled',
    (tester) async {
      ApiClient.testClient = MockClient(
        (request) async => route(
          request,
          candidate: {
            "name": "Zeus",
            "email": "zeus@test.com",
            "job_alerts_enabled": true,
          },
        ),
      );

      await useTallSurface(tester);
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: ProfileCvScreen(userId: 1),
        ),
      );
      await tester.pumpAndSettle();

      expect(
        tester
            .widget<SwitchListTile>(
              find.widgetWithText(SwitchListTile, 'Job alerts'),
            )
            .value,
        isTrue,
      );
    },
  );

  testWidgets(
    'saving the profile submits the current job alerts toggle state',
    (tester) async {
      http.Request? capturedSave;
      ApiClient.testClient = MockClient((request) async {
        if (request.method == 'POST' && request.url.path == '/api/candidate') {
          capturedSave = request;
          return http.Response(
            jsonEncode({"success": true, "candidate_id": 7}),
            200,
          );
        }
        return route(request, candidate: {"job_alerts_enabled": false});
      });

      await useTallSurface(tester);
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: ProfileCvScreen(userId: 1),
        ),
      );
      await tester.pumpAndSettle();

      final jobAlertsSwitch = find.widgetWithText(SwitchListTile, 'Job alerts');
      await tester.ensureVisible(jobAlertsSwitch);
      await tester.pumpAndSettle();
      await tester.tap(jobAlertsSwitch);
      await tester.pump();

      await tester.enterText(find.byType(TextField).at(0), 'Zeus');
      await tester.enterText(find.byType(TextField).at(1), 'zeus@test.com');
      await tester.ensureVisible(find.text('Save Profile'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save Profile'));
      await tester.pumpAndSettle();

      expect(capturedSave, isNotNull);
      final body = jsonDecode(capturedSave!.body) as Map;
      expect(body['job_alerts_enabled'], isTrue);
    },
  );

  // ---------------------------------------------------------------------
  // User.whatsapp_alerts_enabled (GET /api/me, PUT /api/whatsapp_alerts) --
  // an account-level setting, not part of the candidate profile route()
  // above serves, so these get their own MockClient that also answers
  // /api/me.

  testWidgets(
    "pre-selects the WhatsApp alerts switch from the account's existing whatsapp_alerts_enabled",
    (tester) async {
      ApiClient.testClient = MockClient((request) async {
        if (request.url.path == '/api/me') {
          return http.Response(
            jsonEncode({
              "success": true,
              "user": {"id": 1, "whatsapp_alerts_enabled": true},
            }),
            200,
          );
        }
        return route(request, candidate: {});
      });

      await useTallSurface(tester);
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: ProfileCvScreen(userId: 1),
        ),
      );
      await tester.pumpAndSettle();

      expect(
        tester
            .widget<SwitchListTile>(
              find.widgetWithText(SwitchListTile, 'WhatsApp alerts'),
            )
            .value,
        isTrue,
      );
    },
  );

  testWidgets(
    'toggling WhatsApp alerts saves immediately via PUT /api/whatsapp_alerts',
    (tester) async {
      http.Request? capturedPut;
      ApiClient.testClient = MockClient((request) async {
        if (request.url.path == '/api/me') {
          return http.Response(
            jsonEncode({
              "success": true,
              "user": {"id": 1, "whatsapp_alerts_enabled": false},
            }),
            200,
          );
        }
        if (request.method == 'PUT' && request.url.path == '/api/whatsapp_alerts') {
          capturedPut = request;
          return http.Response(
            jsonEncode({"success": true, "whatsapp_alerts_enabled": true}),
            200,
          );
        }
        return route(request, candidate: {});
      });

      await useTallSurface(tester);
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: ProfileCvScreen(userId: 1),
        ),
      );
      await tester.pumpAndSettle();

      final whatsappSwitch = find.widgetWithText(SwitchListTile, 'WhatsApp alerts');
      await tester.ensureVisible(whatsappSwitch);
      await tester.pumpAndSettle();
      await tester.tap(whatsappSwitch);
      // Deliberately pump() not pumpAndSettle() -- this fires immediately
      // on toggle (see _setWhatsappAlertsEnabled), not gated behind the
      // Save Profile button the way the industry chips/job alerts switch
      // above are.
      await tester.pump();
      await tester.pump();

      expect(capturedPut, isNotNull);
      final body = jsonDecode(capturedPut!.body) as Map;
      expect(body['enabled'], isTrue);
      expect(
        tester.widget<SwitchListTile>(whatsappSwitch).value,
        isTrue,
      );
    },
  );

  // ---------------------------------------------------------------------
  // CV generation / HTML preview / PDF export (cv_html, ai_generated).
  //
  // _generateCv() resolves the candidate ID via
  // ApiClient.resolveCandidateId(), which -- with nothing cached in secure
  // storage (the mocked channel above answers every call with null) --
  // always falls through to a live GET /api/candidate/me lookup. So the
  // fixture candidate below must carry an "id", unlike the plain
  // save/industries tests above which never exercise that path.
  const sampleCvHtml =
      '<div class="cv-doc">'
      '<div class="cv-header">'
      '<h1>Mohamed Kamara</h1>'
      '<p class="cv-headline">Reliable Office Support Professional</p>'
      '<p class="cv-contact">mo@test.com &middot; Bo, Sierra Leone</p>'
      '</div>'
      '<div class="cv-section">'
      '<h2>Professional Summary</h2>'
      '<p>Mohamed Kamara is a hardworking recent graduate seeking an office role.</p>'
      '</div>'
      '<div class="cv-section">'
      '<h2>Key Skills</h2>'
      '<ul class="cv-skills"><li>Microsoft Excel</li><li>Typing</li></ul>'
      '</div>'
      '<div class="cv-section">'
      '<h2>Verified Credentials</h2>'
      '<ul class="cv-list cv-credentials">'
      '<li><strong>Computer Applications Certificate</strong> — Bo Digital Skills Centre (2024) '
      '<span class="cv-badge">Verified</span></li>'
      '</ul>'
      '</div>'
      '</div>';

  http.Response routeWithCv(
    http.Request request, {
    required Map<String, dynamic> candidate,
    bool aiGenerated = true,
  }) {
    if (request.url.path.startsWith('/api/generate_cv/')) {
      return http.Response(
        jsonEncode({
          "success": true,
          "candidate_id": candidate["id"],
          "cv": "Mohamed Kamara\nmo@test.com",
          "cv_html": sampleCvHtml,
          "ai_generated": aiGenerated,
        }),
        200,
        // The real backend response is UTF-8 JSON containing non-ASCII
        // punctuation (em dash, middle dot) -- http.Response defaults to
        // latin1 unless the content-type header says otherwise, which
        // throws on encode for those characters.
        headers: {'content-type': 'application/json; charset=utf-8'},
      );
    }
    return route(request, candidate: candidate);
  }

  testWidgets(
    'tapping Generate CV renders the formatted HTML preview content',
    (tester) async {
      ApiClient.testClient = MockClient(
        (request) async => routeWithCv(
          request,
          candidate: {
            "id": 7,
            "name": "Mohamed Kamara",
            "email": "mo@test.com",
          },
        ),
      );

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: ProfileCvScreen(userId: 1),
        ),
      );
      await tester.pumpAndSettle();

      await tester.ensureVisible(find.text('Generate CV'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Generate CV'));
      await tester.pumpAndSettle();

      expect(find.textContaining('Professional Summary'), findsOneWidget);
      expect(find.textContaining('Microsoft Excel'), findsOneWidget);
      expect(find.text('Export as PDF'), findsOneWidget);
    },
  );

  testWidgets('shows an "AI-enhanced" indicator when ai_generated is true', (
    tester,
  ) async {
    ApiClient.testClient = MockClient(
      (request) async => routeWithCv(
        request,
        candidate: {"id": 7, "name": "Mohamed Kamara", "email": "mo@test.com"},
        aiGenerated: true,
      ),
    );

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: ProfileCvScreen(userId: 1),
      ),
    );
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Generate CV'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Generate CV'));
    await tester.pumpAndSettle();

    expect(find.text('AI-enhanced'), findsOneWidget);
    expect(find.text('Basic'), findsNothing);
  });

  testWidgets('shows a "Basic" indicator when ai_generated is false', (
    tester,
  ) async {
    ApiClient.testClient = MockClient(
      (request) async => routeWithCv(
        request,
        candidate: {"id": 7, "name": "Mohamed Kamara", "email": "mo@test.com"},
        aiGenerated: false,
      ),
    );

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: ProfileCvScreen(userId: 1),
      ),
    );
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Generate CV'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Generate CV'));
    await tester.pumpAndSettle();

    expect(find.text('Basic'), findsOneWidget);
    expect(find.text('AI-enhanced'), findsNothing);
  });

  testWidgets(
    'the PDF export button only appears after a CV has been generated',
    (tester) async {
      ApiClient.testClient = MockClient(
        (request) async => routeWithCv(
          request,
          candidate: {
            "id": 7,
            "name": "Mohamed Kamara",
            "email": "mo@test.com",
          },
        ),
      );

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: ProfileCvScreen(userId: 1),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Export as PDF'), findsNothing);

      await tester.ensureVisible(find.text('Generate CV'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Generate CV'));
      await tester.pumpAndSettle();

      expect(find.text('Export as PDF'), findsOneWidget);
    },
  );
}
