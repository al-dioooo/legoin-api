# legoin-api

The Legoin backend. It takes one photo of LEGO pieces, finds and names each piece with the models from [legoin-ml](https://github.com/al-dioooo/legoin-ml), keeps the scan, and suggests builds from the pieces found. The web app is [legoin-web](https://github.com/al-dioooo/legoin-web); the API contract, team guidelines and decisions live in [legoin-docs](https://github.com/al-dioooo/legoin-docs).

A scan runs in three stages, as legoin-ml's release notes say:

1. **Detection** finds every piece with YOLO11n.
2. **Part classification** names each crop as one of the 13 catalogue parts with MobileNetV3-Large.
3. **Color** names each crop's color as one of the 10 catalogue colors, with K-Means in CIELAB.

Each piece gets one confidence, the lowest of the three stages' scores. A piece under 0.6 is flagged for the person to check. No detected piece is dropped.

Picture builds, when they're turned on, design a LEGO build of an uploaded picture: Claude places catalogue pieces through strict tools, and BrickLayer checks and renders the build in a worker process of its own, within a daily budget (ADR-0012 in legoin-docs, [below](#picture-builds)).

Ideas come from an `IdeaGenerator`. With `ANTHROPIC_API_KEY` set, Claude writes them (ADR-0009 in legoin-docs). Otherwise, and whenever Claude can't answer in time, they come from legoin-web's 18 idea templates in English and Indonesian. Whatever writes them, the backend runs `cek_inventori` itself, so every idea says which parts the person owns and which are missing.

See [AGENTS.md](AGENTS.md) for how to contribute.

## Tech stack

- Python 3.12, [FastAPI](https://fastapi.tiangolo.com) on [uvicorn](https://www.uvicorn.org), pydantic v2
- SQLite for scans and ideas, and the local disk for photos, in one data directory
- The `models` extra, pinned to legoin-ml v0.2.0's release notes:
  - [Ultralytics](https://docs.ultralytics.com) 8.4.190;
  - torch 2.15.0 and torchvision 0.29.2, as CPU wheels;
  - scikit-learn 1.9.1 and scikit-image 0.26.0;
  - headless OpenCV.
- The [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python), for ideas written by Claude
- [uv](https://docs.astral.sh/uv/) for Python and dependencies, [Ruff](https://docs.astral.sh/ruff/) for linting and formatting, pytest for tests
- A CPU `Dockerfile`

The stack is ADR-0008 in legoin-docs.

## Setup and run

Needs [uv](https://docs.astral.sh/uv/getting-started/installation/); it installs Python 3.12 if needed.

**The stand-in pipeline** needs no torch or weights. Its detections are made up from the photo's pixels, the same every time, which is enough to build the web app against:

```bash
uv sync
uv run uvicorn --factory legoin_api.main:create_app --reload
```

**The real models:**

```bash
uv sync --extra models
LEGOIN_PIPELINE=models uv run uvicorn --factory legoin_api.main:create_app
```

The first start downloads the model files, about 22 MB, from the pinned legoin-ml release into `data/models/`, and checks each one's SHA-256. Later starts check the cached files. `uv run python -m legoin_api.weights` downloads them ahead of time.

**Ideas from Claude** need an Anthropic API key in the environment. Keep it in a file outside the repository, readable only by you, holding one line `ANTHROPIC_API_KEY=...`, and pass it with `--env-file`, so it never appears on a command line or in your shell history:

```bash
uv run --env-file /path/to/anthropic.env uvicorn --factory legoin_api.main:create_app
```

Without the key, ideas come from the templates. `/health` reports which is running.

Then open `http://localhost:8000/health`, or the API's OpenAPI page at `http://localhost:8000/docs`. To point legoin-web at it, set `NEXT_PUBLIC_API_URL=http://localhost:8000/v1`.

**Docker:**

```bash
docker build -t legoin-api .
docker run -p 8000:8000 -v legoin-data:/data --env-file /path/to/anthropic.env legoin-api
```

- The model files are downloaded and checked while the image builds, so the container starts without network access.
- It runs the real models as a non-root user, one uvicorn process, on `$PORT` (default 8000).
- Everything it keeps is in the `/data` volume.
- The key is passed when the container starts, with `--env-file`. It's never in the image: `.dockerignore` leaves out `.env*`. Without it, ideas come from the templates.

## Checks

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

CI runs the same commands on every pull request, on the stand-in pipeline (`LEGOIN_PIPELINE=stub`), so it needs neither torch nor the model weights.

`tests/test_model_pipeline.py` needs the real libraries, so CI skips it. It checks the crops against Ultralytics' own `save_one_box`, and the color port against the released parameters. Run it with `uv sync --extra models` first.

`tests/test_claude_live.py` makes one real request to Claude. It's skipped without `ANTHROPIC_API_KEY`, so CI, which has no key, never calls the API. Every other test uses a fake client. To run it:

```bash
uv run --env-file /path/to/anthropic.env pytest tests/test_claude_live.py
```

## Environment variables

All optional. `.env.example` lists them with their defaults.

| Variable | Default | What it sets |
| --- | --- | --- |
| `LEGOIN_PIPELINE` | `stub` (`models` in the image) | `models` runs the legoin-ml release. `stub` gives made-up detections, for tests and frontend work. |
| `LEGOIN_DATA_DIR` | `./data` (`/data` in the image) | The service's only state: `legoin.db`, `photos/` and, unless set apart, `models/` |
| `LEGOIN_MODEL_TAG` | `v0.2.0` | The legoin-ml release the models come from. Only tags with hashes in `weights.py` work. |
| `LEGOIN_MODEL_DIR` | `<LEGOIN_DATA_DIR>/models` (`/opt/legoin/models` in the image) | Where model files are cached |
| `LEGOIN_TORCH_THREADS` | torch's default, one per core | Threads torch may use |
| `LEGOIN_CORS_ORIGINS` | `http://localhost:3000,https://legoin-web.vercel.app` | Comma-separated origins allowed to call the API. No wildcard, so add each preview origin. |
| `LEGOIN_PUBLIC_URL` | the URL each request came in on | The absolute base for `photo.src` and preview URLs, e.g. `https://api.example.com`, for a host behind a proxy |
| `LEGOIN_BUILDS_DIR` | `./builds` (`/app/builds` in the image) | The library of checked builds: `manifest.json`, and each build's `preview.webp` and `model.ldr`. Without it, ideas get no previews. |
| `LEGOIN_SCAN_TIMEOUT_S` | `30` | A scan job still running this long after its upload fails with `timeout` |
| `LEGOIN_SCAN_WORKERS` | `1` | Scans run at the same time; torch already uses every core. Must be `1` with the `models` pipeline, whose models aren't thread-safe. |
| `LEGOIN_SCAN_QUEUE_SIZE` | `8` | Scans accepted and not yet finished, running ones included. Beyond it, an upload answers `503 unavailable` with `Retry-After: 5`. Each holds its decoded photo, at most 48 MiB. |
| `LEGOIN_IDEAS_TIMEOUT_S` | `30` | A request for ideas still unanswered after this long fails with `504 timeout`. Claude gets 80% of it; the templates answer after that. |
| `ANTHROPIC_API_KEY` | unset | The Anthropic API key. Read by the SDK from the environment only, never logged. |
| `LEGOIN_IDEAS_GENERATOR` | `anthropic` with a key, else `templates` | What writes ideas. `anthropic` without a key refuses to start. |
| `LEGOIN_IDEAS_MODEL` | `claude-sonnet-5-5` | The Claude model that writes ideas. `claude-opus-5-5` also works, but takes about 17 s. |
| `LEGOIN_IDEAS_EFFORT` | `low` | Claude's effort: `low`, `medium`, `high`, `xhigh` or `max` |
| `LEGOIN_PICTURE_BUILDS` | `off` | `anthropic` turns picture builds on, if `ANTHROPIC_API_KEY` is set, the `picture` extra is installed and the LDraw parts library is found. Otherwise they stay off, and the log says what's missing. |
| `LEGOIN_PICTURE_MODEL` | `claude-opus-5-5` | The Claude model that designs picture builds. `claude-sonnet-5-5` costs about a third, with plainer builds. |
| `LEGOIN_PICTURE_EFFORT` | `medium` | Its effort |
| `LEGOIN_PICTURE_DAILY_BUDGET_USD` | `3.00` | Dollars a day (UTC) for every picture job together, counted from real usage, kept in `picture.db` |
| `LEGOIN_PICTURE_DAILY_LIMIT` | `2` | Picture jobs each account may start a day (UTC) |
| `LEGOIN_PICTURE_JOB_COST_USD` | `0.50` | Dollars one picture job may spend; each reserves this much of the daily budget |
| `LEGOIN_PICTURE_JOB_SECONDS` | `300` | Seconds one picture job may design for. Its worker is killed a minute after that. |
| `LEGOIN_PICTURE_JOB_CALLS` | `24` | Claude calls one picture job may make |
| `LEGOIN_PICTURE_QUEUE_SIZE` | `3` | Picture jobs waiting or running. Beyond it, an upload answers `503 unavailable` with `Retry-After: 30`. |
| `LEGOIN_PICTURE_WORKER_MEMORY_MB` | `1024` | The picture worker's resident memory; past it, the worker is killed |
| `LEGOIN_IDEA_PREVIEWS` | `all` | Previews of the ideas Claude writes: `all` after every request for ideas, `on_open` when an idea's page is opened, or `off`. They need Claude ideas, the key, the `picture` extra and the LDraw parts library, and share picture builds' daily budget. |
| `LEGOIN_IDEA_PREVIEW_MODEL`, `LEGOIN_IDEA_PREVIEW_EFFORT` | `claude-opus-5-5`, `medium` | The model and effort for previews |
| `LEGOIN_IDEA_PREVIEW_DAILY_LIMIT` | `9` | Previews each account may start a day (UTC); past it, an idea's preview is `unavailable` |
| `LEGOIN_IDEA_PREVIEW_COST_USD` | `0.25` | Dollars one preview may spend; each reserves this much of the daily budget |
| `LEGOIN_IDEA_PREVIEW_SECONDS`, `LEGOIN_IDEA_PREVIEW_CALLS` | `120`, `10` | One preview's time and Claude calls |
| `LDRAW_LIBRARY_PATH` | `~/ldraw`, then `/usr/share/ldraw` | The LDraw parts library, for rendering picture builds |
| `LEGOIN_CONTAINER` | unset (`1` in the image) | Warns at start-up when a deployment setting is missing: today, `LEGOIN_PUBLIC_URL` |
| `PORT` | `8000` | The port, in the image only |

The only secret is `ANTHROPIC_API_KEY`. Never commit it, put it in an image or log it.

## The API

The API follows legoin-docs [`api/contract.md`](https://github.com/al-dioooo/legoin-docs/blob/main/api/contract.md) (draft 0.2). The base URL includes the version: `/v1`.

Every scan, scan-job and idea route needs a session, as `Authorization: Bearer <token>` from registering or logging in, and each account only sees its own scans (ADR-0011). Without one, it's `401 unauthorized` with `WWW-Authenticate: Bearer`; another account's scan is a `404`, never a `403`. The account routes, photos, build files and `/health` need no session. Errors share one body, `{"error": {"code", "message", "field?"}}`.

| Endpoint | What it does |
| --- | --- |
| `POST /v1/auth/register` | Makes an account from `email` and `password` (8 to 128 characters) and answers `201` with a session: `token`, `expiresAt` and `account`. `409 email_taken` if the email has one. |
| `POST /v1/auth/login` | A new session, or `401 invalid_credentials`. Both take an optional `deviceId`, the old `Legoin-Device-Id`, and move that device's scans from before accounts to the account, once. After 10 failures for one email in 15 minutes, `429 rate_limited` with `Retry-After`. |
| `POST /v1/auth/logout` | Ends the session in `Authorization: Bearer`; always `204` |
| `GET /v1/auth/me` | The session's account, or `401 unauthorized` |
| `POST /v1/scan-jobs` | Uploads one photo (`multipart/form-data`, part `photo`, JPEG, PNG or WebP, up to 10 MB and 4096 × 4096 pixels) and answers `202` with a running job, or `503 unavailable` when the queue is full |
| `GET /v1/scan-jobs/{jobId}` | The job: `running` with its stage (`detecting`, `classifying`, `coloring`), `done` with its scan, or `failed` with its error. Poll it every 500 ms. |
| `DELETE /v1/scan-jobs/{jobId}` | Cancels the job; nothing is saved |
| `GET /v1/scans` | This account's scans, newest first |
| `GET /v1/scans/{scanId}` | One scan |
| `PATCH /v1/scans/{scanId}/detections` | Corrections, all or nothing: `edit`, `remove`, `add`, `restore` |
| `DELETE /v1/scans/{scanId}` | Deletes the scan, its photo and its ideas |
| `POST /v1/scans/{scanId}/ideas` | Three ideas for `theme` in `locale` (`en` or `id`), avoiding `exclude` |
| `GET /v1/scans/{scanId}/ideas/{ideaId}` | One idea from the scan's latest set, with the set |
| `GET /v1/photos/{name}` | A scan's photo, for a plain `<img>`, with no header needed |
| `GET /v1/builds/{buildId}/preview.webp` | A library build's picture, with no header needed, cached for a year: its URL carries the file's version |
| `GET /v1/builds/{buildId}/model.ldr` | A library build's LDraw model, as a download, cached the same way |
| `GET /v1/picture-quota` | Whether this account can start a picture job now (`available`, and `reason`: `off`, `limit` or `budget`), with `used`, `limit` and `resetsAt` |
| `POST /v1/picture-jobs` | Uploads a picture (`multipart/form-data`: part `picture`, with a photo's limits, and `locale`, `en` or `id`) and answers `202` with a job `queued`. `429 picture_limit` or `503 picture_budget` with `Retry-After` until midnight UTC; `503 unavailable` when off, or with `Retry-After` when the queue is full. |
| `GET /v1/picture-jobs/{jobId}` | The job: `running` with its stage (`queued`, `designing`, `checking`, `rendering`), `done` with its build, or `failed` with its error. Poll it every second; a build takes about a minute. |
| `DELETE /v1/picture-jobs/{jobId}` | Cancels the job, killing its worker; nothing is saved |
| `GET /v1/picture-builds` | This account's picture builds, newest first |
| `GET /v1/picture-builds/{buildId}` | One picture build; with `?scanId=`, its `check`: `used` and `missing` against that scan, from `cek_inventori` |
| `DELETE /v1/picture-builds/{buildId}` | Deletes the build, its picture, preview and model |
| `GET /v1/picture-files/{name}` | A picture build's picture, preview or `.ldr`, at an unguessable name, with no header needed |
| `GET /health` | `{"status": "ok", "version", "pipeline", "modelTag"?, "ideas", "ideasModel"?, "pictureBuilds", "pictureModel"?}`, for a host's health check |

Ideas that are a library build carry its `buildId` and `preview` (ADR-0010): every template idea does, and so does a Claude idea when Claude picks a library build. Claude's cached prompt lists each build's id, title and parts; when the person's pieces fit one, it can answer a slot with that `buildId`, and the service uses the build's own text, steps and parts, still runs `cek_inventori`, and adds `preview`. Claude's own builds have no picture. A set is kept with each idea's `buildId` only; `preview` is worked out on the way out, from `LEGOIN_PUBLIC_URL` and the files' versions.

**Accounts** (ADR-0011). Passwords are kept only as Argon2id hashes, at `argon2-cffi`'s defaults (3 passes over 64 MiB), with at most two hashes at once; an unknown email is checked against a stand-in hash, so it takes as long. A session token is `lgn_` and 32 random bytes, kept only as its SHA-256, and lasts 30 days. The failed log-in count is in memory, like scan jobs. An older data directory is migrated to accounts on start (`PRAGMA user_version` 2), with nothing to run by hand.

`tests/contract/` holds the contract's own example bodies, and the tests check the API against them.

## Model releases

The backend uses legoin-ml [v0.2.0](https://github.com/al-dioooo/legoin-ml/releases/tag/v0.2.0):

| File | What it is |
| --- | --- |
| `best.pt` | the YOLO11n detector |
| `legoin_mobilenetv3_part.pt` | the part classifier |
| `legoin_mobilenetv3_part_labels.json` | the classifier's label map |
| `legoin_warna.json` | the color parameters, still **provisional**: not yet calibrated on photos |

The settings from the release notes live in `src/legoin_api/pipeline/release.py`, and each file's SHA-256 in `src/legoin_api/weights.py`. A file that doesn't match is refused, and so is a label map whose settings differ.

To move to a new tag:

1. Copy its hashes into `MANIFESTS` in `weights.py`.
2. Copy any changed settings into `release.py`.
3. Link the release in the pull request.

Anything built on these models credits the datasets listed in [legoin-ml's README](https://github.com/al-dioooo/legoin-ml#dataset), all CC BY 4.0.

## The build library

`builds/` holds checked LEGO builds made with BrickLayer, one directory each (ADR-0010 in legoin-docs):

- `build.py`: the build in [BrickLayer](https://github.com/AvaLovelace1/brickwright), with its theme, difficulty, title, summary and steps in English and Indonesian in `META`;
- `model.ldr`: the build as an LDraw file;
- `preview.webp`: an isometric picture, 640 px square, on a transparent background.

`builds/manifest.json` lists every build with its parts, counted from the build itself, and each file's SHA-256. Each template has a build with the same id, text and parts. They're made offline: the service never runs BrickLayer, and neither it nor CI needs the LDraw parts library. It only serves the previews and models, and the image ships them with the manifest.

`scripts/builds.py` writes them. It's a [uv script](https://docs.astral.sh/uv/guides/scripts/) with its own dependencies, `bricklayer-sdk` 0.1.0 and `brickwright` 0.1.0, locked in `scripts/builds.py.lock`; they're never installed with the service. For each build it:

1. runs BrickWright's validation, the same as `brickwright external validate`: no collisions, nothing floating, one connected assembly;
2. checks it's 3 to 15 pieces, all catalogue parts and colors;
3. writes `model.ldr` and renders `preview.webp`;
4. writes its entry in the manifest.

Rendering needs the [LDraw parts library](https://library.ldraw.org/library/updates/complete.zip), unzipped outside the repository. It runs on a CPU, with no GPU, in about 2 s a build:

```bash
LDRAW_LIBRARY_PATH=/path/to/ldraw uv run --script scripts/builds.py
```

It renders only builds whose files no longer match the manifest, since renders differ by a few pixels from run to run. Name a build, or pass `--force` after changing the renderer, to render anyway. `--check` validates every build and compares it with the manifest, writing nothing.

To add a build, copy a directory, edit `build.py`, run the script and look at the preview. `tests/test_builds.py` checks the manifest against the catalogue, every file against its hash and size limit, and each template against its build.

The builds use BrickLayer and BrickWright (MIT): Pun et al., *BrickWright: Agentic Creation of Physically Feasible Brick Builds*, 2026, [arXiv:2610.31651](https://arxiv.org/abs/2610.31651). BrickLayer's part data derive from the [LDraw.org Parts Library](https://library.ldraw.org/) (CC BY 2.0 and 4.0), [BrickNet](https://github.com/kulits/BrickNet) (MIT) and [Rebrickable](https://rebrickable.com/downloads/). The previews are rendered from the LDraw.org Parts Library, which the `.ldr` files refer to but don't include.

LEGO® is a trademark of the LEGO Group, which does not sponsor, authorize or endorse this project.

## Picture builds

A person uploads a picture, and Claude designs a LEGO build of it from the catalogue (ADR-0012 in legoin-docs, and section 13 of the contract). It's off unless `LEGOIN_PICTURE_BUILDS=anthropic`.

- **Tools, never code.** Claude only calls strict tools: `place_pieces` (catalogue part and color enums, whole grid positions, a rotation), `remove_pieces`, `validate`, `render`, `finish` and `reject_picture`. The service applies each call to a grid of at most 60 pieces and checks it with BrickLayer: no collisions, nothing floating, one connected assembly. `finish` takes only a valid build that Claude has seen in a render since its last change. The prompt (`src/legoin_api/picture/prompt.py`) adapts BrickWright's guidance to these tools.
- **A job, in a worker process.** One job runs at a time, in `python -m legoin_api.picture.worker`, at nice 10 with 2 render threads. The dispatcher kills it on cancel, past its deadline (`LEGOIN_PICTURE_JOB_SECONDS` + 60 s) or past `LEGOIN_PICTURE_WORKER_MEMORY_MB`. The worker reports its stages and each call's cost on stdout; the API process records them and saves the build.
- **Budgets.** Each call's cost comes from its usage and the model's prices, and is kept in `<data dir>/picture.db`. A job is accepted only if the day's spending plus every running job's cap plus its own fits under `LEGOIN_PICTURE_DAILY_BUDGET_USD`, and the account is under `LEGOIN_PICTURE_DAILY_LIMIT`. Before each call, the call's worst case has to fit under the job's cap. A cap ends the job with the last valid build, under a plain title and level-by-level steps, or fails it with `no_valid_build`.
- **Files.** The picture (a JPEG with no EXIF), the preview (WebP, 640 px, from the library's angle) and the `.ldr` (with the library's credit header) live in `<data dir>/picture-files/` under random names, and go with the build.
- **What Anthropic receives:** the picture, at most 1,024 px, and the language. Nothing else.

It needs the `picture` extra (BrickLayer, ldraw2mesh, pygfx and wgpu, about 245 MB), the [LDraw parts library](https://library.ldraw.org/library/updates/complete.zip) (about 620 MB unzipped) and, with no GPU, Mesa's llvmpipe (`libgl1-mesa-dri` and `libegl1` on Ubuntu):

```bash
uv sync --extra picture
LEGOIN_PICTURE_BUILDS=anthropic LDRAW_LIBRARY_PATH=/path/to/ldraw \
  uv run --env-file /path/outside/the/repo/anthropic.env uvicorn --factory legoin_api.main:create_app
```

The tests use fakes for Claude, BrickLayer and the renderer. `tests/test_picture_checks.py` runs the real ones when the extra and the library are there:

```bash
LDRAW_LIBRARY_PATH=/path/to/ldraw uv run --extra picture pytest tests/test_picture_checks.py
```

On the project's server, Claude Opus 5.5 at `medium` made a build of a drawn picture in a median of 48 s and $0.14, every build valid (ADR-0012's spike). The worker peaked at about 550 MB.

### Idea previews

Each idea Claude writes itself gets a preview built by the same engine, after the ideas are answered (ADR-0012, "Idea previews"):

- **The job:** it runs in the same queue and worker, after any picture builds. It's built from the idea's own part and color pairs, up to twice its pieces (20 at most), and keeps the idea's title.
- **`previewStatus`:** each idea carries `pending`, `on_open`, `ready` or `unavailable`, which `GET /v1/scans/{id}` and `GET /v1/scans/{id}/ideas/{ideaId}` return as it changes. With `on_open`, reading the idea starts its build.
- **A built preview becomes the idea:** a `gen_…` `buildId` and `preview` (files in `picture-files/`), and its steps, summary, `used` and `missing`, from `cek_inventori` of the build's parts.
- **Stale sets:** asking for ideas again, a correction that drops the ideas, or deleting the scan cancels and deletes the old previews.
- **Never in the way:** a limit, the budget or a failure leaves the idea text-only, and nothing about previews can fail a request for ideas.

The spike measured a median preview at $0.036 and 14 s with Opus 5.5, so a set of three costs about $0.12.

## Deployment

Nothing is deployed yet; the host is a team decision. Whatever runs the image needs:

- **One instance, one process.** Scan jobs live in memory, so don't add uvicorn workers or a second instance. A restart forgets running jobs; the web app then gives up on them and reports a network error.
- **A persistent disk at `/data`.** It holds every scan, photo and idea. A host with an ephemeral disk loses them on each redeploy.
- **Memory and CPU.** The container used about 425 MB idle and 550 MB after scanning, on the project's 8-core server. Plan for at least 1 GB. A 31-piece photo scanned in about 0.5 s there, so a smaller CPU still has room within the contract's 5-second budget.
- **Image size.** About 2.0 GB unpacked, 0.6 GB to download, mostly torch.
- **HTTPS and a body limit at the proxy.** Cap request bodies at about 11 MB; the app also refuses uploads over 10 MB itself.
- **`LEGOIN_PUBLIC_URL`** set to the public base URL, so photo URLs use it. The image logs a warning at start-up when it's unset.
- **`LEGOIN_CORS_ORIGINS`** set to the web app's origins.
- **Picture builds, if wanted:** the `picture` extra in the install (`uv sync --extra picture`, or `models picture` together), the LDraw parts library on the host with `LDRAW_LIBRARY_PATH` pointing at it, Mesa's llvmpipe, and `LEGOIN_PICTURE_BUILDS=anthropic`. The image's default doesn't include them. Allow about 550 MB more memory while a picture job runs. `/health` says whether they're on.
- **`ANTHROPIC_API_KEY`** from the host's secret store or `--env-file`, for ideas from Claude. With Sonnet 5.5 at effort `low`, a request for ideas took a median of 9.6 s and cost about $0.02 (ADR-0009).
