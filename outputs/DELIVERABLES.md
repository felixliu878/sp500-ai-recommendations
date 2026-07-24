# Deliverables index

## Weekly meeting

- [Editable Gamma presentation](https://gamma.app/docs/3apepkw0zbrt8h9) — final seven-slide deck that separates the completed v1.2.0 findings from the v1.3.0 protocol correction and reports the final validation count.
- `When-Does-AI-Apply-Its-Own-Judgment-to-Stock-Ratings-Gamma-Final.pptx` — verified local PowerPoint export (seven 16:9 slides).
- [Temporary Gamma PowerPoint download](https://assets.api.gamma.app/export/pptx/3apepkw0zbrt8h9/8cbd79ae7d6ef87f0f5ce3250fa9df42/When-Does-AI-Apply-Its-Own-Judgment-to-Stock-Ratings.pptx) — signed export link; the local copy above is the durable deliverable.
- `../docs/WEEKLY_PRESENTATION_SCRIPT.md` — approximately three-minute script plus likely-question responses.
- `../docs/LIVE_PILOT_RESULTS.md` — final findings, evidence audit, cost, limitations, and scale-up decision.

## Executed live research

- Canonical source run, local-only: `pilot_2026-07-24_openai_20260724T132933641207Z/`.
- Accepted consolidated run, local-only: `pilot_2026-07-24_openai_20260724T132933641207Z/retries/retry_20260724T140656289007Z/`.
- The accepted table contains 72 completed live assessment cells after two targeted retries.
- `pilot_summary.json` contains the detailed local machine-readable aggregate result.
- `LIVE_PILOT_SUMMARY.json` is the sanitized aggregate included in Git.
- `LIVE_PILOT_VALIDATION.json` is the sanitized integrity report included in Git.
- The local `validation_report.json` is `PASS_WITH_WARNINGS`: no hard failures, with two official-PDF trace warnings and the disclosed lower-bound cost caveat.
- Live raw responses and third-party provider caches are intentionally ignored by Git; the repository contains code, tests, methodology, and aggregate findings.

The older `pilot_2026-07-17_dry_run/` remains a deterministic routing artifact labeled `DRY_RUN_NOT_AI` and must not be mixed with the live results.
