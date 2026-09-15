# YouthChain — Mobile App (Flutter)

The youth-facing mobile client for YouthChain: registration with email-OTP,
skill-matched job discovery, applications with document upload, in-app
messaging with employers, and a blockchain-verified digital Employment
Passport. See the [repo root README](../README.md) for the full system
picture (backend, admin console, smart contract).

## Running against the backend

The API host is a single build-time constant, not a config file —
[`lib/services/api_client.dart`](lib/services/api_client.dart). It defaults
to `http://127.0.0.1:5000` (the local dev backend), which **will not work**
on a physical device or a demo machine unless overridden:

```bash
# Local dev (emulator/simulator on the same machine as the backend):
flutter run

# Real device or a deployed backend — always set this for a demo/pitch:
flutter run --dart-define=API_BASE_URL=https://youthchain-backend.up.railway.app
flutter build apk --release --dart-define=API_BASE_URL=https://youthchain-backend.up.railway.app
```

The live demo backend above is a real deployment on Railway (Postgres +
Flask, HTTPS) — not hardened for sustained public traffic, but genuinely
running, not a localhost screen-share.

**Before any live demo**: confirm the build was compiled with the real
backend URL and test it on the actual network you'll be presenting on (venue
wifi/hotspot), not just the office network — a build left on the default
localhost URL will fail silently on first load with no prior cache to fall
back on.

## Setup

```bash
flutter pub get
flutter run
```

Requires the Flutter SDK version pinned in [`pubspec.yaml`](pubspec.yaml).
Several packages are deliberately version-capped there (see the inline
comments) because newer releases require a newer Android Gradle
Plugin/compileSdk than this project targets — do not run
`flutter pub upgrade --major-versions` without reading those comments first.

## Testing

```bash
flutter analyze   # 0 issues
flutter test      # 179 tests
```
