# SMS Gateway for Odoo 18

Send SMS through physical Android phones instead of Odoo IAP. Supports multiple phones with load balancing, rate limiting, domain filtering, dual SIM, and integration with mass mailing campaigns.

**[User Guide](https://varyshop.github.io/sms-gateway/)** | **[Developer & API Docs](https://varyshop.github.io/sms-gateway/developer.html)** | **[Download APK](https://github.com/Varyshop/sms-gateway-app/releases)**

## Architecture

```
┌──────────────┐       REST API        ┌─────────────────┐
│  Odoo 18     │◄─────────────────────►│  Android App    │
│  sms_gateway │  heartbeat / pending  │  (React Native) │
│  module      │  confirm / inbound    │  SMS Gateway    │
└──────┬───────┘                       └─────────────────┘
       │                                      ▲
       │  FCM push (data-only)                │
       │  via Google Firebase                 │  wakes app instantly
       ▼                                      │
┌──────────────┐  push: sms_pending    ┌──────┴──────────┐
│  FCM         │──────────────────────►│  FCM SDK        │
│  (Google)    │  wake-up signal       │  on device      │
└──────────────┘                       └─────────────────┘
       │                                      │
       │  assigns SMS to phone queue          │  sends via native
       │  tracks limits & segments            │  SmsManager API
       │                                      │
       ▼                                      ▼
  sms.gateway.phone                    Physical SIM cards
  (load balancer)                      (Dual SIM support)
```

When FCM push is configured, Odoo sends a data-only FCM message immediately after assigning SMS to a phone queue. The app wakes up, fetches pending messages via REST API, and sends them. If FCM is not configured, the app falls back to 5-minute interval polling.

### Components

| Directory | Description |
|-----------|-------------|
| `sms_modules/sms_gateway/` | Odoo 18 module — replaces IAP SMS provider |
| `sms_modules/sms_gateway_docs/` | Odoo module with HTML documentation |
| `sms-gateway-app/` | React Native (Expo) Android app (git submodule) |

## Installation

### Prerequisites

- Odoo 18 Community or Enterprise
- Python package: `qrcode` (`pip install qrcode[pil]`)
- Python package (optional): `firebase-admin` (`pip install firebase-admin`) — required for FCM push notifications; auto-installed on Odoo startup if FCM is enabled and the package is missing
- Android phone with SIM card(s)

### 1. Install the Odoo Module

```bash
# Clone with submodules
git clone --recurse-submodules git@github.com:Varyshop/sms-gateway.git

# Or if already cloned without submodules
git submodule update --init --recursive
```

Copy or symlink `sms_modules/sms_gateway` into your Odoo addons path:

```bash
ln -s /path/to/sms-gateway/sms_modules/sms_gateway /path/to/odoo/addons/sms_gateway
```

Update Odoo module list and install **SMS Gateway**:

```
Settings → Apps → Update Apps List → Search "SMS Gateway" → Install
```

**Dependencies** (auto-installed): `base_setup`, `sms`, `mass_mailing_sms`, `phone_validation`

### 2. Register a Gateway Phone

1. Go to **Settings → SMS Gateway → Manage Gateway Phones**
2. Click **New**
3. Fill in:
   - **Name**: descriptive label (e.g. "Samsung A54 – SIM1 O2")
   - **Phone Number**: primary number in international format (`+420123456789`)
   - **Phone Number 2**: optional, for dual SIM devices
   - **Daily Limit**: max SMS per day (default: 500)
   - **Monthly Limit**: max SMS per billing period (0 = unlimited)
   - **Billing Period Start Day**: day of month when monthly counter resets (1–28)
   - **SMS per Minute**: rate limit for the queue (default: 100)
   - **Partner Domain Filter**: optional Odoo domain, e.g. `[("category_id", "in", [10])]`
4. Click **Generate API Key**
5. The **QR Code** will appear — scan it with the mobile app to pair

### 3. Install the Mobile App

#### Download the APK (recommended)

Go to [Releases](https://github.com/Varyshop/sms-gateway-app/releases) on GitHub and download the latest `.apk` file directly to your Android phone.

On the phone:
1. Open the downloaded `.apk` file
2. If prompted, enable **"Install from unknown sources"** for your browser/file manager
3. Complete the installation

> **Note:** The app is not published on Google Play. It uses `SEND_SMS` and `READ_SMS` permissions which require special review on the Play Store. Sideloading the APK is the intended distribution method.

#### Build from source

```bash
cd sms-gateway-app
npm install  # or yarn
npx expo prebuild
npx expo run:android        # debug build on connected device
```

To build a release APK:

```bash
eas build --profile production-apk --platform android
```

The built APK can be uploaded as a GitHub Release:

```bash
gh release create v1.0.0 ./build/*.apk \
  --repo Varyshop/sms-gateway-app \
  --title "v1.0.0" \
  --notes "Initial release"
```

#### First launch

On first launch:
1. **Notification permission** — on Android 13+ the app requests `POST_NOTIFICATIONS` permission (required to show the foreground service notification)
2. **Battery optimization exemption** — the app requests to be excluded from battery optimization (ensures AlarmManager wake-ups are not deferred by Doze mode)
3. **Scan QR code** — scan the QR code from the Odoo phone record to pair with the Odoo instance
4. **FCM token auto-registration** — after pairing, the app automatically registers its Firebase Cloud Messaging token with Odoo via `/sms-gateway/register-fcm` for instant push notifications

Once paired, the app will:
- Receive FCM push notifications when new SMS are queued (instant wake-up)
- Fall back to 5-minute polling if FCM is not configured on the server
- Send heartbeats to report battery and signal status
- Send SMS via the native Android SmsManager
- Report delivery status back to Odoo
- Forward inbound SMS containing "STOP" to trigger blacklisting

### 4. Configure SMS Provider on Campaigns

By default, the module intercepts SMS only when `sms_provider = 'gateway'` is set. There are two ways to route SMS through the gateway:

**A) Automatic via Mass Mailing campaign:**

In the mailing form, set the **SMS Provider** field to **SMS Gateway**. All SMS generated by that campaign will be routed through gateway phones.

**B) Manual via "Send with Gateway" wizard:**

