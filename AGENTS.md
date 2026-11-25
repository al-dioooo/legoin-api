# AGENTS.md

Rules for anyone changing this repository, people and coding agents alike. They summarize the team guidelines in [legoin-docs/guidelines](https://github.com/al-dioooo/legoin-docs/tree/main/guidelines); when the two disagree, the guidelines win.

## Stack and commands

A Python 3.12 web service in `src/legoin_api/`: FastAPI on uvicorn, with pydantic v2 models that mirror the [API contract](https://github.com/al-dioooo/legoin-docs/blob/main/api/contract.md). It runs the models from a pinned [legoin-ml](https://github.com/al-dioooo/legoin-ml) release: Ultralytics 8.4.190 (YOLO11), torch 2.15.0 and torchvision 0.29.2 on CPU, scikit-learn 1.9.1 and scikit-image 0.26.0. [uv](https://docs.astral.sh/uv/) manages Python and the dependencies, Ruff lints and formats, and pytest tests. The stack is recorded in ADR-0008 in legoin-docs.

- `uv sync` installs the locked dependencies without the models; `uv sync --extra models` adds torch and Ultralytics.
- `uv run ruff check .` lints and `uv run ruff format .` formats.
- `uv run pytest` runs the tests on the stand-in pipeline (`LEGOIN_PIPELINE=stub`), with no torch or weights.

CI runs `ruff check`, `ruff format --check` and `pytest` on every pull request. The real models never run in CI, since they need torch and the release's weights.

## The contract

- The API follows `api/contract.md` in legoin-docs exactly: paths, shapes, camelCase keys, optional fields left out rather than `null`, error codes and deadlines. legoin-web builds against the same document.
- Where the contract is ambiguous, follow its wording, choose the narrowest reading, and note the gap in the pull request. Never change the contract from here; propose the change in legoin-docs.
- Test against the contract's own example bodies (`tests/contract/`). When the contract changes, update the copies and the code in the same pull request.

## Models and data

- Never commit weights, datasets, photos, databases or logs: `data/`, `*.pt`, `*.onnx`, `*.db`. The service downloads its model files from a legoin-ml release and checks each file's SHA-256 before loading it.
- Settings the backend must match, such as image size, thresholds and the classifier's temperature, come from the release notes. A new model tag is a code change that copies its settings and hashes, with the release linked in the pull request.
- Keep the stand-in pipeline working, so tests and CI never need torch.

## Commits

Small and focused: one logical change, roughly 5 to 100 lines, explainable in one sentence and easy to revert. Commit when a function, a fix or a logical step is done; if you pause work, you should be able to commit. Never mix a refactor with a feature, or a fix with formatting. No 1000+ line commits, no WIP commits on shared branches, and no vague messages like "update code" or "fix bugs". `uv.lock` is generated: commit it with the `pyproject.toml` change that caused it.

**Before committing.** `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest` pass.

**Messages.** [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): description`, scope optional, e.g. `feat(scans): add the scan job routes`. Types: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `ci`. Mark breaking changes with `!`, e.g. `feat(api)!: move to /v2`.

## Branches

Trunk-based. `main` is always release-ready; there is no `develop`, `staging` or `release` branch. Work on short-lived branches named for the change: `feature/<short-description>`, `fix/<bug-description>` or `chore/<maintenance>`, e.g. `feature/scan-jobs`. Every change reaches `main` through a pull request; commit directly to `main` only for an emergency hotfix.

## Pull requests

Start from an issue and agree on the approach before building. One problem per PR, ideally under 500 changed lines, not counting `uv.lock`; split larger work into several PRs, stacked when they depend on each other.

- The title carries the ticket id, e.g. `feat(scans): add the scan job routes (#2)`, and the PR links the issue.
- Fill in [the PR template](.github/pull_request_template.md): summary, problem, solution, test evidence, deployment prerequisites or breaking changes, notes.
- Test evidence says what ran and where: the test suite, a request against a running service, and for model changes a real photo on the stated machine, with its latency.
- Call out anything a deployment must change: a new environment variable, a new model tag, a migration of the data directory.
- Note any gap you found in the contract.

Merge only when CI passes, all discussions are resolved, required reviewers approve, and the commits follow the rules above. Squash-merge with the PR title as the commit subject.

## Docs

Keep the README sections current: overview, tech stack, setup and run, checks, environment variables, the API, model releases, deployment. Decisions are ADRs in [legoin-docs/decisions](https://github.com/al-dioooo/legoin-docs/tree/main/decisions), not in this repo.

## Secrets

Never commit `.env*` files except `.env.example`, API keys, tokens, credentials or logs. The service needs no keys today. When an LLM provider is chosen, read its key from an environment variable, list it in the README and `.env.example` with an empty value, and never log it.
