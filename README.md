# GCP Metadata Monitor — deployment manual

A metadata-driven monitoring system deployed as **one HTTP Cloud Run function** in a central GCP project. The same function reads logs and checks BigQuery data in other projects, saves results, and sends HTML reports to configured email profiles.

## 1. What to deploy and what to run

```text
README.md                           Deployment and permissions manual
LICENSE                             MIT license
sql/
  create_monitoring_tables.sql      Creates the metadata dataset and four tables
  inserts/                          Ready-to-edit INSERT scripts for profiles and checks
functions/
  monitoring/                       Upload THIS folder as ONE Cloud Run function
    main.py                         Entry point: run_monitoring
    config.py                       Environment defaults and settings loader
    monitor.py                      Rule validation, checks, history, and run coordination
    report.py                       HTML rendering and SMTP delivery
    daily_report.html               Email template
    requirements.txt                Python dependencies installed by GCP
```

| Repository path | Action | Destination |
|---|---|---|
| `functions/monitoring/` | Deploy the entire folder: four Python files, the HTML template, and requirements | One Cloud Run function in the main project |
| `sql/create_monitoring_tables.sql` | Edit its configuration declarations and execute the complete script | BigQuery in the main project |
| `sql/inserts/` | Run the profile INSERT, then only the check INSERTs you need | Existing metadata tables in the main project |
| `README.md` | Follow the setup, IAM, configuration, and invocation instructions | Operator documentation |

**Deploy the entire `functions/monitoring/` folder as one function.** All runtime files are at the same level. For a source ZIP, zip the **contents** of that folder so `main.py` and `requirements.txt` are at the ZIP root; do not add an outer `functions/monitoring/` directory inside the ZIP.

The function uses ordinary imports between neighboring Python files. No package directory or `__init__.py` file is needed. `main.py` handles HTTP requests, `config.py` loads environment settings, `monitor.py` validates metadata, performs checks, and records results, and `report.py` prepares and sends reports.

The application uses plain functions and dictionaries, with no custom classes or Pydantic models. `run_monitoring()` calls `run_monitor()`, which reads and validates metadata, runs checks, saves results, and prepares reports. `config.py` contains one `DEFAULTS` dictionary and one `get_settings()` function. Cloud Run environment variables override those defaults; the loader converts numbers and booleans and checks supported values and limits. Configure deployment values through `deploy.env.yaml` and SMTP credentials through Secret Manager. Monitoring rules and recipients are configured through the SQL INSERT scripts; their validation and alarm evaluation live in `monitor.py`.

Only one function is required. Cloud Scheduler calls it; Scheduler, BigQuery, Storage, and Secret Manager are supporting GCP resources, not additional function folders. No stored procedures are required. KPI rules execute a single SELECT directly.

## 2. Projects and service accounts

Use these names throughout the manual, replacing the example values:

| Name | Meaning |
|---|---|
| Main project | Hosts the function, metadata tables, state bucket, SMTP secret, and Scheduler job; pays for BigQuery query jobs |
| Monitored project | Contains logs or source BigQuery datasets to read; repeat source grants for every such project |
| Runtime account | `gcp-monitor-runtime@MAIN_PROJECT.iam.gserviceaccount.com`; selected as the function's execution identity |
| Builder account | `gcp-monitor-builder@MAIN_PROJECT.iam.gserviceaccount.com`; used only to build source |
| Scheduler account | `gcp-monitor-scheduler@MAIN_PROJECT.iam.gserviceaccount.com`; used only to invoke the function |

**Grant access in monitored projects to the runtime account from the main project.** Do not create a new runtime account in every monitored project or download account keys.

Commands below use Bash in Google Cloud Shell, from the repository root. Install a current Google Cloud CLI if working locally. The deployment runtime is Python 3.12.

```bash
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
```

### Exact IAM role matrix (per service account)

