# Operations and recovery

## Statuses

Rule results distinguish OK, ALERT, ERROR (unable to evaluate), and NO_DATA. The latter can be mapped to ALERT or OK explicitly in the rule.

Run status COMPLETED means evaluation/report processing finished; individual rules can still be ALERT or ERROR. NEEDS_ATTENTION indicates invalid rules or delivery failures. FAILED indicates an orchestration/persistence failure. RUNNING is stored in the atomic claim and can remain after a crash.

Invalid individual rules are listed in `configuration_errors`; valid rules continue. Malformed profiles and duplicate IDs fail the run rather than risk ambiguous routing. API errors log rule IDs and exception types without exposing query contents or source log payloads. Diagnose failed queries through their BigQuery jobs in the hosting project.

## Deduplication and recovery

Scheduled run IDs hash job name, scheduled timestamp, and preview flag. A Cloud Storage claim uses `if_generation_match=0` before processing. Completed duplicates return the recorded outcome without repeating work. Cloud Run IAM authenticates callers; Scheduler headers identify an execution, not a trusted identity.

Failed/interrupted claims return HTTP 409 on replay. Scheduler retries are disabled, but duplicate delivery remains possible. The engine never steals a stale claim automatically. SMTP acceptance and state writes are not transactional; exactly-once email is not guaranteed. Stable Message-ID values help tracing but do not ensure deduplication.

Before retrying:

1. Inspect `gs://STATE_BUCKET/runs/RUN_ID.json`.
2. Inspect BigQuery history, Cloud Run logs, and SMTP delivery history. A crash can leave no final BigQuery run row and incomplete counts.
3. Correct the problem and perform a new manual preview with the original `window_end`.
4. Decide whether a new live run should send another report; some recipients may already have received it.

Prefer new manual runs to deleting claims. Claims expire after 90 days. Results are saved before reports are sent. A failure saving final history leaves an incomplete claim for inspection.

## Independent monitoring

Configure external alerts for failed Scheduler executions and missing daily completion events. Successful persistence emits:

```text
monitor_run_finished run_id=... status=COMPLETED
```

Test that external alert path. `/healthz` only proves the process responds. No independent heartbeat alert is automatically provisioned in this MVP.

## Troubleshooting

| Symptom | Check |
|---|---|
| HTTP 401/403 | Invoker role, Scheduler identity, OIDC audience equals base service URL |
| HTTP 409 | Existing run claim; inspect before manual recovery |
| Rule ERROR | Filters, IAM, query location, scan limit, scalar contract, BigQuery job errors |
| No artifact | No matching profile rules, only-problems filtering, invalid configuration |
| Artifact but no email | Preview flag, notifications enabled, SMTP/TLS, sender verification |
| Missing process | Zero errors cannot detect absence; add freshness or successful-event checks |
| Build denied | Builder bucket/repository access, submitter actAs permission |

## Security, retention, updates

Reports contain operational details; snapshots contain SQL. Restrict dataset and bucket access. Runtime can read configuration but writes only history tables. Configuration authors require a separate trusted operator identity. Source-data grants are dataset-scoped; log grants are project-scoped unless your organization narrows them further.

History partitions default to 90 days; state/reports expire after 90 days; build source archives after seven days. Images remain until explicitly removed. SMTP-provider retention is separate.

Build a new immutable image, update Terraform, preview, then resume normal delivery. Preserve the previous image URI for rollback. Model changes require regenerating schemas, reviewing compatibility, and applying the schema update before deploying code that depends on it.

For teardown, pause Scheduler first. Tables have deletion protection and buckets are not force-destroyed. Preserve required history before intentionally removing protections. Destroy application infrastructure before bootstrap infrastructure.
