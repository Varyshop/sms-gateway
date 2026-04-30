# SMS Gateway Auto-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add server-driven auto-update to the SMS Gateway Android app so users get new APK versions via heartbeat-driven checks, with optional force-update blocking.

**Architecture:** Odoo model stores APK releases; heartbeat response gains an `app_update` block when a newer version exists; the RN app downloads the APK via `expo-file-system` and triggers Android's package installer via a small Expo native module.

**Tech Stack:** Odoo 18 (Python), React Native / Expo SDK 54 (TypeScript), Kotlin (Expo native module), expo-file-system

**Spec:** `docs/superpowers/specs/2026-04-29-auto-update-design.md`

---

## File Map

### Odoo — `extra/sms/sms_modules/sms_gateway/`

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `models/sms_gateway_release.py` | Release model with version, APK binary, force flag |
| Modify | `models/__init__.py` | Add import for new model |
| Create | `views/sms_gateway_release_views.xml` | List + form views, action, menu item |
| Modify | `views/sms_gateway_phone_views.xml` | Add "App Releases" menu item under SMS Gateway |
| Modify | `security/ir.model.access.csv` | ACL rows for new model |
| Modify | `__manifest__.py` | Register new data files |
| Modify | `controllers/gateway_api.py` | Heartbeat extension + download endpoint |

### Android App — `extra/sms/sms-gateway-app/`

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `modules/apk-installer/index.ts` | JS interface to native installer |
| Create | `modules/apk-installer/expo-module.config.json` | Expo module registration |
| Create | `modules/apk-installer/package.json` | Module metadata |
| Create | `modules/apk-installer/app.plugin.js` | Config plugin: permission + FileProvider |
| Create | `modules/apk-installer/android/build.gradle` | Android build config |
| Create | `modules/apk-installer/android/src/main/AndroidManifest.xml` | Module manifest |
| Create | `modules/apk-installer/android/src/main/res/xml/apk_paths.xml` | FileProvider paths |
| Create | `modules/apk-installer/android/src/main/java/expo/modules/apkinstaller/ApkInstallerModule.kt` | Kotlin: copy APK to FileProvider dir + launch install intent |
| Create | `modules/apk-installer/android/src/main/java/expo/modules/apkinstaller/ApkInstallerFileProvider.kt` | Empty FileProvider subclass |
| Create | `src/services/updateService.ts` | Update state management, download, install orchestration |
| Modify | `src/types/index.ts` | Add `AppUpdate` interface, extend `HeartbeatResponse` |
| Modify | `src/api/gatewayClient.ts` | Send `app_version` in heartbeat request |
| Modify | `app/_layout.tsx` | Force-update fullscreen overlay |
| Modify | `app/(tabs)/dashboard.tsx` | Update banner component |
| Modify | `app.json` | Register apk-installer plugin |
| Modify | `package.json` | Add `expo-file-system` dependency |

---

## Task 1: Odoo `sms.gateway.release` Model

**Files:**
- Create: `extra/sms/sms_modules/sms_gateway/models/sms_gateway_release.py`
- Modify: `extra/sms/sms_modules/sms_gateway/models/__init__.py`

- [ ] **Step 1: Create the release model**

Create `extra/sms/sms_modules/sms_gateway/models/sms_gateway_release.py`:

```python
# -*- coding: utf-8 -*-

from odoo import api, fields, models


class SmsGatewayRelease(models.Model):
    _name = 'sms.gateway.release'
    _description = 'SMS Gateway App Release'
    _order = 'version_code desc'

    version = fields.Char(string='Version', required=True, help='Semver string, e.g. 1.6.0')
    version_code = fields.Integer(string='Version Code', required=True,
                                  help='Android versionCode (integer) for numeric comparison')
    apk_file = fields.Binary(string='APK File', required=True, attachment=True)
    apk_filename = fields.Char(string='APK Filename')
    file_size = fields.Integer(string='File Size (bytes)', compute='_compute_file_size', store=True)
    release_notes = fields.Text(string='Release Notes', help='Changelog shown to users in the app')
    force_update = fields.Boolean(string='Force Update', default=False,
                                  help='Force users to update before using the app')
    active = fields.Boolean(default=True)
    release_date = fields.Datetime(string='Release Date', default=fields.Datetime.now)

    _sql_constraints = [
        ('version_code_unique', 'UNIQUE(version_code)',
         'Version code must be unique.'),
    ]

    @api.depends('apk_file')
    def _compute_file_size(self):
        for rec in self:
            if rec.apk_file:
                att = self.env['ir.attachment'].sudo().search([
                    ('res_model', '=', self._name),
                    ('res_id', '=', rec.id),
                    ('res_field', '=', 'apk_file'),
                ], limit=1)
                rec.file_size = att.file_size if att else 0
            else:
                rec.file_size = 0

    @api.model
    def get_latest_release(self):
        return self.search([('active', '=', True)], limit=1, order='version_code desc')
```

