# SMS Gateway App — Auto-Update System

## Overview

Server-driven auto-update for the VaryShop SMS Gateway Android app, distributed as APK outside Google Play. Updates are checked via the existing heartbeat endpoint. APK files are stored in Odoo as `ir.attachment` and served via a dedicated download endpoint.

## Odoo: Model `sms.gateway.release`

New model in the `sms_gateway` module.

### Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `version` | Char | Yes | — | Semver string (e.g. "1.6.0") |
| `version_code` | Integer | Yes | — | Android versionCode for numeric comparison |
| `apk_file` | Binary | Yes | — | APK binary stored as ir.attachment |
| `apk_filename` | Char | No | — | Original filename |
| `file_size` | Integer | Computed | — | APK file size in bytes (from attachment) |
| `release_notes` | Text | No | — | Changelog shown to users in the app |
| `force_update` | Boolean | No | False | Force users to update before using the app |
| `active` | Boolean | No | True | Deactivating hides the release |
| `release_date` | Datetime | No | now | When the release was published |

### Logic

- Current version = latest active record ordered by `version_code DESC`
- `file_size` is a computed field derived from the `ir.attachment` size
- SQL constraint: `version_code` must be unique among active records

### Views

- **List view**: version, version_code, release_date, force_update, file_size, active
- **Form view**: all fields, APK upload widget, release notes textarea
- **Menu item**: under SMS Gateway top-level menu

### Security

- Read: `base.group_user`
- Write/Create/Unlink: `mass_mailing.group_mass_mailing_user`

## Odoo: Download Endpoint

New route in `gateway_api.py`:

```
GET /sms-gateway/download/<int:release_id>
```

- Auth: public (validated via X-API-Key header, same as other endpoints)
- Returns APK binary as `application/vnd.android.package-archive` with `Content-Disposition: attachment; filename=...`
- Streams from `ir.attachment` — no full file load into memory
- 404 if release not found or inactive
- 401 if API key invalid

## Odoo: Heartbeat Extension

### Request change

App sends its current version in the heartbeat body:

```json
{
  "phone_numbers": [...],
  "battery_level": 85,
  "signal_strength": -70,
  "app_version": 15
}
```

`app_version` is the integer versionCode from the Android build.

### Response change

Heartbeat response gains an `app_update` field:

```json
{
  "success": true,
  "pending_count": { ... },
  "rate_limit": 100,
  "phone_stats": { ... },
  "app_update": {
    "available": true,
    "version": "1.6.0",
    "version_code": 16,
    "force": false,
    "release_notes": "Oprava stability...",
    "download_url": "/sms-gateway/download/5",
    "file_size": 42000000
  }
}
```

- If no update available: `"app_update": null`
- If `app_version` is missing from request: `"app_update": null` (backwards compatible)
- Comparison: latest active release `version_code` > request `app_version`

## Android App: Types

Extend `HeartbeatResponse` in `src/types/index.ts`:

```typescript
interface AppUpdate {
  available: boolean;
  version: string;
  version_code: number;
  force: boolean;
  release_notes: string;
  download_url: string;
  file_size: number;
}

interface HeartbeatResponse {
  // ... existing fields ...
  app_update: AppUpdate | null;
}
```

## Android App: Heartbeat Change

