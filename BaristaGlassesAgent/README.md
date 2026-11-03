# Barista glasses bridge — local iPhone recordings

iOS companion app for CaféPilot (Meta Global AI Developer Hackathon). It connects to Meta glasses,
takes photos and short clips, and sends photos to the CaféPilot Mac receiver. All cafés, suppliers,
orders and documents in the demo are fictional; the data is synthetic and only for the hackathon demo.

**What it calls.** One endpoint: `POST /api/upload` on the Mac receiver, found over Bonjour
(`_baristaphoto._tcp`). Each request is a raw JPEG with `Authorization: Bearer <PHOTO_SYNC_TOKEN>`,
`X-Capture-ID` and `X-Captured-At`; the two photos of a delivery check also carry `X-Delivery-ID` and
`X-Image-Role: receipt | delivery`. The app never calls the validation itself: the Mac's folder
monitor runs it once both photos have arrived (see *Paired receipt and delivery validation*).

**Needs `Configuration/Local.xcconfig`.** Copy `Configuration/Local.xcconfig.example` to
`Configuration/Local.xcconfig` and fill it in (team ID, and `PHOTO_SYNC_TOKEN` = `MEDIA_UPLOAD_TOKEN`
from `business-core/.env`). The file is ignored by Git; never commit it.

The current checkpoint is **Stage 4: local video recording**. Start the glasses session and preview. Tap Take Photo to save a JPEG in Documents/Photos, or tap Record 15 Seconds for a 3-second countdown followed by a 15-second MP4 capture. Stop Early saves a partial clip. Photos appear in the app gallery and are stored in Documents/Photos. Clips are listed for in-app playback and stored in Documents/Recordings on the iPhone. Each stage waits for approval before the next starts.

The intended recording destination is `Documents/Recordings` on the iPhone, visible in **Files → On My iPhone → Barista → Recordings**. File sharing is enabled and the folder is created now. Recordings stay on the iPhone; there is no cloud upload, backend URL or HTTPS tunnel. Photos go only to the Mac on the same Wi-Fi (below). Existing files from an earlier MediaOutbox are left untouched. The separate backend/gallery demo is documented in `../business-core/docs/video-demo-stage1.md`.

## Manual setup

1. Pair the glasses with the **Meta AI app**, check that they are connected, and enable **Developer Mode** in that app's glasses settings. Keep the glasses powered on, unfolded and worn. A Wearables Developer Center project is not required for this Developer Mode demo.
2. Open `BaristaGlassesAgent.xcodeproj`. Select the BaristaGlassesAgent scheme, **Debug**, and your connected iPhone. In Signing & Capabilities select your Apple team and a bundle identifier provisionable by that team. Enable iPhone Developer Mode and trust the developer if iOS requests it.
3. Required: copy `Configuration/Local.xcconfig.example` to `Configuration/Local.xcconfig` (ignored by Git). Fill in `DEVELOPMENT_TEAM` with your Apple team ID. For glasses Developer Mode, use `META_APP_ID = 0` and leave `CLIENT_TOKEN` empty. Set `PHOTO_SYNC_TOKEN` to the receiver's `MEDIA_UPLOAD_TOKEN`. Never commit a real client token. CLI builds can load this file with `-xcconfig Configuration/Local.xcconfig`; for Xcode GUI builds, set it as the target's configuration file if you need its overrides. The project already has Developer Mode defaults.
4. Build and run. Keep Barista in the foreground. Tap **Register with Meta AI**, complete the approval in Meta AI, and return to Barista. Tap **Allow Glasses Camera**, approve the glasses camera permission, then tap **Start Glasses Session**. If registration and permission were already granted, those checklist items should become green without repeating approval. Refresh rechecks their current state.
5. The readiness checklist covers SDK, registration, connection, permission and session. Start/stop preview separately after session readiness. **End Session** stops cleanly. A paused session waits for the glasses to resume; the app does not replace it. Backgrounding stops the session; start it explicitly after returning. Folded/disconnected glasses require reconnection and a new session.

### Signing and Wi-Fi constraint

The currently selected Personal Team cannot provision Apple's **Hotspot Configuration** entitlement. Debug therefore uses `Configuration/BluetoothOnly.entitlements` and clearly labels itself Bluetooth-only. This allows the control/session setup to build and sign; **Wi-Fi camera transport and real-glasses preview have not been verified with this team**.

The full entitlement remains in `BaristaGlassesAgent/BaristaGlassesAgent.entitlements`. Later Wi-Fi streaming requires a team that can provision it. With such a team, the local configuration can override `CODE_SIGN_ENTITLEMENTS` to that file and `WIFI_STREAMING_CONFIGURED = YES`. The Bonjour service and local-network usage text are already configured. Do not enable the entitlement on the current Personal Team: signing fails.