- [ ] **Step 2: Register the model in `__init__.py`**

Add this line to `extra/sms/sms_modules/sms_gateway/models/__init__.py` after the existing imports:

```python
from . import sms_gateway_release
```

- [ ] **Step 3: Commit**

```bash
git add extra/sms/sms_modules/sms_gateway/models/sms_gateway_release.py extra/sms/sms_modules/sms_gateway/models/__init__.py
git commit -m "feat(sms-gateway): add sms.gateway.release model for APK version management"
```

---

## Task 2: Odoo Security & Manifest

**Files:**
- Modify: `extra/sms/sms_modules/sms_gateway/security/ir.model.access.csv`
- Modify: `extra/sms/sms_modules/sms_gateway/__manifest__.py`

- [ ] **Step 1: Add ACL rows**

Append these two lines to the end of `extra/sms/sms_modules/sms_gateway/security/ir.model.access.csv`:

```csv
access_sms_gateway_release_user,sms.gateway.release.user,model_sms_gateway_release,base.group_user,1,0,0,0
access_sms_gateway_release_manager,sms.gateway.release.manager,model_sms_gateway_release,mass_mailing.group_mass_mailing_user,1,1,1,1
```

- [ ] **Step 2: Register new view file in manifest**

In `extra/sms/sms_modules/sms_gateway/__manifest__.py`, add `'views/sms_gateway_release_views.xml'` to the `'data'` list, after the existing `'views/sms_gateway_phone_views.xml'` entry:

```python
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'data/sms_marketing_segment_data.xml',
        'views/sms_gateway_phone_views.xml',
        'views/sms_gateway_release_views.xml',  # <-- add this line
        'views/mailing_mailing_views.xml',
        'views/res_config_settings_views.xml',
        'views/sms_sms_views.xml',
        'views/sms_gateway_send_wizard_views.xml',
        'views/sms_marketing_template_views.xml',
        'views/res_partner_views.xml',
    ],
```

- [ ] **Step 3: Commit**

```bash
git add extra/sms/sms_modules/sms_gateway/security/ir.model.access.csv extra/sms/sms_modules/sms_gateway/__manifest__.py
git commit -m "feat(sms-gateway): register release model in security ACL and manifest"
```

---

## Task 3: Odoo Views & Menu

**Files:**
- Create: `extra/sms/sms_modules/sms_gateway/views/sms_gateway_release_views.xml`
- Modify: `extra/sms/sms_modules/sms_gateway/views/sms_gateway_phone_views.xml`

- [ ] **Step 1: Create views for release model**

Create `extra/sms/sms_modules/sms_gateway/views/sms_gateway_release_views.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <!-- List View -->
    <record id="sms_gateway_release_view_list" model="ir.ui.view">
        <field name="name">sms.gateway.release.list</field>
        <field name="model">sms.gateway.release</field>
        <field name="arch" type="xml">
            <list string="App Releases" default_order="version_code desc">
                <field name="version"/>
                <field name="version_code"/>
                <field name="release_date"/>
                <field name="force_update"/>
                <field name="file_size" widget="integer"/>
                <field name="active"/>
            </list>
        </field>
    </record>

    <!-- Form View -->
    <record id="sms_gateway_release_view_form" model="ir.ui.view">
        <field name="name">sms.gateway.release.form</field>
        <field name="model">sms.gateway.release</field>
        <field name="arch" type="xml">
            <form string="App Release">
                <sheet>
                    <group>
                        <group string="Version">
                            <field name="version"/>
                            <field name="version_code"/>
                            <field name="release_date"/>
                            <field name="active"/>
                        </group>
                        <group string="APK">
                            <field name="apk_file" filename="apk_filename"/>
                            <field name="apk_filename" invisible="1"/>
                            <field name="file_size"/>
                            <field name="force_update"/>
                        </group>
                    </group>
                    <group string="Release Notes">
                        <field name="release_notes" nolabel="1" colspan="2"/>
                    </group>
                </sheet>
            </form>
        </field>
    </record>

    <!-- Action -->
    <record id="sms_gateway_release_action" model="ir.actions.act_window">
        <field name="name">App Releases</field>
        <field name="res_model">sms.gateway.release</field>
        <field name="view_mode">list,form</field>
        <field name="help" type="html">
            <p class="o_view_nocontent_smiling_face">
                Upload your first APK release
            </p>
            <p>
                Upload APK files for the SMS Gateway mobile app.
                Connected phones will be notified of new versions via heartbeat.
            </p>
        </field>
    </record>

</odoo>
```

