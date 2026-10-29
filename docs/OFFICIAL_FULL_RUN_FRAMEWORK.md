# Official full-run framework

## Current state

The August official S&P 500 experiment and its approved recovery pass have finished. The design contains 499 eligible securities, four information conditions, and three independent repetitions: 5,988 planned assessment cells. It accepted 5,956 results; 32 Condition D tasks remain rejected after the approved three-attempt ceiling. The earlier unlaunched July 31 plans are retained as historical preparation artifacts.

The framework uses the [Batch API](https://developers.openai.com/api/docs/guides/batch) with `/v1/responses`, the dated `gpt-5.4-2026-03-05` snapshot, strict Structured Outputs, and web search only in Condition D. The required four-task smoke gate was exercised before the full phase. See [`FULL_SCALE_RESULTS.md`](./FULL_SCALE_RESULTS.md) for the archived findings and remaining limitations.

## Frozen execution design

```text
Source audit:       503 security rows after cash exclusion
Eligible universe: 499 securities
Excluded:          FDXF, HONA, PSKY, Q
Experiment cells:  499 × 4 conditions × 3 repetitions = 5,988
Smoke phase:       MU, all four conditions, repetition 1 = 4 cells
Full phase:        remaining 5,984 cells
Batch layout:      1 smoke shard + 12 condition-isolated full shards
Shard bound:       at most 500 requests per file
Retry bound:       at most two paid attempts per task
```

The smoke outputs are real experiment cells, not disposable tests. If all four validate, they remain in the final 5,988-cell dataset. If one fails, only that exact task is retried once. The 12 full shards cannot be submitted before the smoke gate passes.

Each plan freezes and hashes:

- the experiment and official protocol configurations;
- the audit and eligible-universe manifests and gates;
- all 1,996 condition-specific prompts and packet hashes;
- the model, cutoff, horizon, schema, and response settings;
- 5,988 deterministic task IDs and their shard membership; and
- every local Batch JSONL input file.

Changing any frozen file causes preflight to fail. A new plan must be prepared and separately approved.

The archived full run used prompt `v1.4.0` with 12 monthly price observations. The default two-attempt plan was not rewritten: a separately hashed recovery plan and matching approval allowed a third attempt for failed tasks. Current `v1.4.1` templates add guardrails for future runs and do not change the archived experiment.

## Restart and transfer behavior

The system is resumable at the stage and artifact level.

### Provider download

Yahoo and State Street artifacts are written to temporary files, flushed, and atomically renamed. On restart, valid same-cutoff cache entries are reused. Missing, partial, corrupt, wrong-date, wrong-schema, or error-bearing cache entries are rejected and downloaded again.

### Batch upload

Before an upload starts, the shard state records upload intent. If the server accepts the file but the client loses the response, the next run searches for the unique frozen filename and exact byte size and records the existing remote file instead of uploading a duplicate.

### Batch creation

Before creating a Batch job, the state records creation intent. Each job carries the plan hash, run ID, shard ID, and input hash. If creation succeeds remotely but the client times out, the next run finds and reuses that Batch job through its metadata and input-file ID.

### Result download

Completed output and error files are downloaded to temporary files and atomically renamed. An interrupted transfer leaves no apparent completed file and is safely downloaded again. This is not byte-range continuation inside one HTTPS transfer; the bounded shard file is restarted. Current input files are no larger than about 6.7 MB.

### Reconciliation and retry

Batch output order is not assumed. Every output/error row is matched to a unique frozen `custom_id`; unknown, duplicate, wrong-attempt, or wrong-shard IDs stop ingestion. Each model response must then pass output identity, schema, cutoff-date, evidence-origin, and condition isolation checks. Accepted and failed task checkpoints are append-only. A retry uses the same prompt and request body, with only a new unique `custom_id` suffix.

## Operating procedure after professor approval

Replace `<run>` with the prepared run directory.

1. Confirm the plan and the approval lock:

   ```bash
   PYTHONPATH=src python scripts/official_full_run.py status --run-dir official_runs/<run>
   PYTHONPATH=src python scripts/official_full_run.py preflight --run-dir official_runs/<run>
   ```

2. After explicit approval of that exact plan, record the approval:

   ```bash
   PYTHONPATH=src python scripts/official_full_run.py approve \
     --run-dir official_runs/<run> \
     --approved-by "<name>" \
     --note "Professor approved the frozen full-run plan"
   ```

3. Submit only the smoke shard:

   ```bash
   PYTHONPATH=src python scripts/official_full_run.py launch \
     --run-dir official_runs/<run> --phase smoke
   ```

4. Poll and validate the smoke result:

   ```bash
   PYTHONPATH=src python scripts/official_full_run.py sync --run-dir official_runs/<run>
   ```

5. If `smoke_gate.json` is `PASS`, run the resumable supervisor. It submits bounded full shards, polls, downloads, checkpoints, creates exact retries when necessary, and finalizes only after all 5,988 cells are accepted:

   ```bash
   PYTHONPATH=src python scripts/official_full_run.py supervise --run-dir official_runs/<run>
   ```

The same `supervise` command can be run again after a process or computer restart. It reads the durable state and continues from the last completed stage.

## Verification performed without paid calls

Fifty-two automated tests and 39 subtests pass. The official-run tests specifically simulate:

- a server-accepted upload followed by a client timeout;
- a server-created Batch job followed by a client timeout;
- an interrupted result download;
- output rows arriving in reverse order;
- one failed model task and an exact-body retry;
- frozen-prompt tampering; and
- complete 24-cell synthetic execution and finalization.

No OpenAI file upload, Batch creation, or paid full-run request was performed during this engineering verification.