Grant the following roles to the **named service account**, at the **specified resource scope**. The three accounts have different purposes; do not give the Builder or Scheduler the Runtime account's data permissions. "Conditional" means the role is needed only if the corresponding monitoring feature is enabled.

| Service account (principal) | IAM role | Grant on | Required for |
|---|---|---|---|
| `RUNTIME_SA` (`gcp-monitor-runtime@MAIN_PROJECT.iam.gserviceaccount.com`) | `roles/bigquery.jobUser` | **MAIN_PROJECT** (project) | Always: creates and bills BigQuery query/load jobs here |
| `RUNTIME_SA` | `roles/bigquery.dataViewer` | **MAIN_PROJECT.monitoring** (metadata dataset) | Always: reads `monitoring_rules` and `notification_profiles`; replace `monitoring` if configured differently |
| `RUNTIME_SA` | `roles/bigquery.dataEditor` | **MAIN_PROJECT.monitoring.monitoring_results** (table) | Always: appends check results |
| `RUNTIME_SA` | `roles/bigquery.dataEditor` | **MAIN_PROJECT.monitoring.monitoring_runs** (table) | Always: appends run history |
| `RUNTIME_SA` | `roles/storage.objectUser` | **gs://STATE_BUCKET** (bucket) | Always: creates, reads, and updates execution state and reports |
| `RUNTIME_SA` | `roles/logging.viewer` | **Each project with monitored logs** | Conditional: ordinary `LOG` checks, including Cloud Run/Scheduler/Workflows/Dataform log checks |
| `RUNTIME_SA` | `roles/logging.privateLogViewer` | **Each project whose private logs are monitored** | Conditional: Data Access/private logs; **use instead of** `roles/logging.viewer` for that project (it includes ordinary log access) |
| `RUNTIME_SA` | `roles/bigquery.dataViewer` | **Each source dataset or individual source table** in a monitored project | Conditional: `SQL_KPI`, `FRESHNESS`, `ROW_COUNT`, `NULL_RATE`, `DUPLICATES` |
| `RUNTIME_SA` | `roles/secretmanager.secretAccessor` | **gcp-monitor-smtp-password** (one Secret Manager secret in MAIN_PROJECT) | Conditional: SMTP password provided through Secret Manager |
| `BUILDER_SA` (`gcp-monitor-builder@MAIN_PROJECT.iam.gserviceaccount.com`) | `roles/run.builder` | **MAIN_PROJECT** (project) | Always for source deployment using `--build-service-account` |
| `SCHEDULER_SA` (`gcp-monitor-scheduler@MAIN_PROJECT.iam.gserviceaccount.com`) | `roles/run.invoker` | **Deployed Cloud Run function/service only** | Required to call the function from Cloud Scheduler; grant after deployment |

**Where to assign roles:** Runtime's BigQuery job role and Builder's build role belong to the **main project**; read roles for external datasets and logs belong to the **monitored resources** (which may also be in the main project). The Scheduler invoker role belongs on the **Cloud Run service**, not on every service in the project. A source project does **not** need `roles/bigquery.jobUser` for this application because its jobs execute in MAIN_PROJECT. Follow sections 5–7 for exact `gcloud` and SQL grants.

**Deployment operator is a separate principal, not one of the three service accounts.** The user or CI identity running `gcloud run deploy` needs `roles/run.sourceDeveloper` and `roles/serviceusage.serviceUsageConsumer` on MAIN_PROJECT, plus `roles/iam.serviceAccountUser` on RUNTIME_SA **and** BUILDER_SA. The identity creating the Scheduler job also needs permission to create Scheduler jobs (for example `roles/cloudscheduler.admin` on MAIN_PROJECT) and `roles/iam.serviceAccountUser` on SCHEDULER_SA. Resource creation, API enablement, and IAM policy editing require separate administrator privileges; do not grant these to the runtime/build/scheduler accounts just to run monitoring. Avoid broad `roles/editor` or `roles/owner` grants.