- [ ] **Step 2: Add menu item in phone views file**

In `extra/sms/sms_modules/sms_gateway/views/sms_gateway_phone_views.xml`, add a new `<menuitem>` after the existing `sms_gateway_phone_menu` menuitem (after line 151, before the SMS Messages menuitem):

```xml
    <menuitem id="sms_gateway_release_menu"
              name="App Releases"
              parent="sms_gateway_menu_root"
              action="sms_gateway_release_action"
              sequence="15"/>
```

- [ ] **Step 3: Commit**

```bash
git add extra/sms/sms_modules/sms_gateway/views/sms_gateway_release_views.xml extra/sms/sms_modules/sms_gateway/views/sms_gateway_phone_views.xml
git commit -m "feat(sms-gateway): add release list/form views and menu item"
```

---

## Task 4: Odoo Heartbeat Extension & Download Endpoint

**Files:**
- Modify: `extra/sms/sms_modules/sms_gateway/controllers/gateway_api.py`

- [ ] **Step 1: Add `_get_app_update` helper method**

In `extra/sms/sms_modules/sms_gateway/controllers/gateway_api.py`, add this method to `SmsGatewayController` class, before the `# ---- Heartbeat ----` comment (before line 66):

```python
    def _get_app_update(self, app_version):
        """Check if a newer app release is available."""
        if not app_version:
            return None
        Release = request.env['sms.gateway.release'].sudo()
        latest = Release.get_latest_release()
        if not latest or latest.version_code <= app_version:
            return None
        return {
            'available': True,
            'version': latest.version,
            'version_code': latest.version_code,
            'force': latest.force_update,
            'release_notes': latest.release_notes or '',
            'download_url': f'/sms-gateway/download/{latest.id}',
            'file_size': latest.file_size,
        }
```

- [ ] **Step 2: Extend the heartbeat method to include `app_update`**

In the `heartbeat` method, add the `app_version` read from request data after the existing `unsynced_count` line (after line 81):

```python
            app_version = data.get('app_version')
```

Then, in the return `_json_response` dict (around line 118-123), add the `app_update` key:

```python
            return self._json_response({
                'success': True,
                'pending_count': pending_count,
                'rate_limit': phones[0].rate_limit if phones else 100,
                'phone_stats': phone_stats,
                'app_update': self._get_app_update(app_version),
            })
```

- [ ] **Step 3: Add the download endpoint**

Add this new route method to the `SmsGatewayController` class, after the heartbeat method:

```python
    # ---- App Download ----

    @http.route('/sms-gateway/download/<int:release_id>', type='http', auth='public',
                methods=['GET'], csrf=False, cors='*')
    def download_release(self, release_id, **kwargs):
        """Download APK file for a specific release."""
        try:
            api_key = self._get_api_key()
            phones = self._validate_api_key(api_key)
            if not phones:
                return self._error_response('Invalid API key', 401)

            release = request.env['sms.gateway.release'].sudo().browse(release_id)
            if not release.exists() or not release.active:
                return self._error_response('Release not found', 404)

            att = request.env['ir.attachment'].sudo().search([
                ('res_model', '=', 'sms.gateway.release'),
                ('res_id', '=', release.id),
                ('res_field', '=', 'apk_file'),
            ], limit=1)
            if not att:
                return self._error_response('APK file not found', 404)

            filename = release.apk_filename or f'sms-gateway-{release.version}.apk'
            return request.make_response(
                att.raw,
                headers=[
                    ('Content-Type', 'application/vnd.android.package-archive'),
                    ('Content-Disposition', f'attachment; filename="{filename}"'),
                    ('Content-Length', str(len(att.raw))),
                ],
            )
        except Exception as e:
            _logger.exception('SMS Gateway download error')
            return self._error_response(str(e), 500)
```

- [ ] **Step 4: Commit**

```bash
git add extra/sms/sms_modules/sms_gateway/controllers/gateway_api.py
git commit -m "feat(sms-gateway): extend heartbeat with app_update + add APK download endpoint"
```

---

## Task 5: Android Native Module — `apk-installer`

**Files:**
- Create: `extra/sms/sms-gateway-app/modules/apk-installer/expo-module.config.json`
- Create: `extra/sms/sms-gateway-app/modules/apk-installer/package.json`
- Create: `extra/sms/sms-gateway-app/modules/apk-installer/android/build.gradle`
- Create: `extra/sms/sms-gateway-app/modules/apk-installer/android/src/main/AndroidManifest.xml`
- Create: `extra/sms/sms-gateway-app/modules/apk-installer/android/src/main/res/xml/apk_paths.xml`
- Create: `extra/sms/sms-gateway-app/modules/apk-installer/android/src/main/java/expo/modules/apkinstaller/ApkInstallerFileProvider.kt`
- Create: `extra/sms/sms-gateway-app/modules/apk-installer/android/src/main/java/expo/modules/apkinstaller/ApkInstallerModule.kt`

