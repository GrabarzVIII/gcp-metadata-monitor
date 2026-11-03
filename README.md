# GCP Metadata Monitor

One authenticated **Cloud Run function** monitors **Cloud Logging** and **BigQuery** across GCP projects, stores results in BigQuery/Cloud Storage, and can send HTML reports by email. **Cloud Scheduler** can run it daily.

- Deploy: [`functions/monitoring/`](functions/monitoring/) (entry point `run_monitoring`, Python 3.12).
- Create metadata: [`sql/create_monitoring_tables.sql`](sql/create_monitoring_tables.sql).
- Configure checks and recipients: [`sql/inserts/`](sql/inserts/).

Use **Cloud Shell / Bash** from the repository root. The **main project** hosts the function, metadata, state bucket, and Scheduler; **monitored projects** contain source logs or tables. Billing must be enabled in the main project. Commands for creating resources and changing IAM require administrator privileges.

## 1. Create resources

Replace `your-monitoring-project` and adjust the region/location if needed:

~~~bash
export MAIN_PROJECT="your-monitoring-project"
export REGION="europe-west1"
export BQ_LOCATION="EU"
export DATASET_ID="monitoring"
export FUNCTION_NAME="gcp-metadata-monitor"
export STATE_BUCKET="$MAIN_PROJECT-monitor-state"
export RUNTIME_SA="gcp-monitor-runtime@$MAIN_PROJECT.iam.gserviceaccount.com"
export BUILDER_SA="gcp-monitor-builder@$MAIN_PROJECT.iam.gserviceaccount.com"
export SCHEDULER_SA="gcp-monitor-scheduler@$MAIN_PROJECT.iam.gserviceaccount.com"

gcloud config set project "$MAIN_PROJECT"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com bigquery.googleapis.com logging.googleapis.com \
  storage.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com \
  iam.googleapis.com --project="$MAIN_PROJECT"

gcloud iam service-accounts create gcp-monitor-runtime --project="$MAIN_PROJECT"
gcloud iam service-accounts create gcp-monitor-builder --project="$MAIN_PROJECT"
gcloud iam service-accounts create gcp-monitor-scheduler --project="$MAIN_PROJECT"
gcloud storage buckets create "gs://$STATE_BUCKET" --project="$MAIN_PROJECT" \
  --location="$REGION" --uniform-bucket-level-access --public-access-prevention
~~~

If an account/bucket already exists, reuse it rather than creating it again.

## 2. IAM: exactly which roles?

**One row per service account + role.** Multiple resources in one row mean **separate grants on each resource**, not a project-wide grant.

| Account | IAM role | Where to grant | When |
|---|---|---|---|
| `RUNTIME_SA` | `roles/bigquery.jobUser` | **Main project** | Always: query and load jobs run here |
| `RUNTIME_SA` | `roles/bigquery.dataViewer` | **Metadata dataset** in main project; **each source dataset/table** read by a BigQuery check | Metadata always; source data for SQL checks |
| `RUNTIME_SA` | `roles/bigquery.dataEditor` | **Each of two tables:** `monitoring_results` and `monitoring_runs` | Always: write history |
| `RUNTIME_SA` | `roles/storage.objectUser` | **State bucket** only | Always: state and HTML reports |
| `RUNTIME_SA` | `roles/logging.viewer` | **Each project** with ordinary log checks | For `LOG` rules |
| `RUNTIME_SA` | `roles/logging.privateLogViewer` | **Each project** with private/Data Access logs | Instead of `logging.viewer` for that project |
| `RUNTIME_SA` | `roles/secretmanager.secretAccessor` | **SMTP password secret** only | Only for email; see `6` |
| `BUILDER_SA` | `roles/run.builder` | **Main project** | Building the function |
| `SCHEDULER_SA` | `roles/run.invoker` | **This Cloud Run service** only | Scheduler; see `7` |

`RUNTIME_SA` always belongs to the main project, **including when monitoring other projects**. It needs no `bigquery.jobUser` role in source projects. Log-based checks for Cloud Run, Scheduler, Workflows, and Dataform do **not** require those services' admin/invoker roles.

Apply the main-project runtime/builder grants:

~~~bash
gcloud projects add-iam-policy-binding "$MAIN_PROJECT" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/bigquery.jobUser
gcloud projects add-iam-policy-binding "$MAIN_PROJECT" \
  --member="serviceAccount:$BUILDER_SA" --role=roles/run.builder
gcloud storage buckets add-iam-policy-binding "gs://$STATE_BUCKET" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/storage.objectUser
~~~

Run the following **BigQuery SQL** as an IAM-authorized operator. Replace `MAIN_PROJECT`, `monitoring`, and the account email:

~~~sql
GRANT `roles/bigquery.dataViewer`
ON SCHEMA `MAIN_PROJECT.monitoring`
TO 'serviceAccount:gcp-monitor-runtime@MAIN_PROJECT.iam.gserviceaccount.com';