For a future attested Developer Center configuration, enter the project's real Meta app ID/client token locally and follow Meta's production callback requirements. The custom `baristaglassesagent://` callback here is the Developer Mode path. Voice, Speech, Inputs and standalone Photo capture are out of scope.

## Build and verify

From this directory, with Xcode installed at `/Applications/Xcode.app`:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild \
  -project BaristaGlassesAgent.xcodeproj -scheme BaristaGlassesAgent \
  -configuration Debug -xcconfig Configuration/Local.xcconfig \
  -destination 'platform=iOS,id=YOUR_IPHONE_UDID' build
```

The simulator uses Meta's **MockDeviceKit** by default; the physical iPhone uses real glasses. An orange MOCK GLASSES banner distinguishes the mock. Debug launch arguments `--use-mock-glasses` or `--use-real-glasses` override the default. No mock success is proof of hardware connectivity.

Boot an iOS simulator, then run the repeatable SDK check:

```bash
python3 Scripts/test-stage2.py
```

It builds and installs the Debug simulator app, runs actual SDK mock registration, permissions and session transitions, and writes `Documents/Diagnostics/stage2-smoke-test.json`. Ten checks cover configuration, pre-registration gating, registration, denied permission, granting permission, duplicate start protection, pause retention, resume, disconnect/reconnect, and foreground/background handling. It creates no recordings and makes no application media uploads. The SDK may still perform its own registration/diagnostic networking.

## Current confidence

- **Verified:** simulator build and all ten SDK mock checks; file-sharing configuration; signed Bluetooth-only Debug device build and installation on the connected iPhone.
- **Needs hardware confirmation:** registration, permission and session readiness with your connected glasses. The mock does not test Bluetooth radio connectivity.
- **Verified:** Stage 3 simulator build. **Still needs a hardware check:** live glasses preview and actual frame size/fps. **Verified:** Stage 4 device build and a synthetic MP4 recorder smoke check. **Needs hardware confirmation:** real glasses stream, clip resolution, duration and file size.
- **Known setup limitation:** Wi-Fi entitlement provisioning is unavailable on the selected Personal Team. No new dependency beyond the existing Meta SDK was added.

References: [DAT iOS SDK](https://github.com/meta/meta-wearables-dat-ios), [iOS integration](https://wearables.developer.meta.com/docs/develop/dat/build-integration-ios/), [session lifecycle](https://wearables.developer.meta.com/docs/develop/dat/lifecycle-events/), [MockDeviceKit](https://wearables.developer.meta.com/docs/develop/dat/testing-mdk-ios/).

## Automatic photo copy to the Mac over local Wi-Fi

The iPhone keeps every JPEG in `Documents/Photos` and queues a copy to the Mac
receiver. The first local-network discovery prompts iOS for permission; allow it.
The app discovers `_baristaphoto._tcp` and retries queued photos when the receiver is
available. Each gallery tile shows whether its Mac copy is pending or confirmed.

On the Mac, keep the receiver running from `business-core` with the project's Python
(`CAFEPILOT_PYTHON=.venv/bin/python ./scripts/run_local_photo_receiver.sh`). Put the
iPhone and Mac on the same non-guest Wi-Fi network. The Mac copy appears in
`~/Pictures/BaristaShared`; the backend also processes the accepted photo through
the existing photo reader. If macOS Firewall prompts, allow incoming connections
for the receiver. No router port forwarding, iCloud, or cloud bucket is used.
The local sync token is read from the ignored `Configuration/Local.xcconfig`; never
commit that file or copy the token into tracked source.

## Paired receipt and delivery validation

Tap **Start Delivery Check**, then capture the receipt when prompted. Barista tags
that JPEG as the receipt and automatically switches to the delivery photo for the
next capture. Both images keep their ordinary iPhone copies and sync to
`~/Pictures/BaristaShared/<delivery id>/` on the Mac. When both have arrived, the Mac's
folder monitor calls `POST /api/receiving/validate-pair`:

1. Muse Spark reads the packing slip; code finds the order by its PO number, so no order
   number is needed while taking the photos.
2. SAM 3.1 + Muse Spark count the delivery photo; code adds up the totals.
3. Code compares the order, the slip and the count, then decides:
   - **every line matches and the count is sure** → the delivery is confirmed automatically,
     stock goes up by what was counted, and the order is marked **Delivered**;
   - **short, over, damaged or unsure** → nothing changes in stock; the check waits for the
     owner's count or OK;
   - **PO not in the system** → everything is read and counted, nothing is confirmed, and
     "order not found" is logged.