- [ ] **Step 1: Create module config files**

Create `extra/sms/sms-gateway-app/modules/apk-installer/expo-module.config.json`:

```json
{
  "platforms": ["android"],
  "android": {
    "modules": ["expo.modules.apkinstaller.ApkInstallerModule"]
  }
}
```

Create `extra/sms/sms-gateway-app/modules/apk-installer/package.json`:

```json
{
  "name": "apk-installer",
  "version": "1.0.0",
  "main": "index.ts",
  "types": "index.ts",
  "expo": {
    "configPlugin": "./app.plugin.js"
  }
}
```

- [ ] **Step 2: Create Android build config and manifest**

Create `extra/sms/sms-gateway-app/modules/apk-installer/android/build.gradle`:

```groovy
apply plugin: 'com.android.library'
apply plugin: 'kotlin-android'

group = 'expo.modules.apkinstaller'
version = '1.0.0'

def expoModulesCorePlugin = new File(project(":expo-modules-core").projectDir, "ExpoModulesCorePlugin.gradle")
if (expoModulesCorePlugin.exists()) {
  apply from: expoModulesCorePlugin
  applyKotlinExpoModulesCorePlugin()
}

android {
  namespace "expo.modules.apkinstaller"
  compileSdkVersion safeExtGet("compileSdkVersion", 34)

  defaultConfig {
    minSdkVersion safeExtGet("minSdkVersion", 24)
    targetSdkVersion safeExtGet("targetSdkVersion", 34)
  }

  lintOptions {
    abortOnError false
  }
}

dependencies {
  implementation project(':expo-modules-core')
}
```

Create `extra/sms/sms-gateway-app/modules/apk-installer/android/src/main/AndroidManifest.xml`:

```xml
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
  <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES"/>
</manifest>
```

- [ ] **Step 3: Create FileProvider config and class**

Create `extra/sms/sms-gateway-app/modules/apk-installer/android/src/main/res/xml/apk_paths.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<paths>
    <cache-path name="apk_cache" path="apk_updates/" />
</paths>
```

Create `extra/sms/sms-gateway-app/modules/apk-installer/android/src/main/java/expo/modules/apkinstaller/ApkInstallerFileProvider.kt`:

```kotlin
package expo.modules.apkinstaller

import androidx.core.content.FileProvider

class ApkInstallerFileProvider : FileProvider()
```

- [ ] **Step 4: Create the Kotlin installer module**

Create `extra/sms/sms-gateway-app/modules/apk-installer/android/src/main/java/expo/modules/apkinstaller/ApkInstallerModule.kt`:

```kotlin
package expo.modules.apkinstaller

import android.content.Intent
import android.os.Build
import androidx.core.content.FileProvider
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import expo.modules.kotlin.Promise
import expo.modules.kotlin.exception.CodedException
import java.io.File

class ApkInstallerModule : Module() {
    override fun definition() = ModuleDefinition {
        Name("ApkInstaller")

        AsyncFunction("installApk") { filePath: String, promise: Promise ->
            val context = appContext.reactContext
            if (context == null) {
                promise.reject(CodedException("ERR_CONTEXT", "React context is null", null))
                return@AsyncFunction
            }

            try {
                val sourceFile = File(filePath)
                if (!sourceFile.exists()) {
                    promise.reject(CodedException("ERR_FILE", "APK file not found: $filePath", null))
                    return@AsyncFunction
                }

                val cacheDir = File(context.cacheDir, "apk_updates")
                cacheDir.mkdirs()
                val apkFile = File(cacheDir, "update.apk")
                sourceFile.copyTo(apkFile, overwrite = true)

                val authority = "${context.packageName}.apkinstaller"
                val uri = FileProvider.getUriForFile(context, authority, apkFile)

                val intent = Intent(Intent.ACTION_VIEW).apply {
                    setDataAndType(uri, "application/vnd.android.package-archive")
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }

                context.startActivity(intent)
                promise.resolve(true)
            } catch (e: Exception) {
                promise.reject(CodedException("ERR_INSTALL", "Failed to install APK: ${e.message}", e))
            }
        }
    }
}
```

- [ ] **Step 5: Commit**

```bash
git add extra/sms/sms-gateway-app/modules/apk-installer/
git commit -m "feat(sms-app): add apk-installer native module with FileProvider"
```

---

## Task 6: Android Config Plugin & App Registration

