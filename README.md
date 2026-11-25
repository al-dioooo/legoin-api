# legoin-api

The Legoin backend. It takes one photo of LEGO pieces, finds and names each piece with the models from [legoin-ml](https://github.com/al-dioooo/legoin-ml), keeps the scan, and suggests builds from the pieces found. The web app is [legoin-web](https://github.com/al-dioooo/legoin-web); the API contract, team guidelines and decisions live in [legoin-docs](https://github.com/al-dioooo/legoin-docs).

A scan runs in three stages, as legoin-ml's release notes say:

1. **Detection** finds every piece with YOLO11n.
2. **Part classification** names each crop as one of the 13 catalogue parts with MobileNetV3-Large.
3. **Color** names each crop's color as one of the 10 catalogue colors, with K-Means in CIELAB.

Each piece gets one confidence, the lowest of the three stages' scores. A piece under 0.6 is flagged for the person to check. No detected piece is dropped.

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
| `LEGOIN_CONTAINER` | unset (`1` in the image) | Warns at start-up when a deployment setting is missing: today, `LEGOIN_PUBLIC_URL` |
| `PORT` | `8000` | The port, in the image only |

The only secret is `ANTHROPIC_API_KEY`. Never commit it, put it in an image or log it.

## The API

The API follows legoin-docs [`api/contract.md`](https://github.com/al-dioooo/legoin-docs/blob/main/api/contract.md) (draft v0.1). The base URL includes the version: `/v1`.

Every route needs a `Legoin-Device-Id` header, the contract's placeholder for accounts: each device only sees its own scans. Errors share one body, `{"error": {"code", "message", "field?"}}`.

| Endpoint | What it does |
| --- | --- |
| `POST /v1/scan-jobs` | Uploads one photo (`multipart/form-data`, part `photo`, JPEG, PNG or WebP, up to 10 MB and 4096 × 4096 pixels) and answers `202` with a running job, or `503 unavailable` when the queue is full |
| `GET /v1/scan-jobs/{jobId}` | The job: `running` with its stage (`detecting`, `classifying`, `coloring`), `done` with its scan, or `failed` with its error. Poll it every 500 ms. |
| `DELETE /v1/scan-jobs/{jobId}` | Cancels the job; nothing is saved |
| `GET /v1/scans` | This device's scans, newest first |
| `GET /v1/scans/{scanId}` | One scan |
| `PATCH /v1/scans/{scanId}/detections` | Corrections, all or nothing: `edit`, `remove`, `add`, `restore` |
| `DELETE /v1/scans/{scanId}` | Deletes the scan, its photo and its ideas |
| `POST /v1/scans/{scanId}/ideas` | Three ideas for `theme` in `locale` (`en` or `id`), avoiding `exclude` |
| `GET /v1/scans/{scanId}/ideas/{ideaId}` | One idea from the scan's latest set, with the set |
| `GET /v1/photos/{name}` | A scan's photo, for a plain `<img>`, with no header needed |
| `GET /v1/builds/{buildId}/preview.webp` | A library build's picture, with no header needed, cached for a year: its URL carries the file's version |
| `GET /v1/builds/{buildId}/model.ldr` | A library build's LDraw model, as a download, cached the same way |
| `GET /health` | `{"status": "ok", "version", "pipeline", "modelTag"?, "ideas", "ideasModel"?}`, for a host's health check |

Ideas that are a library build carry its `buildId` and `preview` (ADR-0010): every template idea does, and so does a Claude idea when Claude picks a library build. Claude's cached prompt lists each build's id, title and parts; when the person's pieces fit one, it can answer a slot with that `buildId`, and the service uses the build's own text, steps and parts, still runs `cek_inventori`, and adds `preview`. Claude's own builds have no picture. A set is kept with each idea's `buildId` only; `preview` is worked out on the way out, from `LEGOIN_PUBLIC_URL` and the files' versions.

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

## Deployment

Nothing is deployed yet; the host is a team decision. Whatever runs the image needs:

- **One instance, one process.** Scan jobs live in memory, so don't add uvicorn workers or a second instance. A restart forgets running jobs; the web app then gives up on them and reports a network error.
- **A persistent disk at `/data`.** It holds every scan, photo and idea. A host with an ephemeral disk loses them on each redeploy.
- **Memory and CPU.** The container used about 425 MB idle and 550 MB after scanning, on the project's 8-core server. Plan for at least 1 GB. A 31-piece photo scanned in about 0.5 s there, so a smaller CPU still has room within the contract's 5-second budget.
- **Image size.** About 2.0 GB unpacked, 0.6 GB to download, mostly torch.
- **HTTPS and a body limit at the proxy.** Cap request bodies at about 11 MB; the app also refuses uploads over 10 MB itself.
- **`LEGOIN_PUBLIC_URL`** set to the public base URL, so photo URLs use it. The image logs a warning at start-up when it's unset.
- **`LEGOIN_CORS_ORIGINS`** set to the web app's origins.
- **`ANTHROPIC_API_KEY`** from the host's secret store or `--env-file`, for ideas from Claude. With Sonnet 5.5 at effort `low`, a request for ideas took a median of 9.6 s and cost about $0.02 (ADR-0009).
