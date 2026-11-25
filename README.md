# legoin-api

The Legoin backend. It takes one photo of LEGO pieces, finds and names each piece with the models from [legoin-ml](https://github.com/al-dioooo/legoin-ml), keeps the scan, and suggests builds from the pieces found. The web app is [legoin-web](https://github.com/al-dioooo/legoin-web); the API contract, team guidelines and decisions live in [legoin-docs](https://github.com/al-dioooo/legoin-docs).

A scan runs in three stages, as legoin-ml's release notes say:

1. **Detection** finds every piece with YOLO11n.
2. **Part classification** names each crop as one of the 13 catalogue parts with MobileNetV3-Large.
3. **Color** names each crop's color as one of the 10 catalogue colors, with K-Means in CIELAB.

Each piece gets one confidence, the lowest of the three stages' scores. A piece under 0.6 is flagged for the person to check. No detected piece is dropped.

Ideas come from an `IdeaGenerator`. Until the team picks an LLM provider, that's legoin-web's 18 idea templates in English and Indonesian. Whatever writes them, the backend runs `cek_inventori` itself, so every idea says which parts the person owns and which are missing.

See [AGENTS.md](AGENTS.md) for how to contribute.

## Tech stack

- Python 3.12, [FastAPI](https://fastapi.tiangolo.com) on [uvicorn](https://www.uvicorn.org), pydantic v2
- SQLite for scans and ideas, and the local disk for photos, in one data directory
- The `models` extra, pinned to legoin-ml v0.2.0's release notes:
  - [Ultralytics](https://docs.ultralytics.com) 8.4.190;
  - torch 2.15.0 and torchvision 0.29.2, as CPU wheels;
  - scikit-learn 1.9.1 and scikit-image 0.26.0;
  - headless OpenCV.
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

Then open `http://localhost:8000/health`, or the API's OpenAPI page at `http://localhost:8000/docs`. To point legoin-web at it, set `NEXT_PUBLIC_API_URL=http://localhost:8000/v1`.

**Docker:**

```bash
docker build -t legoin-api .
docker run -p 8000:8000 -v legoin-data:/data legoin-api
```

- The model files are downloaded and checked while the image builds, so the container starts without network access.
- It runs the real models as a non-root user, one uvicorn process, on `$PORT` (default 8000).
- Everything it keeps is in the `/data` volume.

## Checks

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

CI runs the same commands on every pull request, on the stand-in pipeline (`LEGOIN_PIPELINE=stub`), so it needs neither torch nor the model weights.

`tests/test_model_pipeline.py` needs the real libraries, so CI skips it. It checks the crops against Ultralytics' own `save_one_box`, and the color port against the released parameters. Run it with `uv sync --extra models` first.

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
| `LEGOIN_PUBLIC_URL` | the URL each request came in on | The absolute base for `photo.src`, e.g. `https://api.example.com`, for a host behind a proxy |
| `LEGOIN_SCAN_TIMEOUT_S` | `30` | A scan job still running this long after its upload fails with `timeout` |
| `LEGOIN_SCAN_WORKERS` | `1` | Scans run at the same time; torch already uses every core |
| `LEGOIN_IDEAS_TIMEOUT_S` | `30` | A request for ideas still unanswered after this long fails with `504 timeout` |
| `PORT` | `8000` | The port, in the image only |

The service needs no keys. When an LLM provider is chosen, its key goes here and in `.env.example`, with an empty value.

## The API

The API follows legoin-docs [`api/contract.md`](https://github.com/al-dioooo/legoin-docs/blob/main/api/contract.md) (draft v0.1). The base URL includes the version: `/v1`.

Every route needs a `Legoin-Device-Id` header, the contract's placeholder for accounts: each device only sees its own scans. Errors share one body, `{"error": {"code", "message", "field?"}}`.

| Endpoint | What it does |
| --- | --- |
| `POST /v1/scan-jobs` | Uploads one photo (`multipart/form-data`, part `photo`, JPEG, PNG or WebP, up to 10 MB) and answers `202` with a running job |
| `GET /v1/scan-jobs/{jobId}` | The job: `running` with its stage (`detecting`, `classifying`, `coloring`), `done` with its scan, or `failed` with its error. Poll it every 500 ms. |
| `DELETE /v1/scan-jobs/{jobId}` | Cancels the job; nothing is saved |
| `GET /v1/scans` | This device's scans, newest first |
| `GET /v1/scans/{scanId}` | One scan |
| `PATCH /v1/scans/{scanId}/detections` | Corrections, all or nothing: `edit`, `remove`, `add`, `restore` |
| `DELETE /v1/scans/{scanId}` | Deletes the scan, its photo and its ideas |
| `POST /v1/scans/{scanId}/ideas` | Three ideas for `theme` in `locale` (`en` or `id`), avoiding `exclude` |
| `GET /v1/scans/{scanId}/ideas/{ideaId}` | One idea from the scan's latest set, with the set |
| `GET /v1/photos/{name}` | A scan's photo, for a plain `<img>`, with no header needed |
| `GET /health` | `{"status": "ok", "version", "pipeline", "modelTag"?}`, for a host's health check |

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

## Deployment

Nothing is deployed yet; the host is a team decision. Whatever runs the image needs:

- **One instance, one process.** Scan jobs live in memory, so don't add uvicorn workers or a second instance. A restart forgets running jobs; the web app then gives up on them and reports a network error.
- **A persistent disk at `/data`.** It holds every scan, photo and idea. A host with an ephemeral disk loses them on each redeploy.
- **Memory and CPU.** The container used about 425 MB idle and 550 MB after scanning, on the project's 8-core server. Plan for at least 1 GB. A 31-piece photo scanned in about 0.5 s there, so a smaller CPU still has room within the contract's 5-second budget.
- **Image size.** About 2.0 GB unpacked, 0.6 GB to download, mostly torch.
- **HTTPS and a body limit at the proxy.** Cap request bodies at about 11 MB; the app also refuses uploads over 10 MB itself.
- **`LEGOIN_PUBLIC_URL`** set to the public base URL, so photo URLs use it.
- **`LEGOIN_CORS_ORIGINS`** set to the web app's origins.