**Files:**
- Create: `extra/sms/sms-gateway-app/modules/apk-installer/app.plugin.js`
- Create: `extra/sms/sms-gateway-app/modules/apk-installer/index.ts`
- Modify: `extra/sms/sms-gateway-app/app.json`

- [ ] **Step 1: Create the Expo config plugin**

Create `extra/sms/sms-gateway-app/modules/apk-installer/app.plugin.js`:

```javascript
const { withAndroidManifest, AndroidConfig } = require("@expo/config-plugins");

function withApkInstaller(config) {
  config = AndroidConfig.Permissions.withPermissions(config, [
    "android.permission.REQUEST_INSTALL_PACKAGES",
  ]);

  config = withAndroidManifest(config, (config) => {
    const manifest = config.modResults;
    const application = manifest.manifest.application?.[0];
    if (!application) return config;

    const providerName = "expo.modules.apkinstaller.ApkInstallerFileProvider";
    const providers = application.provider || [];
    const exists = providers.some(
      (p) => p.$?.["android:name"] === providerName
    );

    if (!exists) {
      if (!application.provider) application.provider = [];
      application.provider.push({
        $: {
          "android:name": providerName,
          "android:authorities": "${applicationId}.apkinstaller",
          "android:exported": "false",
          "android:grantUriPermissions": "true",
        },
        "meta-data": [
          {
            $: {
              "android:name": "android.support.FILE_PROVIDER_PATHS",
              "android:resource": "@xml/apk_paths",
            },
          },
        ],
      });
    }

    return config;
  });

  return config;
}

module.exports = withApkInstaller;
```

- [ ] **Step 2: Create the JS interface**

Create `extra/sms/sms-gateway-app/modules/apk-installer/index.ts`:

```typescript
import { requireNativeModule, Platform } from "expo-modules-core";

interface ApkInstallerModule {
  installApk(filePath: string): Promise<boolean>;
}

const ApkInstaller: ApkInstallerModule | null =
  Platform.OS === "android" ? requireNativeModule("ApkInstaller") : null;

export async function installApk(filePath: string): Promise<boolean> {
  if (!ApkInstaller) {
    throw new Error("APK installer is only available on Android");
  }
  return ApkInstaller.installApk(filePath);
}

export default { installApk };
```

- [ ] **Step 3: Register the plugin in app.json**

In `extra/sms/sms-gateway-app/app.json`, add `"./modules/apk-installer/app.plugin.js"` to the `plugins` array:

```json
    "plugins": [
      "expo-router",
      "./modules/gateway-service/app.plugin.js",
      "./modules/apk-installer/app.plugin.js"
    ],
```

- [ ] **Step 4: Commit**

```bash
git add extra/sms/sms-gateway-app/modules/apk-installer/app.plugin.js extra/sms/sms-gateway-app/modules/apk-installer/index.ts extra/sms/sms-gateway-app/app.json
git commit -m "feat(sms-app): add apk-installer config plugin, JS interface, register in app.json"
```

---

## Task 7: App Types & API Client Changes

**Files:**
- Modify: `extra/sms/sms-gateway-app/src/types/index.ts`
- Modify: `extra/sms/sms-gateway-app/src/api/gatewayClient.ts`

- [ ] **Step 1: Add `AppUpdate` type and extend `HeartbeatResponse`**

In `extra/sms/sms-gateway-app/src/types/index.ts`, add the `AppUpdate` interface before the existing `HeartbeatResponse` interface (before line 32), and add `app_update` to `HeartbeatResponse`:

Add before `HeartbeatResponse`:

```typescript
export interface AppUpdate {
  available: boolean;
  version: string;
  version_code: number;
  force: boolean;
  release_notes: string;
  download_url: string;
  file_size: number;
}
```

Add `app_update` field to the `HeartbeatResponse` interface (after `phone_stats`):

```typescript
export interface HeartbeatResponse {
  success: boolean;
  pending_count: Record<string, number>;
  rate_limit: number;
  phone_stats?: Record<string, PhoneCounters>;
  app_update?: AppUpdate | null;
}
```

- [ ] **Step 2: Send `app_version` in heartbeat request**

In `extra/sms/sms-gateway-app/src/api/gatewayClient.ts`, add an import at the top of the file:

```typescript
import Constants from 'expo-constants';
```

Then modify the `heartbeat` method to include `app_version` in the request body. Change the method (around line 64-74) to:

```typescript
  async heartbeat(
    phoneNumbers: string[],
    batteryLevel?: number,
    signalStrength?: number
  ): Promise<HeartbeatResponse> {
    return this.request<HeartbeatResponse>('/sms-gateway/heartbeat', {
      phone_numbers: phoneNumbers,
      battery_level: batteryLevel,
      signal_strength: signalStrength,
      app_version: Constants.expoConfig?.android?.versionCode ?? 0,
    });
  }
```