1. Go to **SMS → SMS Messages** (list view)
2. Select SMS records (state: outgoing or error)
3. Click **Action → Send with Gateway**
4. Select which SIM numbers to use
5. Review capacity and click **Send**

## How It Works

### SMS Lifecycle

```
outgoing → [_send()] → pending → processing → sending → sent
                                                      → error
```

1. **outgoing**: SMS created by Odoo (campaign or manual)
2. **pending**: assigned to a gateway phone queue
3. **processing**: picked up by mobile app (`/sms-gateway/pending`)
4. **sending**: app started sending via SmsManager
5. **sent** / **error**: app confirms via `/sms-gateway/confirm/<id>`

### Load Balancing

When no specific phone is pre-assigned, the module automatically selects the **least-loaded online phone** that:
- Has remaining daily capacity
- Has remaining monthly capacity
- Passes the domain filter for the recipient's partner record

### Segment Counting

SMS billing depends on message length and character set:

| Encoding | Single SMS | Multi-part segment |
|----------|-----------|-------------------|
| GSM-7 (ASCII, basic Latin) | ≤ 160 chars | 153 chars/segment |
| Unicode (diacritics, emoji) | ≤ 70 chars | 67 chars/segment |

All limits (daily, monthly) count **segments**, not messages. A 300-character Czech SMS with diacritics = ceil(300/67) = **5 segments**.

### Unsubscribe / STOP

Outgoing SMS replaces the long unsubscribe URL with a short notice:
```
odhl. sms: STOP
```

When the app receives an inbound SMS containing "STOP", the sender's number is added to `phone.blacklist` (GDPR compliance). If the sender matches a partner, a chatter note is posted.

### Domain Filtering

Each gateway phone can have a **Partner Domain Filter** — an Odoo domain expression that restricts which SMS it handles. Example:

```python
[("category_id", "in", [10])]  # Only partners with tag ID 10
```

This is useful when different phones/SIMs belong to different companies or business units.

### Dual SIM

Each phone record supports two numbers. The **Send with Gateway** wizard shows each SIM as a separate line, but capacity is shared per device. The `gateway_sim_number` field on `sms.sms` tells the app which SIM to use.

## API Endpoints

All endpoints use `POST` with JSON body. Authentication via `X-API-Key` header.

