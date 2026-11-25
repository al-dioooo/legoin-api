# Contract examples

The example bodies from legoin-docs [`api/contract.md`](https://github.com/al-dioooo/legoin-docs/blob/feature/accounts-adr/api/contract.md), draft 0.2 (legoin-docs#24, which adds accounts, on top of legoin-docs#20's `buildId` and `preview`), copied as they are. The tests check the API against them, so a change to the contract shows up here first.

| File | Contract example |
| --- | --- |
| `auth-register-request.json` | `POST /auth/register`, the request |
| `auth-session.json` | `POST /auth/register`, the `201` answer: a `Session` |
| `auth-me.json` | `GET /auth/me`, the answer |
| `scan-job-accepted.json` | `POST /scan-jobs`, the `202` answer |
| `scan-job-running.json` | `GET /scan-jobs/{jobId}`, a running job |
| `scan-job-done.json` | `GET /scan-jobs/{jobId}`, a finished job with its scan |
| `scan-job-failed.json` | `GET /scan-jobs/{jobId}`, a failed job |
| `error-not-found.json` | `GET /scans/{scanId}`, the `404` answer |
| `scans-list.json` | `GET /scans`* |
| `patch-detections-request.json` | `PATCH /scans/{scanId}/detections`, the request |
| `patch-detections-response.json` | `PATCH /scans/{scanId}/detections`, the answer |
| `ideas-request.json` | `POST /scans/{scanId}/ideas`, the request |
| `ideas-response.json` | `POST /scans/{scanId}/ideas`, the answer |
| `stored-idea.json` | `GET /scans/{scanId}/ideas/{ideaId}`* |
| `error-invalid-request.json` | Section 7, the error body |
| `picture-quota.json` | `GET /picture-quota`, the answer (section 13, Proposed in legoin-docs#26) |
| `picture-job-accepted.json` | `POST /picture-jobs`, the `202` answer |
| `picture-job-done.json` | `GET /picture-jobs/{jobId}`, a finished job with its build* |
| `picture-job-failed.json` | `GET /picture-jobs/{jobId}`, a failed job |

\* The contract shortens these with comments (`jsonc`). The copies fill them in as the comments say: `scans-list.json` with "the three detections above" from the finished job, and `stored-idea.json` with the first idea and the whole set from the ideas answer. `picture-job-done.json` keeps the steps and parts the example shows and drops the comments.
