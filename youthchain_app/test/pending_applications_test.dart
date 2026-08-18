import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:path_provider_platform_interface/path_provider_platform_interface.dart';
import 'package:plugin_platform_interface/plugin_platform_interface.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:youthchain_app/services/api_client.dart';
import 'package:youthchain_app/services/pending_applications.dart';

class _FakePathProviderPlatform extends PathProviderPlatform
    with MockPlatformInterfaceMixin {
  final String tempDirPath;
  _FakePathProviderPlatform(this.tempDirPath);

  @override
  Future<String?> getApplicationDocumentsPath() async => tempDirPath;
}

class _SocketExceptionStub implements Exception {
  const _SocketExceptionStub();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Directory tempDir;

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    tempDir = await Directory.systemTemp.createTemp('yc_pending_test_');
    PathProviderPlatform.instance = _FakePathProviderPlatform(tempDir.path);
  });

  tearDown(() async {
    ApiClient.testClient = null;
    try {
      await tempDir.delete(recursive: true);
    } catch (_) {}
  });

  test('enqueue persists a pending application with real files on disk', () async {
    await PendingApplicationsQueue.instance.enqueue(
      jobId: 42,
      jobTitle: 'Fisheries Data Clerk',
      cvFileName: 'cv.pdf',
      cvBytes: utf8.encode('fake cv bytes'),
    );

    final items = await PendingApplicationsQueue.instance.loadAll();
    expect(items, hasLength(1));
    expect(items.first.jobId, 42);
    expect(items.first.jobTitle, 'Fisheries Data Clerk');
    expect(await File(items.first.cvLocalPath).exists(), isTrue);
    expect(
      await File(items.first.cvLocalPath).readAsString(),
      'fake cv bytes',
    );
  });

  test('trySyncAll submits a queued application and removes it on success', () async {
    await PendingApplicationsQueue.instance.enqueue(
      jobId: 7,
      jobTitle: 'Junior Developer',
      cvFileName: 'cv.pdf',
      cvBytes: utf8.encode('cv content'),
    );

    final beforeSync = await PendingApplicationsQueue.instance.loadAll();
    final cvPath = beforeSync.first.cvLocalPath;
    expect(await File(cvPath).exists(), isTrue);

    ApiClient.testClient = MockClient((request) async {
      expect(request.url.path, '/apply');
      return http.Response('{"application_id": 1}', 201);
    });

    await PendingApplicationsQueue.instance.trySyncAll();

    final afterSync = await PendingApplicationsQueue.instance.loadAll();
    expect(afterSync, isEmpty);
    expect(await File(cvPath).exists(), isFalse);
  });

  test('trySyncAll leaves the item queued (not failed) on a network error', () async {
    await PendingApplicationsQueue.instance.enqueue(
      jobId: 9,
      jobTitle: 'Remote Role',
      cvFileName: 'cv.pdf',
      cvBytes: utf8.encode('cv content'),
    );

    ApiClient.testClient = MockClient((request) async {
      throw const _SocketExceptionStub();
    });

    await PendingApplicationsQueue.instance.trySyncAll();

    final items = await PendingApplicationsQueue.instance.loadAll();
    expect(items, hasLength(1));
    expect(items.first.status, 'pending');
  });

  test('trySyncAll marks the item failed (not silently dropped) on a real server rejection', () async {
    await PendingApplicationsQueue.instance.enqueue(
      jobId: 11,
      jobTitle: 'Rejected Role',
      cvFileName: 'cv.pdf',
      cvBytes: utf8.encode('cv content'),
    );

    ApiClient.testClient = MockClient((request) async {
      return http.Response('{"error": "Job no longer accepting applications"}', 400);
    });

    await PendingApplicationsQueue.instance.trySyncAll();

    final items = await PendingApplicationsQueue.instance.loadAll();
    expect(items, hasLength(1));
    expect(items.first.status, 'failed');
    expect(items.first.lastError, contains('no longer accepting'));
  });

  test('discard removes the queue entry and deletes its local file', () async {
    await PendingApplicationsQueue.instance.enqueue(
      jobId: 3,
      jobTitle: 'To Discard',
      cvFileName: 'cv.pdf',
      cvBytes: utf8.encode('cv content'),
    );
    final items = await PendingApplicationsQueue.instance.loadAll();
    final cvPath = items.first.cvLocalPath;

    await PendingApplicationsQueue.instance.discard(items.first);

    expect(await PendingApplicationsQueue.instance.loadAll(), isEmpty);
    expect(await File(cvPath).exists(), isFalse);
  });
}