- [ ] **Step 3: Commit**

```bash
git add extra/sms/sms-gateway-app/src/types/index.ts extra/sms/sms-gateway-app/src/api/gatewayClient.ts
git commit -m "feat(sms-app): add AppUpdate type, send app_version in heartbeat"
```

---

## Task 8: Update Service

**Files:**
- Create: `extra/sms/sms-gateway-app/src/services/updateService.ts`
- Modify: `extra/sms/sms-gateway-app/package.json` (add `expo-file-system`)

- [ ] **Step 1: Install expo-file-system**

```bash
cd extra/sms/sms-gateway-app && npx expo install expo-file-system
```

- [ ] **Step 2: Create the update service**

Create `extra/sms/sms-gateway-app/src/services/updateService.ts`:

```typescript
import * as FileSystem from 'expo-file-system';
import { onHeartbeat } from './heartbeatService';
import { getSettings } from '../storage/settings';
import { installApk } from '../../modules/apk-installer';
import { AppUpdate } from '../types';

let currentUpdate: AppUpdate | null = null;
let listeners: ((update: AppUpdate | null) => void)[] = [];
let downloading = false;
let downloadProgress = 0;
let progressListeners: ((progress: number) => void)[] = [];
let dismissed = false;
let unsubscribe: (() => void) | null = null;

function notifyListeners() {
  for (const listener of listeners) {
    listener(currentUpdate);
  }
}

function notifyProgress(progress: number) {
  downloadProgress = progress;
  for (const listener of progressListeners) {
    listener(progress);
  }
}

export function startUpdateService(): void {
  if (unsubscribe) return;
  unsubscribe = onHeartbeat((response) => {
    const update = response.app_update;
    if (update?.available) {
      if (!currentUpdate || currentUpdate.version_code !== update.version_code) {
        dismissed = false;
      }
      currentUpdate = update;
    } else {
      currentUpdate = null;
    }
    notifyListeners();
  });
}

export function stopUpdateService(): void {
  if (unsubscribe) {
    unsubscribe();
    unsubscribe = null;
  }
}

export function getAvailableUpdate(): AppUpdate | null {
  return currentUpdate;
}

export function isDownloading(): boolean {
  return downloading;
}

export function getDownloadProgress(): number {
  return downloadProgress;
}

export function isDismissed(): boolean {
  return dismissed;
}

export function dismissUpdate(): void {
  dismissed = true;
  notifyListeners();
}

export function onUpdateAvailable(callback: (update: AppUpdate | null) => void): () => void {
  listeners.push(callback);
  return () => {
    listeners = listeners.filter((l) => l !== callback);
  };
}

export function onDownloadProgress(callback: (progress: number) => void): () => void {
  progressListeners.push(callback);
  return () => {
    progressListeners = progressListeners.filter((l) => l !== callback);
  };
}

export async function downloadAndInstall(): Promise<void> {
  if (!currentUpdate || downloading) return;

  const settings = getSettings();
  const url = `${settings.apiUrl}${currentUpdate.download_url}`;

  downloading = true;
  notifyProgress(0);
  notifyListeners();

  try {
    const downloadDest = `${FileSystem.cacheDirectory}sms-gateway-update.apk`;

    const downloadResumable = FileSystem.createDownloadResumable(
      url,
      downloadDest,
      { headers: { 'X-API-Key': settings.apiKey } },
      (progress) => {
        const pct = progress.totalBytesExpectedToWrite > 0
          ? progress.totalBytesWritten / progress.totalBytesExpectedToWrite
          : 0;
        notifyProgress(pct);
      },
    );

    const result = await downloadResumable.downloadAsync();
    if (!result?.uri) {
      throw new Error('Download failed — no file returned');
    }

    notifyProgress(1);
    await installApk(result.uri.replace('file://', ''));
  } finally {
    downloading = false;
    notifyListeners();
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add extra/sms/sms-gateway-app/src/services/updateService.ts extra/sms/sms-gateway-app/package.json
git commit -m "feat(sms-app): add updateService with download + install orchestration"
```

---

## Task 9: Force Update Overlay in Layout

**Files:**
- Modify: `extra/sms/sms-gateway-app/app/_layout.tsx`

- [ ] **Step 1: Add force update overlay**

In `extra/sms/sms-gateway-app/app/_layout.tsx`, add the following imports at the top (alongside existing imports):

```typescript
import { TouchableOpacity, StyleSheet } from 'react-native';
import {
  startUpdateService,
  stopUpdateService,
  onUpdateAvailable,
  onDownloadProgress,
  downloadAndInstall,
  isDownloading,
  getAvailableUpdate,
} from '../src/services/updateService';
import { AppUpdate } from '../src/types';
```

