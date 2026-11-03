# Barista video demo — Stage 1

**Scope update:** the owner subsequently chose local-only iPhone recordings.
The backend/gallery below remains available for separately uploaded test clips;
the iOS app no longer uploads, needs no backend URL/token/tunnel, and will retain
future recordings in its own Documents/Recordings folder.

Stage 1 is backend and web only. No iOS code was changed in this stage.
Stages 2–5 wait for the owner's explicit “go ahead.” Voice, Speech, Inputs and
standalone Photo are outside this video demo's scope.

## Existing stack and processing

- Web: React 19, TypeScript, Vite. Gallery route: `/videos` (“Glasses clips”).
- Backend: FastAPI, SQLAlchemy/PostgreSQL; video ingestion itself needs no database migration.
- Existing analysis: `backend/modules/receiving/vision.py:read_photo` sends a JPEG
  to the configured Meta model, validates `PhotoRead`, and logs the model call.
  The video worker calls this same function, with the same prompt, for each frame.
- Existing iOS bridge: SwiftUI and Meta DAT 1.0.1. The requested 15-second recording
  and permanent Documents/Recordings behavior are future stages, not verified here.

The backend saves `uploads/videos/<UTC timestamp>_<capture UUID>.mp4` relative to
`business-core/`. Metadata (`.json`), results (`.result.json`), and extracted JPEGs
(`.frames/`) sit beside that MP4. `.intake/` contains the existing upload receipts
and a hard link to the same bytes for idempotent retries; it is not a second disk
copy. Keep it with the recordings. This entire uploads directory is ignored by Git.

## Run locally

The existing main backend on port 8001 and Vite on port 5173 are already running.
These are the exact commands to restart them, in separate terminals, if needed:

```bash
cd business-core
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001 --reload
```

```bash
cd business-core/frontend
npm run dev -- --host 127.0.0.1
```

Use the project's `.venv` (it has every requirement, including Pillow for the delivery
count).
`ffmpeg` and `ffprobe` were already installed on this Mac; no dependency was added.

The Videos page (`frontend/src/pages/VideosPage.tsx`) is not linked from the app's
navigation yet, and its `/api/videos` routes are served by the receiver app below, not by
port 8001. Once it is routed, open **http://127.0.0.1:5173/videos**. Enter the value of `MEDIA_UPLOAD_TOKEN` from
`business-core/.env`. The page keeps it in session storage and sends it in request
headers, never in playback URLs. `MODEL_API_KEY` stays on the backend and is not
the gallery/upload credential.

A narrow, authenticated media-only API is also running on port 8003:

```bash
cd business-core
.venv/bin/python -m uvicorn backend.media_demo:app --host 127.0.0.1 --port 8003
```

It shares storage and processing with the normal backend. It exposes media routes,
not the rest of the café's inventory/order APIs. Logs: `/tmp/cafepilot-video-stage1-service.log`.

## Test with curl

The sample used for verification is a 15-second video made by repeating the existing
delivery-photo fixture. It is labeled **Test clip · not from glasses** in the gallery.
It is not evidence of physical-glasses capture.

Generate another sample, if needed:

```bash
ffmpeg -nostdin -hide_banner -loglevel error -y -loop 1 \
  -i demo-assets/receiving/delivery-photo.jpg -t 15 -vf 'scale=360:-2' \
  -r 15 -an -c:v libx264 -pix_fmt yuv420p -movflags +faststart \
  /tmp/cafepilot-video-stage1-sample.mp4
```

Upload it with the curl helper (the helper keeps the secret out of command-line
arguments and logs):

```bash
PYTHONPATH=. .venv/bin/python scripts/try_video_upload.py \
  /tmp/cafepilot-video-stage1-sample.mp4 --base-url http://127.0.0.1:8003
```

This runs real Meta processing: a 15-second clip normally produces 15 image-reader
calls. It returns the upload acknowledgement immediately; watch the gallery for
processing progress. Preserve both `--capture-id` and `--captured-at` to retry a
specific upload without creating another clip.

## API and behavior

All endpoints require `Authorization: Bearer <MEDIA_UPLOAD_TOKEN>`.

| Endpoint | Behavior |
|---|---|
| `POST /api/upload-video` | Raw MP4 bytes; `Content-Type: video/mp4`; returns 201 with `id`, `url`, filename and processing state |
| `GET /api/videos` | Latest clips and processing results; the gallery polls every two seconds |
| `GET /api/videos/{id}` | One clip's metadata and processing progress |
| `GET /api/videos/{id}/content` | Authenticated MP4 download, including HTTP Range requests |
| `GET /api/videos/{id}/frames/{number}` | A sampled JPEG, numbered from zero |
| `POST /api/videos/{id}/process` | Retry unfinished/failed processing, reusing successful frame results |

Optional upload headers: `X-Capture-ID` (UUID), `X-Captured-At` (ISO timestamp with
offset), and `X-Capture-Source` (`test`, `mock-glasses`, `meta-glasses`). Stable IDs
and metadata make identical retries return 200; conflicting reuse returns 409.

