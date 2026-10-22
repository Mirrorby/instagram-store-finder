# Delokay import worker on Railway (no Apify)

This is an internal import service, not the public customer interface. It runs
the existing photo-only collector in its own headless Chromium and publishes
demo stores through the existing Delokay platform API. Local Chrome, Google
Sheets and Apify are not used. Docker copies only code, config and dependencies;
local output, cookies, credentials and account lists are excluded.

## Railway configuration

Deploy this repository using the included Dockerfile and railway.json.
Use **one replica** and mount a persistent Volume at `/data`.
The SQLite queue and publication checkpoints must survive redeploys.
Do not enable overlapping replicas or share this volume with a second worker.
The service takes an exclusive filesystem lock on startup.

Set Railway variables:

| Variable | Value |
| --- | --- |
| `IMPORT_API_TOKEN` | New random secret, at least 32 characters; backend only |
| `STORE_API_ORIGIN` | `https://catalog-factory.nikita-karpof.workers.dev` |
| `PLATFORM_API_KEY` | Existing Delokay platform secret; never expose to the browser |
| `DATA_DIR` | `/data` |
| `INSTAGRAM_STORAGE_STATE_JSON` | Optional Playwright storage-state JSON for a dedicated Instagram session |

Railway supplies `PORT`; the service listens on `0.0.0.0`. Healthcheck: `/health`.
Generate an HTTPS domain for access from the Cloudflare backend. Keep the API
token server-side. The public Mini App must use Delokay authentication, not this
token. Apply per-user free-import limits in Delokay before calling this service.

## Internal API

All `/v1/*` routes require `Authorization: Bearer <IMPORT_API_TOKEN>`.

`POST /v1/jobs`, JSON body:

```json
{
  "instagram": "https://www.instagram.com/example/",
  "theme": "minimal",
  "language": "en",
  "currency": "USD",
  "products": 6
}
```

Also send `Idempotency-Key`, 16–100 ASCII letters, digits, underscores or hyphens.
The backend must generate and persist this key for each logical customer request.
The same key and payload return the same job; a changed payload returns 409.
Up to 20 jobs may be queued/running. One browser runs at a time, with a 15-minute
process timeout. Theme: `minimal`, `fashion`, `home`, `beauty`, `auto`.
Language: `ru`, `en`, `auto`. Products: 1–6. Currency: USD, EUR, BYN, RUB, KZT.

Response: HTTP 202 with `id`, `status`, `createdAt`.
Poll `GET /v1/jobs/<id>` using backend credentials.

| Status | Meaning |
| --- | --- |
| `queued`, `running` | Waiting / collecting and publishing |
| `ready` | Demo published; `store` contains the existing publisher's ID and URL |
| `needs_review` | Incomplete source; no demo published; issues returned |
| `restricted` | Instagram requires login, challenge, or limits access |
| `failed` | Sanitized error; may retry explicitly |
| `interrupted` | Service restarted during work; inspect and retry explicitly |

`POST /v1/jobs/<id>/retry` resumes failed/interrupted work with the same
publication checkpoint. It does not recollect a saved bundle or overwrite a
manually changed store. Ready/restricted/needs_review jobs cannot be retried by
this endpoint. A fresh import uses a new job/key and creates a separate draft.
Instagram restrictions are never retried automatically.

## What still needs integration and validation

- Live Railway build, Chromium startup, Instagram access and successful demo
  publication must be tested after Railway authentication is restored.
- Start without a session. If Instagram blocks access, configure a dedicated
  session securely; do not paste cookies into chat or commit them. This service
  does not provide an interactive browser-login UI. A session can expire or be
  rejected from a datacenter IP; a deployment is not evidence of successful scraping.
- Customer-facing template selection, per-user quotas, job ownership, Telegram
  notifications, automatic store-owner assignment and payment activation belong
  in Delokay. They are not implemented by this internal worker. Stores remain
  demos with real orders disabled until the existing ownership/activation flow.
- `needs_review` keeps its bundle on the volume; partial editor import is not
  implemented yet. Prices remain "on request" as in the existing collector.
- The volume retains source data and checkpoints; monitor disk use. Establish a
  retention policy before opening self-service publicly. Do not delete checkpoints
  for active jobs or jobs that must remain resumable.
- Never log Authorization headers, storage-state JSON, signed CDN URLs or raw
  browser errors. This service returns only sanitized failure codes.

## Local checks

```sh
python -m unittest discover -s tests -p 'test_railway_service.py'
docker build -t delokay-import .
```

The Docker image and Python Playwright dependency are both pinned to 1.58.0.
The Mac desktop collector is unchanged.

## Collection-only rollout and website references

For initial cloud validation, set `IMPORT_PUBLISH_ENABLED=0`. In this mode the
service starts without Delokay platform credentials, collects Instagram into a
private bundle on the volume and never creates or overwrites a store. Job output
reports `published: false`. Retrieve the saved bundle with authenticated
`GET /v1/jobs/<id>/bundle`. This mode is intended for the first live data-quality
checks before integrating with the existing owned Delokay draft.

The same queue accepts a website reference:

```json
{"kind":"website","url":"https://example.com/"}
```

Results include title, description, headings, links, image references, fonts and
colors from public HTML and up to six CSS files. `analysis=static_html_css` and
`javascriptExecuted=false` explicitly identify this as static analysis. JavaScript
applications may return incomplete results and are marked `needsRenderedReview`.
No screenshot or reusable template is generated yet. HTML/CSS analysis uses no
Instagram session and sends no visitor cookies. Requests are limited to public
HTTPS targets; DNS is checked and connections are pinned to a public IP while
preserving TLS hostname verification. Redirects and CSS targets are rechecked.
Each response is capped at 1 MB; at most four redirects are followed.

On 2026-10-22 Railway account/project reads worked, but creation of the staging
service returned `Deploys have been paused due to an upstream issue`. No live
collector or Instagram success is claimed until an actual deployment reaches
SUCCESS and live collection jobs have been inspected. Connect this service only
to Delokay staging during the initial rollout.

On 2026-10-23, service creation returned `Your trial has expired. Please select
a plan to continue using Railway.` Account reads still work, but the connected
workspace cannot provision this service until its plan is activated. No service,
volume or deployment was created, and no new secrets were sent to Railway.

The cloud Docker image defaults to `IMPORT_PUBLISH_ENABLED=0`. Enable publishing
explicitly only after validating collection and owned-draft integration. The
initial runtime snapshot is published on GitHub branch
`feat/railway-reference-collector`; it contains the collector dependencies and
deployment files without uncommitted Mac desktop changes. Initial Railway setup:
use that branch, one replica, a `/data` volume and a fresh `IMPORT_API_TOKEN`.