GRANT `roles/bigquery.dataEditor`
ON TABLE `MAIN_PROJECT.monitoring.monitoring_results`
TO 'serviceAccount:gcp-monitor-runtime@MAIN_PROJECT.iam.gserviceaccount.com';

GRANT `roles/bigquery.dataEditor`
ON TABLE `MAIN_PROJECT.monitoring.monitoring_runs`
TO 'serviceAccount:gcp-monitor-runtime@MAIN_PROJECT.iam.gserviceaccount.com';
~~~

For **every monitored project** containing logs, grant either ordinary or private log access:

~~~bash
export MONITORED_PROJECT="your-source-project"
gcloud projects add-iam-policy-binding "$MONITORED_PROJECT" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/logging.viewer
# If you need private/Data Access logs in this project, use
# roles/logging.privateLogViewer instead of roles/logging.viewer.
~~~

For **each BigQuery source dataset** used by a check, run (in its BigQuery location):

~~~sql
GRANT `roles/bigquery.dataViewer`
ON SCHEMA `MONITORED_PROJECT.SOURCE_DATASET`
TO 'serviceAccount:gcp-monitor-runtime@MAIN_PROJECT.iam.gserviceaccount.com';
~~~

**Deployer/operator permissions are separate from service-account permissions.** The user/CI deploying the function needs `roles/run.sourceDeveloper` and `roles/serviceusage.serviceUsageConsumer` on the main project, plus `roles/iam.serviceAccountUser` on **RUNTIME_SA and BUILDER_SA**. The person creating the Scheduler job needs permission to manage jobs (e.g. `roles/cloudscheduler.admin` in main project) and `roles/iam.serviceAccountUser` on **SCHEDULER_SA**. Resource/API/IAM setup needs additional administrator rights. Do not grant broad Owner/Editor roles to these service accounts.

## 3. Create metadata and checks

Edit the four `DECLARE` values at the top of [`sql/create_monitoring_tables.sql`](sql/create_monitoring_tables.sql) (project, dataset, BigQuery location, retention), then run:

~~~bash
bq query --project_id="$MAIN_PROJECT" --location="$BQ_LOCATION" \
  --use_legacy_sql=false < sql/create_monitoring_tables.sql
~~~

This creates `notification_profiles`, `monitoring_rules`, `monitoring_results`, and `monitoring_runs` (default history retention: 90 days).

Edit and execute [`01_notification_profile.sql`](sql/inserts/01_notification_profile.sql) first, then **only the rules you want** from [`sql/inserts/`](sql/inserts/):

| File | Check |
|---|---|
| `02_log_rule.sql` | Count matching logs |
| `03_sql_kpi_rule.sql` | Custom single-value SELECT |
| `04_freshness_rule.sql` | Age of latest data |
| `05_row_count_rule.sql` | Row count |
| `06_null_rate_rule.sql` | Fraction of NULL values |
| `07_duplicates_rule.sql` | Duplicate keys |

In **every selected INSERT file**, replace project/dataset paths, source resources, recipients, and IDs. Set `target_profiles` to an existing profile ID. For BigQuery rules, `location` is the **source dataset's** location, not necessarily `BQ_LOCATION`. Built-in SQL rules need a fully qualified table name and a TIMESTAMP column. Alarm operators (e.g. `GT 0` or `LT 100`) mean **when to alert**.

~~~bash
bq query --project_id="$MAIN_PROJECT" --location="$BQ_LOCATION" \
  --use_legacy_sql=false < sql/inserts/01_notification_profile.sql
# Example: after editing the chosen rule file
bq query --project_id="$MAIN_PROJECT" --location="$BQ_LOCATION" \
  --use_legacy_sql=false < sql/inserts/05_row_count_rule.sql
~~~

Examples start **disabled**. Enable the profile and your chosen rule in BigQuery (IDs below match the example files):

~~~sql
UPDATE `MAIN_PROJECT.monitoring.notification_profiles`
SET is_active = TRUE WHERE profile_id = 'data-team';
UPDATE `MAIN_PROJECT.monitoring.monitoring_rules`
SET is_active = TRUE WHERE rule_id = 'orders-row-count';
~~~

Use a unique `rule_id` for each check. To change an existing rule, use `UPDATE` (rerunning an INSERT does not overwrite it). Grant the required source IAM before running the check.

## 4. Deploy

Create a private **`deploy.env.yaml`** in the repository root (not inside `functions/monitoring/`):

~~~yaml
GOOGLE_CLOUD_PROJECT: "your-monitoring-project"
METADATA_DATASET: "monitoring"
BQ_LOCATION: "EU"
STATE_BUCKET: "your-monitoring-project-monitor-state"
NOTIFICATIONS_ENABLED: "false"
~~~

Other defaults (timeouts, budgets, timezone, SMTP) are in [`config.py`](functions/monitoring/config.py). Keep notifications disabled until testing. Do not put SMTP passwords in this file.