Files are streamed to disk, checked for the MP4 container signature, hashed, and
published before processing. `ffprobe` checks the actual duration/dimensions;
`ffmpeg` samples approximately once per second. Defaults cap clips at 30 seconds,
30 frames and 100 MiB; the intended recording length is 15 seconds. The short-clip
fallback extracts one frame if 1-fps sampling rounds down to zero.

Each frame keeps its own Muse counts, confidence, notes and damage findings.
**Counts are never summed across frames**: the same carton may be seen repeatedly.
This stage does not confirm receipts or change inventory. Failed processing retains
the MP4 and available results; the gallery can request a retry. An interrupted
server process also needs a retry. A separate job queue was not added for this demo.

## HTTPS and the physical iPhone

`localhost` on an iPhone means the phone, not the Mac. For the physical-phone stage,
use a trusted HTTPS endpoint. A tunnel to the media-only server on port 8003 is the
simplest laptop setup. Neither ngrok nor cloudflared is installed here; installation
will require approval before adding that dependency. No tunnel was installed or
opened in Stage 1.

After approval and installation, an example is:

```bash
cloudflared tunnel --url http://127.0.0.1:8003
```

Use the printed HTTPS base URL in the future iOS configuration. The public side is
HTTPS even though its loopback origin is HTTP. Keep the bearer token enabled.
Quick-tunnel hostnames change on restart and have no uptime guarantee; the current
gallery uses polling, which does not depend on SSE support.
[Cloudflare Quick Tunnel documentation](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

## Verified / remaining

Verified: curl upload of a 15.0-second MP4, storage under `uploads/videos`, 15 JPEG
frames, all 15 read by the existing live `muse-spark-1.3` pipeline, authenticated
playback and Range requests, and actual browser playback at the sample's measured
360×480 dimensions. The browser showed processing progress through polling.

Automated verification: 21 backend tests (real MP4 decoding with mocked model calls),
four gallery tests, and a successful TypeScript/Vite build. The Intel build needed
the already-locked Rollup/esbuild platform artifacts restored into
`/tmp/cafepilot-js-platform`; application manifests and lockfiles were not changed.

Remaining: iOS registration/preview/15-second recording/permanent local save/upload,
physical-glasses verification and the HTTPS tunnel. These belong to later stages.
No iOS files, Apple signing settings, Meta app credentials or production deployment
were changed in this stage.

## Local Wi-Fi photo receiver for Barista

For the no-cloud phone-to-Mac path, run the separate media-only API on the Mac's
LAN interface. It advertises `_baristaphoto._tcp` with Bonjour and accepts the
existing authenticated raw JPEG route at `/api/upload`. After the receiver
acknowledges a photo, the backend keeps its normal media-store/processing copy and
also atomically copies it to `~/Pictures/BaristaShared/<UTC timestamp>_<capture id>.jpg`.
The iPhone remains responsible for retaining its own local copy and retry queue.

Start it from `business-core/`:

```bash
./scripts/run_local_photo_receiver.sh
```

This service uses port 8004 so the existing loopback-only media service on port
8003 is left alone. Keep the terminal open while using photo sync. Put the phone
and Mac on the same non-guest Wi-Fi network. The app needs iOS Local Network
permission and the receiver needs to be allowed through the Mac firewall. Do not
configure router port forwarding. The existing `MEDIA_UPLOAD_TOKEN` authenticates
uploads. The iOS app will need a local development credential supplied through
its ignored xcconfig; never commit it. Videos are not copied by this receiver.

## Automatic receipt and delivery validation

Barista's **Start Delivery Check** creates a UUID locally; it does not require the
PO number during capture. It tags the first JPEG with `X-Image-Role: receipt` and
the next with `X-Image-Role: delivery`, both sharing `X-Delivery-ID`. The receiver
stores them as `<delivery id>/receipt.jpg` and `delivery.jpg` under the shared
photo folder. It marks the manifest ready only after both roles arrive.

The running media service scans ready manifests every two seconds and calls its
authenticated `POST /api/receiving/validate-pair` endpoint. That endpoint creates a
receiving receipt; Muse Spark reads the slip (supplier, PO, lines) and code finds the
order by its PO number; SAM 3.1 + Muse Spark count the delivery photo and code adds up the
totals; the receiving service compares order, slip and count. Code then decides:
every line matches and the count is sure → the receipt is confirmed, stock goes up and
the order is marked Delivered; short, over, damaged or unsure → the receipt stays a draft
for the owner; PO not in the system → read and counted, nothing confirmed, "order not
found" logged. Inspect status/results at the authenticated
`GET /api/receiving/delivery-pairs` endpoint; each complete result includes the
existing receipt ID for the receiving API. Failed model/API jobs remain marked
failed and can be retried by POSTing the pair endpoint again. Videos and ordinary
untagged photos keep their existing behavior.