The main project must have billing enabled. An administrator performs resource creation, API enablement, and IAM changes. These setup permissions are separate from the runtime account's permissions below. For source deployment, the deployer needs `roles/run.sourceDeveloper` and `roles/serviceusage.serviceUsageConsumer` in the main project, plus `roles/iam.serviceAccountUser` on the runtime and builder accounts. The Scheduler operator needs permission to create jobs and act as its Scheduler account. See [Google's deployment requirements](https://docs.cloud.google.com/run/docs/deploy-functions).

## 3. Create the supporting resources in the main project

Enable the required APIs and create the accounts once:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com bigquery.googleapis.com logging.googleapis.com \
  storage.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com \
  iam.googleapis.com --project="$MAIN_PROJECT"

gcloud iam service-accounts create gcp-monitor-runtime --project="$MAIN_PROJECT"
gcloud iam service-accounts create gcp-monitor-builder --project="$MAIN_PROJECT"
gcloud iam service-accounts create gcp-monitor-scheduler --project="$MAIN_PROJECT"

gcloud storage buckets create "gs://$STATE_BUCKET" --project="$MAIN_PROJECT" \
  --location="$REGION" --uniform-bucket-level-access --public-access-prevention
```

Reuse existing resources after checking their settings. Keep the state bucket private: it stores execution claims and reports. Google manages the build artifacts required for source deployment.

## 4. Execute the table-creation SQL

Open **[sql/create_monitoring_tables.sql](sql/create_monitoring_tables.sql)** and set its first four declarations:

| SQL variable | Example | Must match |
|---|---|---|
| `metadata_project` | `your-monitoring-project` | MAIN_PROJECT / GOOGLE_CLOUD_PROJECT |
| `metadata_dataset` | `monitoring` | DATASET_ID / METADATA_DATASET |
| `metadata_location` | `EU` | BigQuery editor query location / BQ_LOCATION |
| `history_retention_days` | `90` | Your history retention requirement |

Execute the **entire script** in the main project's BigQuery editor. Alternatively, after editing it:

```bash
bq query --project_id="$MAIN_PROJECT" --location="$BQ_LOCATION" \
  --use_legacy_sql=false < sql/create_monitoring_tables.sql
```

This is the only SQL file required to create objects:

| Created object | Purpose |
|---|---|
| Dataset `MAIN_PROJECT.monitoring` | Holds metadata and history; name is configurable |
| `notification_profiles` | Recipient addresses and report filtering |
| `monitoring_rules` | Projects, services, resources, links, check definitions, thresholds, routing |
| `monitoring_results` | Observations and snapshots of evaluated rules |
| `monitoring_runs` | Execution status, counts, and notification outcomes |

The result/run tables are partitioned and clustered; their default retention is 90 days. The script uses IF NOT EXISTS and preserves existing objects. It does not change the schema, location, defaults, or retention of an existing table. Apply explicit migrations if updating an existing installation.

## 5. Runtime permissions in the main project

All rows below apply to **RUNTIME_SA**. Assign roles at the listed scope, not indiscriminately to the entire project.

| Role | Scope in the main project | Why / when |
|---|---|---|
| `roles/bigquery.jobUser` | Main project | Start metadata queries, source KPI queries, and result load jobs |
| `roles/bigquery.dataViewer` | Metadata dataset | Read rules/profiles and existing table definitions |
| `roles/bigquery.dataEditor` | Table `monitoring_results` | Write evaluated results |
| `roles/bigquery.dataEditor` | Table `monitoring_runs` | Write run history |
| `roles/storage.objectUser` | State bucket only | Create/read execution claims and write private reports |
| `roles/secretmanager.secretAccessor` | SMTP password secret only | Read SMTP credentials; needed when email delivery is enabled |

```bash
gcloud projects add-iam-policy-binding "$MAIN_PROJECT" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/bigquery.jobUser

gcloud storage buckets add-iam-policy-binding "gs://$STATE_BUCKET" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/storage.objectUser
```

Run these SQL grants as an authorized administrator in the metadata location. Replace `MAIN_PROJECT` in both the resource names and account email; replace `monitoring` if you chose a different dataset:

```sql
GRANT `roles/bigquery.dataViewer`
ON SCHEMA `MAIN_PROJECT.monitoring`
TO 'serviceAccount:gcp-monitor-runtime@MAIN_PROJECT.iam.gserviceaccount.com';

GRANT `roles/bigquery.dataEditor`
ON TABLE `MAIN_PROJECT.monitoring.monitoring_results`
TO 'serviceAccount:gcp-monitor-runtime@MAIN_PROJECT.iam.gserviceaccount.com';

GRANT `roles/bigquery.dataEditor`
ON TABLE `MAIN_PROJECT.monitoring.monitoring_runs`
TO 'serviceAccount:gcp-monitor-runtime@MAIN_PROJECT.iam.gserviceaccount.com';
```

The SMTP secret and its grant are configured in step 10. If the main project also contains monitored logs or source tables, apply the relevant source grants from the next section there too.

## 6. Runtime permissions in every monitored project

Use the **same RUNTIME_SA email from the main project** in every grant.

| Check being used | Role | Scope in each monitored project |
|---|---|---|
| LOG checks | `roles/logging.viewer` | Project whose log entries are queried |
| Private logs, such as Data Access audit logs | `roles/logging.privateLogViewer` | Project; use only if selected filters require private logs; it includes ordinary log access |
| SQL_KPI, FRESHNESS, ROW_COUNT, NULL_RATE, DUPLICATES | `roles/bigquery.dataViewer` | Each source dataset, or each explicitly selected source table |

For ordinary logs, repeat for each relevant project:

```bash
export MONITORED_PROJECT="your-source-project"
gcloud services enable logging.googleapis.com --project="$MONITORED_PROJECT"
gcloud projects add-iam-policy-binding "$MONITORED_PROJECT" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/logging.viewer
```

If private/Data Access logs are monitored, replace the ordinary Logs Viewer grant in that project with `roles/logging.privateLogViewer`:

```bash
gcloud projects add-iam-policy-binding "$MONITORED_PROJECT" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/logging.privateLogViewer
```

For each source dataset, run this grant in that dataset's BigQuery location:

```sql
GRANT `roles/bigquery.dataViewer`
ON SCHEMA `MONITORED_PROJECT.SOURCE_DATASET`
TO 'serviceAccount:gcp-monitor-runtime@MAIN_PROJECT.iam.gserviceaccount.com';
```

Source BigQuery datasets must already exist. Source projects must have the relevant APIs enabled. The runtime account does not need API-administration permissions.

**BigQuery Job User belongs in the main project**, where this implementation creates and bills jobs. It is not additionally needed in a monitored project solely to read its tables. Queries use fully qualified source table names. Source datasets do not receive write grants. See [BigQuery IAM roles](https://docs.cloud.google.com/bigquery/docs/access-control).

Checks for Cloud Run, Scheduler, Workflows, and Dataform currently read their logs. They do not invoke those services or use their native execution-status APIs. No runtime `run.admin`, `workflows.invoker`, or `dataform.viewer` grant is required for these log checks. Custom log-view-only access, row-level security, policy-tag-protected columns, external tables, and remote functions can require additional configuration; review the actual source access rules before enabling such checks. The current log adapter queries project-level resources. See [Logging access control](https://docs.cloud.google.com/logging/docs/access-control).

## 7. Builder and Scheduler permissions

These are different identities from the runtime account:

| Identity | Role | Scope |
|---|---|---|
| BUILDER_SA | `roles/run.builder` | Main project |
| SCHEDULER_SA | `roles/run.invoker` | Deployed monitoring function only |

```bash
gcloud projects add-iam-policy-binding "$MAIN_PROJECT" \
  --member="serviceAccount:$BUILDER_SA" --role=roles/run.builder
```

Add the Scheduler invoker grant after deploying the function (step 11). Neither account needs access to source datasets or logs in monitored projects. Keep Google's managed Scheduler service agent permissions intact.

## 8. Add a profile and a monitoring rule

The tables start empty. **[sql/inserts/](sql/inserts/)** contains independent, complete BigQuery scripts. Add a recipient profile first, then run only the check scripts you need; there is no requirement to enable every example.

| Script | Creates | Example alarm condition |
|---|---|---|
| [01_notification_profile.sql](sql/inserts/01_notification_profile.sql) | One profile with one or more email addresses | Optional report filtering through `only_problems` |
| [02_log_rule.sql](sql/inserts/02_log_rule.sql) | A Cloud Run log check | More than zero matching error entries in 24 hours |
| [03_sql_kpi_rule.sql](sql/inserts/03_sql_kpi_rule.sql) | A custom SQL KPI | More than 5% of orders without a customer ID |
| [04_freshness_rule.sql](sql/inserts/04_freshness_rule.sql) | A data freshness check | Latest load older than 120 minutes |
| [05_row_count_rule.sql](sql/inserts/05_row_count_rule.sql) | A record-count check | Fewer than 100 records in 24 hours |
| [06_null_rate_rule.sql](sql/inserts/06_null_rate_rule.sql) | A NULL-rate template check | More than 5% NULL customer IDs |
| [07_duplicates_rule.sql](sql/inserts/07_duplicates_rule.sql) | A duplicate-key check | Any excess rows for an order ID |

Before running a script:

1. Replace `MAIN_PROJECT` and, if different, the `monitoring` metadata dataset in **both** destination table references.
2. Choose a unique `new_profile_id` or `new_rule_id` in the DECLARE statement. To add another check of the same type, copy the script and change that ID.
3. Replace `your-source-project`, source table/service, column names, region, URLs, recipients, and owner with real values. The `project_id` in a rule identifies the monitored project, not the main project.
4. Set `target_profiles` (for example `['data-team']`) to existing profile IDs. Multiple IDs route the same result to multiple profiles.
5. Review the alarm operator, threshold, time window, and no-data policy. For BigQuery checks, set `location` to the **source** dataset location and use TIMESTAMP columns.

Execute the whole selected script, including its DECLARE statement, in the **main metadata dataset's location**. In Cloud Shell, after editing a file:

```bash
bq query --project_id="$MAIN_PROJECT" --location="$BQ_LOCATION" \
  --use_legacy_sql=false < sql/inserts/01_notification_profile.sql

bq query --project_id="$MAIN_PROJECT" --location="$BQ_LOCATION" \
  --use_legacy_sql=false < sql/inserts/05_row_count_rule.sql
```

The files use INSERT ... SELECT ... WHERE NOT EXISTS. Sequential reruns skip an existing ID and do not change its configuration. This is not an atomic uniqueness guarantee for concurrent writers; do not submit simultaneous inserts for the same ID. Use an explicit UPDATE to edit an existing rule and increment `rule_version`.

Every example starts with `is_active = FALSE`. After reviewing the inserted values, enable the selected profile and rule (adapt the IDs if you changed them):

```sql
UPDATE `MAIN_PROJECT.monitoring.notification_profiles`
SET is_active = TRUE WHERE profile_id = 'data-team';
UPDATE `MAIN_PROJECT.monitoring.monitoring_rules`
SET is_active = TRUE WHERE rule_id = 'orders-row-count';
```

Then invoke the function with `{"preview":true}` as described in step 9. Preview evaluates active rules; it does not evaluate disabled drafts. Keep email delivery disabled until you have inspected the report. The SQL_KPI and NULL_RATE examples illustrate two ways to check the same condition, so normally choose one of them.

To monitor a **new project or service**, grant the source access described in step 6 and insert a rule pointing to that resource. There is no separate project-registration table. For Scheduler, Workflows, or Dataform logs, adapt the LOG template's `service_type` and use a resource-specific filter verified in Logs Explorer. Do not insert manually into `monitoring_results` or `monitoring_runs`; the function writes those history tables.

One row represents one check. IDs must be unique, referenced profiles must be active, and only explicit `is_active = TRUE` rows are evaluated. Configuration authors are trusted operators because SQL executes with the runtime's data access.

| Rule field | Meaning |
|---|---|
| `project_id`, `service_type` | Monitored project and service category |
| `resource_name`, `resource_url` | Readable resource name and HTTPS console link |
| `resource_id` | Optional descriptive resource ID; does not automatically scope a check |
| `check_type` | LOG, SQL_KPI, FRESHNESS, ROW_COUNT, NULL_RATE, DUPLICATES |
| `log_filter` | Resource-specific Cloud Logging filter for LOG |
| `kpi_query` | Single SELECT returning exactly one numeric column named `value` and one row |
| `check_config` | JSON settings for built-in SQL templates |
| `comparison_operator`, `threshold_value`, `threshold_upper` | Alarm condition and numeric bounds |
| `window_minutes`, `location` | Rolling observation window and source BigQuery location |
| `target_profiles`, `owner`, `pipeline_id`, `runbook_url` | Recipient routing and operational context |
| `rule_version`, `severity`, `no_data_policy` | Configuration version, priority, and behavior for NULL results |

Comparisons describe **alarm conditions**: LT 100 alerts below 100; GT 0 alerts above zero. Supported operators: GT, GE, LT, LE, EQ, NE, OUTSIDE_RANGE. Range boundaries are acceptable values. `no_data_policy` is NO_DATA, ALERT, or OK. Zero is an observation, not missing data. Invalid result shapes and NaN/infinity are ERRORs.

For LOG, provide a filter such as `resource.type="cloud_run_revision" AND resource.labels.service_name="orders-ingestion" AND severity>=ERROR`, set the service category to CLOUD_RUN, and use GT 0. Display names do not filter logs automatically. A zero error count does not prove that a process executed.

Built-in SQL templates use `check_config` with `table` (fully qualified) and `timestamp_column` (TIMESTAMP). ROW_COUNT counts window rows; FRESHNESS returns age in minutes of the last timestamp before the window end, looking across history. NULL_RATE additionally needs `column` and returns a fraction from 0 to 1. DUPLICATES additionally needs a non-empty `key_columns` array and counts excess rows in the window. SQL parameters `@window_start` and `@window_end` define a half-open interval. Increment `rule_version` when changing a rule; each result stores its evaluated configuration.

## 9. Deploy functions/monitoring/ as one function

### Function settings

| Setting | Value |
|---|---|
| Source folder | **functions/monitoring/** |
| Function entry point | **run_monitoring** |
| Runtime | Python 3.12 |
| Trigger | HTTP |
| Authentication | Required |
| Execution service account | RUNTIME_SA |
| Build service account | BUILDER_SA |
| Memory / request timeout | 512 MiB / 1,800 seconds |
| Concurrency / maximum instances | 1 / 1 |

In the Cloud Run console, use **Write a function**, select Python 3.12, require authentication, and set the execution identity and runtime limits. In the source editor or supported source upload workflow, preserve all files from `functions/monitoring/`, with `main.py` and `requirements.txt` directly at source root. Set entry point `run_monitoring`. Source upload controls can vary; the following CLI command is the reproducible deployment path.

### Environment variables

Create a private `deploy.env.yaml` file **at the repository root**, outside the function folder. Copy this block into it and replace the project/bucket values:

```yaml
GOOGLE_CLOUD_PROJECT: "your-monitoring-project"
METADATA_DATASET: "monitoring"
BQ_LOCATION: "EU"
STATE_BUCKET: "your-monitoring-project-monitor-state"
REPORT_TIMEZONE: "Europe/Warsaw"
MAXIMUM_BYTES_BILLED: "1073741824"
QUERY_TIMEOUT_SECONDS: "60"
LOG_ENTRY_LIMIT: "10000"
RUN_BUDGET_SECONDS: "1500"
NOTIFICATIONS_ENABLED: "false"
SMTP_HOST: "smtp.example.com"
SMTP_PORT: "587"
SMTP_USER: ""
SMTP_FROM: "monitor@example.com"
SMTP_SECURITY: "starttls"
```

The first four settings identify the hosting resources. `BQ_LOCATION` refers to metadata; source query locations are set per rule. Keep notification delivery disabled for the first preview. All YAML values are strings. SMTP_PASS is supplied through Secret Manager, not this file. The environment file is ignored by Git.

From the repository root:

```bash
gcloud run deploy "$FUNCTION_NAME" --project="$MAIN_PROJECT" --region="$REGION" \
  --source=functions/monitoring --function=run_monitoring --base-image=python312 \
  --build-service-account="projects/$MAIN_PROJECT/serviceAccounts/$BUILDER_SA" \
  --service-account="$RUNTIME_SA" --env-vars-file=deploy.env.yaml \
  --no-allow-unauthenticated --invoker-iam-check \
  --timeout=1800 --memory=512Mi --concurrency=1 --max-instances=1

export FUNCTION_URL="$(gcloud run services describe "$FUNCTION_NAME" \
  --project="$MAIN_PROJECT" --region="$REGION" --format='value(status.url)')"
```

Do not make the function public. If updating an existing service, inspect and remove any previous public invoker grants. Source builds and runtime artifacts are managed by GCP; no Docker commands or image configuration files are needed.

### Preview

Grant an authorized testing identity invoker access if needed, then call the function:

```bash
gcloud run services add-iam-policy-binding "$FUNCTION_NAME" \
  --project="$MAIN_PROJECT" --region="$REGION" \
  --member="user:YOUR_EMAIL" --role=roles/run.invoker

curl --fail-with-body -X POST "$FUNCTION_URL" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" -d '{"preview":true}'
```

Preview runs real queries and saves results/reports, but sends no email. Check `monitoring_results` and `monitoring_runs`, and use the returned run ID to download the private report:

```bash
gcloud storage cp "gs://$STATE_BUCKET/reports/RUN_ID/data-team.html" ./preview.html
```

## 10. Enable email delivery

In the main project, create the password secret once and add its value without placing it in code or command history:

```bash
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
```

Configure the SMTP provider values in `deploy.env.yaml`, verify the sender/domain with your provider, and set `NOTIFICATIONS_ENABLED: "true"`. Use STARTTLS/587 or SSL/465 according to the provider.

```bash
# Replace 1 with the actual secret version.
gcloud run services update "$FUNCTION_NAME" --project="$MAIN_PROJECT" --region="$REGION" \
  --env-vars-file=deploy.env.yaml --update-secrets=SMTP_PASS=gcp-monitor-smtp-password:1
```

Repeat the authenticated POST with `{"preview":false}` and verify delivery. Both this flag and the environment setting must allow email. Use Secret Manager IAM on this single secret, not project-wide secret access.

## 11. Schedule the deployed function

After validating a report and its recipients:

```bash
gcloud run services add-iam-policy-binding "$FUNCTION_NAME" \
  --project="$MAIN_PROJECT" --region="$REGION" \
  --member="serviceAccount:$SCHEDULER_SA" --role=roles/run.invoker

gcloud scheduler jobs create http gcp-monitor-daily --project="$MAIN_PROJECT" \
  --location="$REGION" --schedule='0 8 * * *' --time-zone='Europe/Warsaw' \
  --uri="$FUNCTION_URL" --http-method=POST \
  --oidc-service-account-email="$SCHEDULER_SA" --oidc-token-audience="$FUNCTION_URL" \
  --headers='Content-Type=application/json' --message-body='{"preview":false}' \
  --attempt-deadline=1800s --max-retry-attempts=0 --max-retry-duration=0s
```

The OIDC audience is the base function URL. Job creation activates the schedule. Use preview=true for scheduled previews, or pause the job while editing configuration:

```bash
gcloud scheduler jobs pause gcp-monitor-daily --project="$MAIN_PROJECT" --location="$REGION"
```

## 12. Verify and operate

Check these before relying on the daily report:

1. The function uses RUNTIME_SA and source `functions/monitoring/`, with entry point `run_monitoring`.
2. The four tables exist in the configured dataset/location, with at least one valid active rule/profile.
3. Runtime IAM is applied separately in the main project and every selected source project/dataset.
4. A preview produces numerical results and a private HTML artifact.
5. An intentional live request delivers the expected report.
6. Scheduler receives a successful response, and an independent alert detects failed/missing scheduled runs.

| Symptom | Check |
|---|---|
| 401/403 invoking the function | Invoker role, IAM authentication, Scheduler identity, OIDC audience |
| Rule ERROR / source access denied | Runtime grants in the source project/dataset, query location, filter, scalar result contract |
| Failed persistence | Main-project jobUser, history-table dataEditor, bucket objectUser |
| Report exists but email is absent | Preview flag, NOTIFICATIONS_ENABLED, SMTP/TLS, secret access/version, sender verification |
| Empty configuration / HTTP 500 | Explicit is_active flags, valid unique IDs, active recipient profiles |
| HTTP 409 | Existing execution claim; inspect it before replaying |

Rule statuses are OK, ALERT, ERROR, NO_DATA. Run status NEEDS_ATTENTION covers empty/invalid configuration, failed checks, or failed delivery and returns HTTP 500. COMPLETED may still contain valid ALERT/NO_DATA results. FAILED means orchestration or persistence failed. Notification status distinguishes PREVIEW, DISABLED, SENT, and FAILED.

Scheduled runs use an atomic claim in `gs://STATE_BUCKET/runs/RUN_ID.json`. Completed duplicates do not repeat work. Failed or interrupted claims require inspection; SMTP acceptance and state writes are not a transaction, so exactly-once email is not guaranteed. Review BigQuery history, Storage state, function logs, and your SMTP provider before manually retrying. A manual preview can specify a timezone-aware `window_end`; scheduled requests use their scheduled timestamp and cannot override it. If the process is killed or Storage is unavailable, the last persisted checkpoint remains.

The function evaluates sequentially, batches result writes, and checks a 25-minute orchestration budget. SQL has a default 1 GiB scan cap and 60-second result wait; log reads share a deadline across pages and stop with ERROR above 10,000 entries. Use modest rule sets and compact audit tables for expensive freshness checks. Cancellation is best effort and may incur charges.

Windows are rolling, not previous local calendar days. A 1,440-minute window with a local-time daily schedule can overlap or leave gaps at DST transitions. Use UTC scheduling for uniform windows or explicit calendar SQL. GET /healthz checks process liveness only. Independently monitor missing `monitor_run_finished ... status=COMPLETED` logs and failed Scheduler calls.

History-table partitions default to 90 days. Configure private report retention separately from execution claims; deleting claims shortens the deduplication horizon. GCP builds, BigQuery, runtime requests, Storage, Secret Manager, and SMTP may incur costs.

To update code, redeploy the same function folder and run a preview. Existing installations managed outside this manual require an explicit ownership/migration decision before changing them. This repository contains deployable function source, object-creation/configuration SQL, and documentation; development tests and CI configuration are not part of the deployment package. Live SQL, IAM, builds, and SMTP must be verified in your own project.

MIT license; see [LICENSE](LICENSE).
