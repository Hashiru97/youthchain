# Mobile release builds

Real gap found via a full-codebase review: `ApiClient.baseUrl` (see
`youthchain_app/lib/services/api_client.dart`) defaults to
`http://127.0.0.1:5000` — correct for local development against a backend
running on the same machine, but there was no documented, CI-verified
process for producing a real release build that talks to an actual
deployed backend instead. Before this, someone running
`flutter build apk` with no special flags would silently ship a build that
can only ever reach `127.0.0.1` on the device it's running on — which
means it simply cannot reach any real backend at all, not a security
downgrade so much as a build that doesn't work, with no build-time signal
that anything was wrong.

## Building a real release

The base URL is set at **build time**, not runtime — there's no in-app
settings screen for it, and there shouldn't be (an app that lets its own
API host be redirected at runtime is a phishing/MITM vector in disguise).

```
flutter build apk --release \
  --dart-define=API_BASE_URL=https://api.youthchain.example
```

or for iOS:

```
flutter build ipa --release \
  --dart-define=API_BASE_URL=https://api.youthchain.example
```

`API_BASE_URL` **must** be an `https://` URL for any build that will run
on a real device outside a developer's own machine — see
`backend/.env.example`'s own notes on TLS termination (this backend
expects to sit behind a reverse proxy that terminates HTTPS; a mobile
build pointed at plain `http://` sends every password, JWT, and uploaded
CV/certificate in plaintext).

## CI: a release build is now actually attempted, not just analyzed/tested

The `mobile` job in `.github/workflows/ci.yml` previously only ran
`flutter analyze` and `flutter test` — genuinely useful, but neither one
catches the class of bug that only shows up in a release build specifically
(tree-shaking removing something reflection-dependent, a `--dart-define`
typo, a release-only compile error). The job now also runs
`flutter build apk --release --dart-define=API_BASE_URL=...`, using the
`MOBILE_API_BASE_URL` repository variable if one is configured, or the
same local-dev loopback default otherwise — either way, the build itself
is what's being verified here, not the specific URL value baked into a CI
artifact nobody ships. Set a real `MOBILE_API_BASE_URL` repository
variable (Settings → Secrets and variables → Actions → Variables) once a
real backend domain exists, so CI's build artifact naming/logs make it
obvious whether a given run used the real URL or the loopback fallback.

## What this still doesn't cover (recorded as a known gap, not solved here)

- **Code signing.** `flutter build apk --release` without a configured
  signing key produces a build signed with Flutter's own debug keystore —
  installable for testing, but Android will refuse it as an update to a
  Play Store listing signed with a real key, and it isn't what should ever
  ship to end users. A real release needs a real upload keystore
  (`key.properties` + `android/app/build.gradle` signing config, per
  Flutter's own deployment docs) whose private key is never committed —
  this needs a real signing identity to exist first, which is a
  business/ops decision (who holds the key, how it's backed up), not
  something this pass can generate.
- **App Store / Play Store submission.** Entirely out of scope here —
  this document only covers producing a correctly-configured build
  artifact, not publishing it.
- **iOS provisioning profiles / Apple Developer Program enrollment.** Same
  reasoning as code signing above.
