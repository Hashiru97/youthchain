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
    // Every queue entry is now tagged with ApiClient.getUserId() at
    // enqueue time and filtered against it again on read/sync (see
    // PendingApplicationsQueue.loadForCurrentUser()'s own docstring on
    // why: without this, a shared-device account switch would leak the
    // previous account's still-pending application to the next one).
    // Secure storage's platform channel isn't mocked in this harness, so
    // the write below will throw -- caught here the same way ApiClient's
    // own getToken()/getUserId() already degrade to null on it, but the
    // in-memory cache (set synchronously before that awaited write) is
    // what actually makes getUserId() resolve to 1 for the rest of each
    // test regardless.
    try {
      await ApiClient.instance.saveSession(token: 'test-token', userId: 1);
    } catch (_) {}
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

  test('a shared device: switching accounts hides the previous account\'s '
      'pending application and does not sync it under the new one', () async {
    // User 1 (set up in setUp above) queues an application offline.
    await PendingApplicationsQueue.instance.enqueue(
      jobId: 55,
      jobTitle: 'User 1s job',
      cvFileName: 'cv.pdf',
      cvBytes: utf8.encode('user 1 cv content'),
    );

    // User 1 logs out, User 2 logs in on the same device.
    try {
      await ApiClient.instance.clearSession();
    } catch (_) {}
    try {
      await ApiClient.instance.saveSession(token: 'user-2-token', userId: 2);
    } catch (_) {}

    // User 2 must not see User 1's still-pending application...
    final visibleToUser2 = await PendingApplicationsQueue.instance.loadForCurrentUser();
    expect(visibleToUser2, isEmpty);

    // ...and reconnecting must not silently submit it under User 2's
    // identity (the request below would fail this expectation if
    // trySyncAll() ever reached the network for it).
    ApiClient.testClient = MockClient((request) async {
      fail('trySyncAll() must not submit another account\'s queued application');
    });
    await PendingApplicationsQueue.instance.trySyncAll();

    // The entry is still there, untouched, for when User 1 logs back in.
    final raw = await PendingApplicationsQueue.instance.loadAll();
    expect(raw, hasLength(1));
    expect(raw.first.jobId, 55);

    try {
      await ApiClient.instance.clearSession();
    } catch (_) {}
    try {
      await ApiClient.instance.saveSession(token: 'test-token', userId: 1);
    } catch (_) {}
    final visibleToUser1Again = await PendingApplicationsQueue.instance.loadForCurrentUser();
    expect(visibleToUser1Again, hasLength(1));
    expect(visibleToUser1Again.first.jobId, 55);
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
