// Widget coverage for SavedSearchesScreen -- lists GET /api/saved_searches
// and deletes via POST /api/saved_searches/<id>/delete, same
// ApiClient.testClient seam every other screen's tests use.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:youthchain_app/l10n/app_localizations.dart';
import 'package:youthchain_app/l10n/kri_material_fallback.dart';
import 'package:youthchain_app/screens/saved_searches_screen.dart';
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
    // _fetch() now reads /api/saved_searches via ApiClient.getWithCache
    // (BL-37 offline read caching, extended to this screen), which writes
    // through SharedPreferences on every successful fetch -- without a
    // mocked plugin instance that call never resolves under flutter_test.
    // Same fix saved_jobs_screen_test.dart already needed for the same
    // reason.
    SharedPreferences.setMockInitialValues({});
    ApiClient.baseUrl = 'http://127.0.0.1:5000';
    ApiClient.testClient = null;
  });

  tearDown(() {
    ApiClient.testClient = null;
  });

  final sampleSearches = [
    {"id": 1, "q": "developer", "location": null, "skill": null},
    {"id": 2, "q": null, "location": "Bo", "skill": "excel"},
  ];

  testWidgets('renders every saved search returned by the API', (tester) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode(sampleSearches), 200);
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const SavedSearchesScreen(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('developer'), findsOneWidget);
    expect(find.textContaining('excel'), findsOneWidget);
    expect(find.textContaining('Bo'), findsOneWidget);
  });

  testWidgets('shows an empty state when there are no saved searches', (
    tester,
  ) async {
    ApiClient.testClient = MockClient((request) async {
      return http.Response(jsonEncode([]), 200);
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const SavedSearchesScreen(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('No saved searches yet'), findsOneWidget);
  });

  testWidgets('deleting a saved search removes it from the list', (
    tester,
  ) async {
    var searches = List<Map<String, dynamic>>.from(sampleSearches);
    ApiClient.testClient = MockClient((request) async {
      if (request.method == 'POST' &&
          request.url.path == '/api/saved_searches/1/delete') {
        searches = searches.where((s) => s['id'] != 1).toList();
        return http.Response(jsonEncode({"success": true}), 200);
      }
      return http.Response(jsonEncode(searches), 200);
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        localizationsDelegates: appLocalizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const SavedSearchesScreen(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('developer'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.delete_outline_rounded).first);
    await tester.pumpAndSettle();

    expect(find.textContaining('developer'), findsNothing);
    expect(find.textContaining('excel'), findsOneWidget);
  });

  // BL-37 follow-up: SavedSearchesScreen previously called
  // ApiClient.instance.get() directly, with no offline fallback -- a
  // network failure here just meant an empty list. Now on getWithCache
  // (see saved_jobs_screen_test.dart / api_client_cache_test.dart for the
  // same pattern), it should keep showing the last successfully loaded
  // searches instead.
  testWidgets(
    'falls back to the last loaded searches and shows an offline banner when the network fails',
    (tester) async {
      ApiClient.testClient = MockClient((request) async {
        return http.Response(jsonEncode(sampleSearches), 200);
      });

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const SavedSearchesScreen(),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.textContaining('developer'), findsOneWidget);
      expect(find.textContaining("You're offline"), findsNothing);

      // Go offline and pull to refresh -- getWithCache should fall back to
      // the copy cached by the successful load above rather than clearing
      // the list.
      ApiClient.testClient = MockClient((request) async {
        throw Exception('connection refused');
      });

      await tester.fling(
        find.byType(RefreshIndicator),
        const Offset(0, 300),
        1000,
      );
      await tester.pump();
      await tester.pump(const Duration(seconds: 1));
      await tester.pumpAndSettle();

      expect(find.textContaining('developer'), findsOneWidget);
      expect(find.textContaining("You're offline"), findsOneWidget);
    },
  );

  testWidgets(
    'shows a "no connection" message when offline with nothing ever cached',
    (tester) async {
      ApiClient.testClient = MockClient((request) async {
        throw Exception('connection refused');
      });

      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.light(),
          localizationsDelegates: appLocalizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const SavedSearchesScreen(),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('No saved searches yet'), findsOneWidget);
      expect(
        find.text('No connection and no previously loaded data.'),
        findsOneWidget,
      );
    },
  );
}
