// Widget coverage for SavedSearchesScreen -- lists GET /api/saved_searches
// and deletes via POST /api/saved_searches/<id>/delete, same
// ApiClient.testClient seam every other screen's tests use.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

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
}