~~~bash
gcloud run deploy "$FUNCTION_NAME" --project="$MAIN_PROJECT" --region="$REGION" \
  --source=functions/monitoring --function=run_monitoring --base-image=python312 \
  --build-service-account="projects/$MAIN_PROJECT/serviceAccounts/$BUILDER_SA" \
  --service-account="$RUNTIME_SA" --env-vars-file=deploy.env.yaml \
  --no-allow-unauthenticated --invoker-iam-check \
  --timeout=1800 --memory=512Mi --concurrency=1 --max-instances=1

export FUNCTION_URL="$(gcloud run services describe "$FUNCTION_NAME" \
  --project="$MAIN_PROJECT" --region="$REGION" --format='value(status.url)')"
~~~

Deploy the **whole folder**; `main.py` and `requirements.txt` must be at the source root. Keep the service private.

## 5. Test a preview

Grant your test identity permission to invoke the service (replace `YOUR_EMAIL`):

~~~bash
gcloud run services add-iam-policy-binding "$FUNCTION_NAME" \
  --project="$MAIN_PROJECT" --region="$REGION" \
  --member="user:YOUR_EMAIL" --role=roles/run.invoker

curl --fail-with-body -X POST "$FUNCTION_URL" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" -d '{"preview":true}'
~~~

A preview **runs real checks and saves results, but does not send email**. Inspect `monitoring_results` and `monitoring_runs`, and the private HTML report in `gs://STATE_BUCKET/reports/RUN_ID/PROFILE_ID.html`.

## 6. Email (optional)

Create a password secret in the main project and grant **RUNTIME_SA** access:

~~~bash
gcloud secrets create gcp-monitor-smtp-password \
  --project="$MAIN_PROJECT" --replication-policy=automatic
read -r -s -p "SMTP password: " SMTP_PASSWORD
printf '\n'
printf '%s' "$SMTP_PASSWORD" | gcloud secrets versions add gcp-monitor-smtp-password \
  --project="$MAIN_PROJECT" --data-file=-
unset SMTP_PASSWORD
gcloud secrets add-iam-policy-binding gcp-monitor-smtp-password \
  --project="$MAIN_PROJECT" --member="serviceAccount:$RUNTIME_SA" \
  --role=roles/secretmanager.secretAccessor
~~~

Add these values to `deploy.env.yaml` (adapt to your SMTP provider):

~~~yaml
NOTIFICATIONS_ENABLED: "true"
SMTP_HOST: "smtp.example.com"
SMTP_PORT: "587"
SMTP_USER: "smtp-user"
SMTP_FROM: "monitor@example.com"
SMTP_SECURITY: "starttls"
~~~

Update Cloud Run (replace `1` with the actual secret version), then send a test request with `{"preview":false}`:

~~~bash
gcloud run services update "$FUNCTION_NAME" --project="$MAIN_PROJECT" --region="$REGION" \
  --env-vars-file=deploy.env.yaml --update-secrets=SMTP_PASS=gcp-monitor-smtp-password:1
~~~

## 7. Schedule (optional)

Grant **SCHEDULER_SA** invocation access and create a daily 08:00 Warsaw-time job **after** validating the report:

~~~bash
gcloud run services add-iam-policy-binding "$FUNCTION_NAME" \
  --project="$MAIN_PROJECT" --region="$REGION" \
  --member="serviceAccount:$SCHEDULER_SA" --role=roles/run.invoker

gcloud scheduler jobs create http gcp-monitor-daily --project="$MAIN_PROJECT" \
  --location="$REGION" --schedule='0 8 * * *' --time-zone='Europe/Warsaw' \
  --uri="$FUNCTION_URL" --http-method=POST \
  --oidc-service-account-email="$SCHEDULER_SA" --oidc-token-audience="$FUNCTION_URL" \
  --headers='Content-Type=application/json' --message-body='{"preview":false}' \
  --attempt-deadline=1800s --max-retry-attempts=0 --max-retry-duration=0s
~~~

To stop the schedule: `gcloud scheduler jobs pause gcp-monitor-daily --project="$MAIN_PROJECT" --location="$REGION"`.

## Troubleshooting / limits

- **401/403:** check Cloud Run Invoker role, authenticated identity, and Scheduler OIDC audience.
- **Check ERROR:** verify source-project IAM, BigQuery source location, or log filter.
- **HTTP 500:** inspect Cloud Run logs and `monitoring_runs`; invalid configuration, failed checks, or delivery errors can cause `NEEDS_ATTENTION`.
- **HTTP 409:** an unfinished execution already has that run ID; inspect state before retrying.
- **No email:** check `preview=false`, `NOTIFICATIONS_ENABLED=true`, SMTP configuration, and secret access.
- Monitoring uses rolling time windows; daily local-time schedules can shift at DST. BigQuery queries, builds, storage, and requests may incur costs. Check for missing Scheduler runs separately; retries cannot guarantee exactly-once email.

MIT license: [LICENSE](LICENSE).
