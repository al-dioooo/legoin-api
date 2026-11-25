# legoin-api

The Legoin backend. It takes one photo of LEGO pieces, finds and names each piece with the models from [legoin-ml](https://github.com/al-dioooo/legoin-ml), keeps the scan, and suggests builds from the pieces found. The web app is [legoin-web](https://github.com/al-dioooo/legoin-web); the API contract, team guidelines and decisions live in [legoin-docs](https://github.com/al-dioooo/legoin-docs).

The service is being built. The stack is proposed in ADR-0008 in legoin-docs, and the API follows [`api/contract.md`](https://github.com/al-dioooo/legoin-docs/blob/main/api/contract.md).

See [AGENTS.md](AGENTS.md) for how to contribute.

## Checks

Needs [uv](https://docs.astral.sh/uv/getting-started/installation/); it installs Python 3.12 if needed.

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

CI runs the same commands on every pull request, on the stand-in pipeline (`LEGOIN_PIPELINE=stub`), so it needs neither torch nor the model weights.

## Run locally

```bash
uv run uvicorn --factory legoin_api.main:create_app --reload
```

`GET http://localhost:8000/health` answers `{"status": "ok", ...}` with the pipeline that's running.