| Endpoint | Description |
|----------|-------------|
| `/sms-gateway/heartbeat` | Phone sends heartbeat (battery, signal) |
| `/sms-gateway/pending` | Phone fetches pending SMS from queue |
| `/sms-gateway/confirm/<id>` | Phone reports SMS status (sending/sent/error) |
| `/sms-gateway/confirm-batch` | Phone reports status for multiple SMS in one request |
| `/sms-gateway/inbound` | Phone forwards received SMS (STOP detection) |
| `/sms-gateway/register-fcm` | Phone registers its FCM token for push notifications |
| `/sms-gateway/stats` | Phone requests its statistics |

### Example: Heartbeat

```bash
curl -X POST https://your-odoo.com/sms-gateway/heartbeat \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"battery_level": 85, "signal_strength": -67}'
```

Response:
```json
{
  "success": true,
  "pending_count": {"420123456789": 15},
  "rate_limit": 100
}
```

### Example: Fetch Pending SMS

```bash
curl -X POST https://your-odoo.com/sms-gateway/pending \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"limit": 20}'
```

Response:
```json
{
  "success": true,
  "sms_list": [
    {
      "id": 42,
      "phone_number": "+420987654321",
      "message": "Your order is ready!",
      "uuid": "abc-123",
      "gateway_phone_number": "+420123456789"
    }
  ]
}
```

### Example: Confirm Status

```bash
curl -X POST https://your-odoo.com/sms-gateway/confirm/42 \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"status": "sent"}'
```

## Cron Jobs

| Cron | Interval | Description |
|------|----------|-------------|
| Check Heartbeat Timeout | 5 min | Marks phones offline if no heartbeat |
| Reset Daily Counters | Daily (midnight) | Resets `sent_today` on all phones |
| Reset Monthly Counters | Daily | Resets `sent_month` when billing period starts |

## Troubleshooting

**Phone shows "Offline"**
- Check that the app is running and connected to the internet
- Verify the API key matches
- Check heartbeat timeout setting (default: 5 min)

**SMS stuck in "pending"**
- Ensure the phone is online (heartbeat active)
- Check daily/monthly limits haven't been exceeded
- Look at Odoo server logs for gateway errors

**Campaign not completing**
- All SMS must reach sent/error state for the campaign to mark as "done"
- Check for paused mailings
- Verify no SMS are stuck in processing state

**Segments counted incorrectly**
- Czech diacritics (č, ř, ž, etc.) force Unicode encoding
- A 160-char Czech message uses 3 segments, not 1
- Check `sms_gateway/tools/sms_utils.py` for the exact algorithm

## FCM Push Notifications (Event-Driven)

The gateway uses **Firebase Cloud Messaging (FCM)** to deliver instant wake-up signals to the Android app when new SMS are queued. This replaces the old interval-based polling with a push-first, poll-as-fallback architecture.

### How It Works

```
┌──────────┐   1. SMS queued    ┌──────────┐   2. data push   ┌─────────┐
│  Odoo    │ ─────────────────► │  FCM     │ ────────────────► │  App    │
│  module  │                    │  (Google)│                   │         │
└──────────┘                    └──────────┘                   └────┬────┘
      ▲                                                             │
      └────────── 3. POST /pending ────── 4. send via SmsManager ───┘
```

1. Odoo assigns SMS to a phone queue via `_send()`
2. Odoo sends a **data-only** FCM message (no visible notification, just a wake-up signal) with payload: `{"type": "sms_pending", "count": "15"}`
3. The app wakes up immediately (even from background/killed state) and calls `/sms-gateway/pending`
4. The app sends the SMS and confirms delivery back to Odoo

### Key Design Decisions

- **One Firebase project** is shared across all Odoo instances. The app does not know which Odoo server will send it a push — it pairs to a specific instance via QR code at setup time.
- **FCM token registration**: after pairing, the app sends its FCM token to Odoo via `POST /sms-gateway/register-fcm`. The token is stored on the `sms.gateway.phone` record.
- **Data-only messages**: FCM messages carry no notification payload — they silently wake the app to trigger a poll cycle. This avoids user-visible push notifications for internal operations.
- **Fallback to 5-minute polling**: if FCM is not configured on the server (no credentials), the app automatically falls back to polling every 5 minutes.
- **Heartbeat safety net**: even with FCM enabled, if `pending_count > 0` and the last poll was too long ago, the heartbeat response triggers an immediate poll. This catches edge cases where an FCM message was lost.
- **`firebase-admin` auto-install**: the Odoo module attempts to install `firebase-admin` via pip on startup if FCM is enabled and the package is missing. This is logged as a warning — in production, pre-install the package in your Docker image or virtualenv.