In the `AppLayout` component, add state and effects for the force update overlay. Add after the `servicesInitialized` ref (after line 56):

```typescript
  const [forceUpdate, setForceUpdate] = useState<AppUpdate | null>(null);
  const [dlProgress, setDlProgress] = useState(0);
  const [dlActive, setDlActive] = useState(false);
```

In the `initializeServices` async function (inside the `useEffect` at line 76), add after `startInboundSmsListener();` (after line 91):

```typescript
          startUpdateService();
```

In the cleanup return of that same `useEffect` (around line 101-104), add:

```typescript
      stopUpdateService();
```

Add a new `useEffect` after the services `useEffect` block for listening to force updates:

```typescript
  useEffect(() => {
    const unsubUpdate = onUpdateAvailable((update) => {
      if (update?.force) {
        setForceUpdate(update);
      } else {
        setForceUpdate(null);
      }
    });
    const unsubProgress = onDownloadProgress((p) => {
      setDlProgress(p);
      setDlActive(isDownloading());
    });
    return () => {
      unsubUpdate();
      unsubProgress();
    };
  }, []);
```

Finally, render the force update overlay inside the returned JSX, just before the closing `</View>` of the root wrapper (before the closing `</SafeAreaProvider>`):

```typescript
        {forceUpdate && (
          <View style={forceStyles.overlay}>
            <View style={forceStyles.card}>
              <Text style={forceStyles.title}>Vyžadována aktualizace</Text>
              <Text style={forceStyles.version}>Verze {forceUpdate.version}</Text>
              {forceUpdate.release_notes ? (
                <Text style={forceStyles.notes}>{forceUpdate.release_notes}</Text>
              ) : null}
              {dlActive ? (
                <View style={forceStyles.progressContainer}>
                  <View style={forceStyles.progressBar}>
                    <View style={[forceStyles.progressFill, { width: `${Math.round(dlProgress * 100)}%` }]} />
                  </View>
                  <Text style={forceStyles.progressText}>{Math.round(dlProgress * 100)} %</Text>
                </View>
              ) : (
                <TouchableOpacity style={forceStyles.button} onPress={downloadAndInstall}>
                  <Text style={forceStyles.buttonText}>Aktualizovat nyní</Text>
                </TouchableOpacity>
              )}
            </View>
          </View>
        )}
```

Add the styles at the bottom of the file (outside the component):

```typescript
const forceStyles = StyleSheet.create({
  overlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.85)',
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 9999,
  },
  card: {
    backgroundColor: '#1F2937',
    borderRadius: 16,
    padding: 28,
    marginHorizontal: 24,
    width: '85%',
    alignItems: 'center',
  },
  title: {
    color: '#F9FAFB',
    fontSize: 20,
    fontWeight: 'bold',
    marginBottom: 8,
  },
  version: {
    color: '#9CA3AF',
    fontSize: 14,
    marginBottom: 16,
  },
  notes: {
    color: '#D1D5DB',
    fontSize: 14,
    lineHeight: 20,
    textAlign: 'center',
    marginBottom: 20,
  },
  button: {
    backgroundColor: '#2563EB',
    paddingVertical: 14,
    paddingHorizontal: 32,
    borderRadius: 10,
    width: '100%',
    alignItems: 'center',
  },
  buttonText: {
    color: '#FFF',
    fontSize: 16,
    fontWeight: '600',
  },
  progressContainer: {
    width: '100%',
    alignItems: 'center',
    gap: 8,
  },
  progressBar: {
    height: 8,
    backgroundColor: '#374151',
    borderRadius: 4,
    width: '100%',
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: '#3B82F6',
    borderRadius: 4,
  },
  progressText: {
    color: '#9CA3AF',
    fontSize: 13,
  },
});
```

- [ ] **Step 2: Commit**

```bash
git add extra/sms/sms-gateway-app/app/_layout.tsx
git commit -m "feat(sms-app): add force-update fullscreen overlay in root layout"
```

---

## Task 10: Update Banner on Dashboard

**Files:**
- Modify: `extra/sms/sms-gateway-app/app/(tabs)/dashboard.tsx`

- [ ] **Step 1: Add update banner to dashboard**

In `extra/sms/sms-gateway-app/app/(tabs)/dashboard.tsx`, add imports at the top (with existing imports):

```typescript
import {
  onUpdateAvailable,
  onDownloadProgress,
  downloadAndInstall,
  isDownloading,
  isDismissed,
  dismissUpdate,
} from '../../src/services/updateService';
import { AppUpdate } from '../../src/types';
```