In `src/api/gatewayClient.ts`, the `heartbeat()` method sends `app_version` (the current build's versionCode) alongside existing parameters.

The versionCode is read from `expo-constants` (`Constants.expoConfig?.android?.versionCode`).

## Android App: Update Service

New file `src/services/updateService.ts`:

- Subscribes to heartbeat responses via `onHeartbeat()`
- When `app_update?.available` is true, stores update info in module-level state
- Exposes: `getAvailableUpdate()`, `onUpdateAvailable(callback)`, `downloadAndInstall()`
- `downloadAndInstall()`:
  1. Downloads APK from `baseUrl + download_url` to app cache directory using `expo-file-system` (`FileSystem.downloadAsync`), passing `X-API-Key` header for authentication
  2. Calls native `ApkInstaller.installApk(localPath)` to trigger Android package installer
- Download uses the same API key already stored in app settings (no extra auth needed)
- Dependency: `expo-file-system` (already available in Expo SDK 54)

## Android App: Update UI

### Banner (non-force)

On the Dashboard screen (`app/(tabs)/dashboard.tsx`), a dismissible banner at the top:

- Blue/green background with text: "Dostupná verze {version}"
- "Aktualizovat" button triggers download + install
- "Později" dismisses (reappears on next heartbeat)
- Shows download progress during download

### Force overlay (force=true)

In `app/_layout.tsx`, a fullscreen overlay rendered above everything:

- Dark background, centered card
- Title: "Vyžadována aktualizace"
- Release notes text
- Single button: "Aktualizovat nyní"
- No close/dismiss option
- Shows download progress during download

## Android App: Native Module `apk-installer`

New Expo native module at `modules/apk-installer/`:

### Structure

```
modules/apk-installer/
├── index.ts                          # JS export
├── src/ApkInstallerModule.ts         # Expo module definition
└── android/
    └── src/main/java/expo/modules/apkinstaller/
        ├── ApkInstallerModule.kt     # Kotlin module
        └── ApkInstallerFileProvider.kt  # Empty subclass of FileProvider
```

### Native method

`installApk(filePath: String)`:
1. Copies APK to a FileProvider-accessible directory if needed
2. Creates intent: `ACTION_VIEW` with `application/vnd.android.package-archive` MIME type
3. Uses `FileProvider.getUriForFile()` to get content:// URI
4. Adds `FLAG_GRANT_READ_URI_PERMISSION` + `FLAG_ACTIVITY_NEW_TASK`
5. Starts activity with the intent

### FileProvider config

`android/app/src/main/res/xml/apk_installer_paths.xml`:
```xml
<paths>
  <cache-path name="apk_cache" path="." />
</paths>
```

Registered in AndroidManifest.xml via the module's app.plugin.js (Expo config plugin).

## Android App: Permissions

Add to `AndroidManifest.xml`:
```xml
<uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />
```

Added via the `apk-installer` module's Expo config plugin (`app.plugin.js`), keeping manifest changes co-located with the module that needs them.

## Files Modified / Created

### Odoo (extra/sms/sms_modules/sms_gateway/)

| Action | File |
|--------|------|
| Create | `models/sms_gateway_release.py` |
| Modify | `models/__init__.py` (add import) |
| Create | `views/sms_gateway_release_views.xml` |
| Modify | `views/sms_gateway_phone_views.xml` (add menu item) |
| Modify | `security/ir.model.access.csv` (add ACL rows) |
| Modify | `__manifest__.py` (add data files) |
| Modify | `controllers/gateway_api.py` (heartbeat extension + download endpoint) |

### Android App (extra/sms/sms-gateway-app/)

| Action | File |
|--------|------|
| Create | `modules/apk-installer/index.ts` |
| Create | `modules/apk-installer/src/ApkInstallerModule.ts` |
| Create | `modules/apk-installer/android/src/main/java/expo/modules/apkinstaller/ApkInstallerModule.kt` |
| Create | `modules/apk-installer/android/src/main/java/expo/modules/apkinstaller/ApkInstallerFileProvider.kt` |
| Create | `modules/apk-installer/android/build.gradle` |
| Create | `modules/apk-installer/android/src/main/AndroidManifest.xml` |
| Create | `modules/apk-installer/app.plugin.js` |
| Create | `modules/apk-installer/expo-module.config.json` |
| Create | `src/services/updateService.ts` |
| Modify | `src/types/index.ts` (add AppUpdate type) |
| Modify | `src/api/gatewayClient.ts` (send app_version in heartbeat) |
| Modify | `app/_layout.tsx` (force update overlay) |
| Modify | `app/(tabs)/dashboard.tsx` (update banner) |
| Modify | `app.json` (add apk-installer plugin) |
