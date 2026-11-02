# Operations and recovery

## Statuses

Rule results distinguish OK, ALERT, ERROR (unable to evaluate), and NO_DATA. The latter can be mapped to ALERT or OK explicitly in the rule.

Run status COMPLETED means evaluation/report processing finished; individual rules can still be ALERT or NO_DATA. NEEDS_ATTENTION indicates empty configuration, invalid rules, rule ERRORs, or delivery failures and returns HTTP 500 so Scheduler can expose the failure. FAILED indicates an orchestration/persistence failure. RUNNING is stored in the atomic claim and can remain after a process crash. Notification statuses distinguish PREVIEW, DISABLED, SENT, and FAILED.

Invalid individual rules are listed in `configuration_errors`; valid rules continue. Malformed profiles and duplicate IDs fail the run rather than risk ambiguous routing. API errors log rule IDs and exception types without exposing query contents or source log payloads. Diagnose failed queries through their BigQuery jobs in the hosting project.

## Deduplication and recovery

Scheduled run IDs hash job name, the normalized scheduled timestamp, and preview flag. Both Scheduler headers must be present, and scheduled requests cannot override window_end. A Cloud Storage claim uses `if_generation_match=0` before processing. Completed duplicates return the recorded outcome without repeating work. Cloud Run IAM authenticates callers; Scheduler headers identify an execution, not a trusted identity. Manual requests get a new ID each time.

Failed/interrupted claims return HTTP 409 on replay. Scheduler retries are disabled, but duplicate delivery remains possible. The engine never steals a stale claim automatically. SMTP acceptance and state writes are not transactional; exactly-once email is not guaranteed. Stable Message-ID values help tracing but do not ensure deduplication.

Before retrying:

1. Inspect `gs://STATE_BUCKET/runs/RUN_ID.json`.
2. Inspect BigQuery history, Cloud Run logs, and SMTP delivery history. A crash can leave no final BigQuery run row and incomplete counts.
3. Correct the problem and perform a new manual preview with the original `window_end` (without Scheduler headers).
4. Decide whether a new live run should send another report; some recipients may already have received it.

Prefer new manual runs to deleting claims. Results are saved in one batch before reports are sent; partial results are saved on a graceful early failure. Delivery progress is checkpointed after each profile. If BigQuery history fails, the function still attempts to write FAILED and the observed counts to Cloud Storage. If both backends fail or the process is killed, the last checkpoint remains. Configure retention for report objects separately; retaining claim objects preserves the deduplication horizon. Do not expire active claims.

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

History partitions created by SQL default to 90 days. State/report objects are retained until a bucket lifecycle rule or an operator removes them. Configure lifecycle rules according to your retention requirements. Cloud Build and Artifact Registry retention are managed in your GCP project. SMTP-provider retention is separate.

Deploy a new source revision with the README's `gcloud run deploy --source . --function run_monitoring` command, preview, then resume normal delivery. Use Cloud Run revision traffic controls for rollback. SQL files are the schema source of truth: review and apply explicit ALTER statements when changing existing tables. CREATE TABLE IF NOT EXISTS does not migrate an existing schema.

For teardown, pause Scheduler first and preserve required history before intentionally deleting resources. If migrating an earlier Terraform-managed deployment, back up its state and decide which resources retain Terraform ownership before changing them with gcloud. Removing Terraform files from this repository does not delete resources or relinquish their ownership in an existing state file.