Add state variables inside `DashboardScreen` (after the existing `useState` declarations, around line 47):

```typescript
  const [appUpdate, setAppUpdate] = useState<AppUpdate | null>(null);
  const [updateDismissed, setUpdateDismissed] = useState(false);
  const [updateDlProgress, setUpdateDlProgress] = useState(0);
  const [updateDlActive, setUpdateDlActive] = useState(false);
```

Add a new `useEffect` for subscribing to update events (after the existing `useEffect` blocks, e.g. after the `fetchStats` interval effect):

```typescript
  useEffect(() => {
    const unsubUpdate = onUpdateAvailable((update) => {
      setAppUpdate(update);
      if (update && !update.force) {
        setUpdateDismissed(isDismissed());
      }
    });
    const unsubProgress = onDownloadProgress((p) => {
      setUpdateDlProgress(p);
      setUpdateDlActive(isDownloading());
    });
    return () => {
      unsubUpdate();
      unsubProgress();
    };
  }, []);
```

Add the banner JSX inside the `ScrollView`, right after the `{error && (...)}` block and before the `{/* Global Summary */}` comment (around line 200):

```typescript
      {appUpdate && !appUpdate.force && !updateDismissed && (
        <View style={styles.updateBanner}>
          {updateDlActive ? (
            <>
              <View style={styles.updateProgressBar}>
                <View style={[styles.updateProgressFill, { width: `${Math.round(updateDlProgress * 100)}%` }]} />
              </View>
              <Text style={styles.updateText}>Stahování… {Math.round(updateDlProgress * 100)} %</Text>
            </>
          ) : (
            <>
              <View style={{ flex: 1 }}>
                <Text style={styles.updateText}>Dostupná verze {appUpdate.version}</Text>
              </View>
              <TouchableOpacity
                style={styles.updateButton}
                onPress={downloadAndInstall}
              >
                <Text style={styles.updateButtonText}>Aktualizovat</Text>
              </TouchableOpacity>
              <TouchableOpacity
                onPress={() => { dismissUpdate(); setUpdateDismissed(true); }}
                style={{ paddingHorizontal: 8 }}
              >
                <Ionicons name="close" size={18} color="#9CA3AF" />
              </TouchableOpacity>
            </>
          )}
        </View>
      )}
```

Add the corresponding styles to the `StyleSheet.create({...})` at the bottom:

```typescript
  updateBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    marginHorizontal: 16,
    marginBottom: 16,
    padding: 12,
    backgroundColor: '#1E3A5F',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#2563EB',
  },
  updateText: {
    color: '#93C5FD',
    fontSize: 14,
    fontWeight: '500',
  },
  updateButton: {
    backgroundColor: '#2563EB',
    paddingVertical: 6,
    paddingHorizontal: 14,
    borderRadius: 6,
    marginLeft: 8,
  },
  updateButtonText: {
    color: '#FFF',
    fontSize: 13,
    fontWeight: '600',
  },
  updateProgressBar: {
    flex: 1,
    height: 6,
    backgroundColor: '#374151',
    borderRadius: 3,
    overflow: 'hidden',
    marginRight: 10,
  },
  updateProgressFill: {
    height: '100%',
    backgroundColor: '#3B82F6',
    borderRadius: 3,
  },
```

- [ ] **Step 2: Commit**

```bash
git add extra/sms/sms-gateway-app/app/\(tabs\)/dashboard.tsx
git commit -m "feat(sms-app): add update banner on dashboard with download progress"
```

---

## Task 11: Build Verification

- [ ] **Step 1: Run Expo prebuild to verify native config**

```bash
cd extra/sms/sms-gateway-app && npx expo prebuild --platform android --clean 2>&1 | tail -20
```

Expected: Prebuild completes with no errors. The generated `android/app/src/main/AndroidManifest.xml` should contain:
- `<uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES"/>`
- `<provider android:name="expo.modules.apkinstaller.ApkInstallerFileProvider" ...>`

- [ ] **Step 2: Run TypeScript type check**

```bash
cd extra/sms/sms-gateway-app && npx tsc --noEmit 2>&1 | head -30
```

Expected: No type errors related to the new code.

- [ ] **Step 3: Build APK to verify native compilation**

```bash
cd extra/sms/sms-gateway-app && cd android && ./gradlew assembleRelease 2>&1 | tail -20
```

Expected: `BUILD SUCCESSFUL`. The APK at `android/app/build/outputs/apk/release/app-release.apk` is produced.

- [ ] **Step 4: Commit any prebuild-generated changes**

```bash
git add extra/sms/sms-gateway-app/android/
git commit -m "build(sms-app): prebuild with apk-installer native module"
```
