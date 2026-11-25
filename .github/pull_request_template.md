<!--
PR title: <type>(<scope>): <description> (#<issue>)
Example:  feat(scans): add the scan job routes (#2)
-->

## Summary

<!-- Brief description of the change. -->

## Problem

<!-- What issue or limitation this PR addresses. -->

## Solution

<!-- How the change solves the problem. -->

## Test Evidence

<!-- What ran and where: the test suite, requests against a running service, and for model changes a real photo on the stated machine with its latency. -->

## Deployment Prerequisite / Breaking Changes

<!-- New environment variables, a new model tag, a data directory migration, a contract change. Write "None" if there are none. -->

## Notes

<!-- Contract gaps found, additional context and what reviewers should focus on. -->

Closes #

### Checklist

- [ ] PR title carries the issue number
- [ ] `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest` pass
- [ ] Follows the API contract; any gap is noted above
- [ ] No weights, photos, databases, logs or secrets committed