### Token Lifecycle

| Event | Action |
|-------|--------|
| App pairs via QR code | FCM token sent to `/sms-gateway/register-fcm` |
| FCM token refresh (automatic by Firebase SDK) | App re-sends new token to `/sms-gateway/register-fcm` |
| Phone record deleted in Odoo | Token becomes orphaned (harmless, FCM will eventually expire it) |
| App uninstalled | Token invalidated by Google; Odoo FCM send will fail silently |

## Odoo FCM Configuration

### 1. Create a Firebase Project

1. Go to [Firebase Console](https://console.firebase.google.com/)
2. Create a new project (or use an existing one)
3. No need to add an Android app in the console — the app already has Firebase configured with its own `google-services.json`

### 2. Get a Service Account Key

1. In Firebase Console, go to **Project Settings → Service Accounts**
2. Click **Generate new private key**
3. Download the JSON file

### 3. Configure in Odoo

Go to **Settings → SMS Gateway** (or **Settings → Technical → SMS Gateway Settings**):

- **FCM Push Enabled**: check this box to enable push notifications
- **FCM Credentials JSON**: paste the entire contents of the service account JSON file here (inline). This is the preferred method — no file path management needed.
- **FCM Credentials Path**: alternatively, provide an absolute path to the service account JSON file on the server filesystem (e.g. `/etc/odoo/firebase-sa.json`). Use this if you prefer not to store credentials in the database.

> **Note:** If both are provided, the inline JSON takes precedence over the file path.

### 4. Verify

After saving, check the Odoo server log for:
```
INFO: FCM initialized successfully for project: your-project-id
```

Send a test SMS — the app should receive it within 1-2 seconds instead of waiting for the next poll cycle.

## Background Execution (Android)

The app uses several Android mechanisms to ensure reliable operation even when the device is in Doze mode, the app is in the background, or the manufacturer applies aggressive battery optimization (e.g. MIUI/Xiaomi, Samsung, Huawei).

### AlarmManager (Poll + Heartbeat)

- Uses `AlarmManager.setExactAndAllowWhileIdle()` for both poll and heartbeat alarms
- This API is allowed to fire during Doze mode idle windows, ensuring the app wakes up even on heavily optimized devices (Xiaomi MIUI, Samsung OneUI)
- Alarms reschedule themselves after each execution to maintain the cycle

### WorkManager (Inbound SMS Reporting)

- Inbound SMS (STOP keyword detection) is reported to Odoo via `WorkManager` enqueued work
- WorkManager guarantees delivery even if the app is killed between receiving the SMS and completing the HTTP request
- Uses `OneTimeWorkRequest` with network constraint to ensure connectivity

### Notification Channel

- The foreground service uses an `IMPORTANCE_HIGH` notification channel
- This prevents Android from silently demoting the service or hiding its notification
- The persistent notification shows gateway status (connected, phone number, pending count)

### Battery Optimization Exemption

- On first launch, the app requests exclusion from battery optimization via `ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`
- This is critical for reliable AlarmManager wake-ups on all Android manufacturers
- Without this exemption, some OEMs (Xiaomi, Huawei, Oppo) will kill the app within minutes of going to background

### Permissions

| Permission | Required | Purpose |
|-----------|---------|---------|
| `SEND_SMS` | Yes | Send SMS via SmsManager |
| `RECEIVE_SMS` | Yes | Detect inbound STOP messages |
| `POST_NOTIFICATIONS` | Android 13+ | Show foreground service notification |
| `SCHEDULE_EXACT_ALARM` | Android 12+ | Exact AlarmManager scheduling |
| `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` | Yes | Request Doze exemption |

### WakeLock Usage

- `FcmMessageHandler`: acquires a partial WakeLock when an FCM data message arrives to ensure the poll-and-send cycle completes before the CPU goes back to sleep
- `SmsBroadcastReceiver`: acquires a partial WakeLock while processing an inbound SMS broadcast, released after the WorkManager task is enqueued

## Contact

V případě jakýchkoli dotazů nebo potřebné pomoci s nastavením kontaktujte [info@varyshop.eu](mailto:info@varyshop.eu) nebo přímo vývojáře [info@michalvarys.eu](mailto:info@michalvarys.eu)

## License

LGPL-3
