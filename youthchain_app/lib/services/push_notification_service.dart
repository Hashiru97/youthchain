import 'package:firebase_messaging/firebase_messaging.dart';

import 'api_client.dart';

/// Registers this device's FCM token with the backend so
/// send_push_notification() (backend/app.py) has somewhere to deliver to.
/// Mirrors PUT /api/push_token's own docstring: called after login, and
/// again whenever FCM rotates the token.
class PushNotificationService {
  PushNotificationService._();

  static bool _listenerAttached = false;

  /// Requests notification permission, fetches the current FCM token, and
  /// registers it with the backend. Safe to call more than once (e.g. once
  /// from AuthGate for a restored session, once right after a fresh
  /// login/registration) — each call just PUTs whatever the current token
  /// is, which is an idempotent no-op if it hasn't changed.
  ///
  /// Deliberately swallows every failure: a user who denies the
  /// notification permission, or a device without Google Play services
  /// (some Android builds, all emulators without the Play Store image),
  /// must never be blocked from logging in or using the app over this.
  static Future<void> registerToken() async {
    try {
      final messaging = FirebaseMessaging.instance;
      await messaging.requestPermission(alert: true, badge: true, sound: true);
      final token = await messaging.getToken();
      if (token != null) {
        await ApiClient.instance.putJson('/api/push_token', {'push_token': token});
      }
      _attachRefreshListener();
    } catch (_) {
      // Best-effort — see docstring above.
    }
  }

  /// FCM rotates the token occasionally (reinstall, restore, security
  /// rotation); each rotation must be re-registered or push silently stops
  /// working for that device. Attached once per app lifetime, not once per
  /// registerToken() call, so re-logging in doesn't stack duplicate
  /// listeners.
  static void _attachRefreshListener() {
    if (_listenerAttached) return;
    _listenerAttached = true;
    FirebaseMessaging.instance.onTokenRefresh.listen((token) async {
      try {
        await ApiClient.instance.putJson('/api/push_token', {'push_token': token});
      } catch (_) {
        // Best-effort — see registerToken()'s docstring.
      }
    });
  }

  /// Clears this device's token server-side on logout, so a stale token
  /// from a previous user of a shared device never receives another
  /// user's push notifications (see PUT /api/push_token's docstring).
  /// Called BEFORE ApiClient.clearSession() — needs the still-valid
  /// Authorization header to identify whose token to clear.
  static Future<void> clearToken() async {
    try {
      await ApiClient.instance.putJson('/api/push_token', {'push_token': null});
    } catch (_) {
      // Offline or network error — local logout must still proceed.
    }
  }
}
