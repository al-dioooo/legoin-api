# Contract examples

The example bodies from legoin-docs [`api/contract.md`](https://github.com/al-dioooo/legoin-docs/blob/4ae6f08/api/contract.md), draft v0.1 at `4ae6f08`, copied as they are. The tests check the API against them, so a change to the contract shows up here first.

| File | Contract example |
| --- | --- |
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
| `error-invalid-request.json` | Section 6, the error body |

\* The contract shortens these two with comments (`jsonc`). The copies fill them in as the comments say: `scans-list.json` with "the three detections above" from the finished job, and `stored-idea.json` with the first idea and the whole set from the ideas answer.
