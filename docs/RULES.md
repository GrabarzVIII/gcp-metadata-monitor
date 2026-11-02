# Rule configuration

| Field | Purpose |
|---|---|
| `rule_id`, `rule_name`, `rule_version` | Stable identity, readable name, operator-maintained version |
| `project_id` | Monitored project; scopes log reads |
| `service_type` | CLOUD_RUN, SCHEDULER, WORKFLOWS, DATAFORM, BIGQUERY |
| `resource_name`, `resource_id`, `resource_url` | Display name, optional canonical ID, HTTPS link |
| `location` | BigQuery query location; default EU |
| `check_type` | LOG, SQL_KPI, FRESHNESS, ROW_COUNT, NULL_RATE, DUPLICATES |
| `log_filter` | Explicit resource-scoped Logging filter |
| `kpi_query` | Single scalar SELECT |
| `check_config` | Template parameters as JSON |
| `comparison_operator` | Alarm comparison, not a success condition |
| `threshold_value`, `threshold_upper` | Numeric alarm bounds |
| `window_minutes` | Rolling window; default 1,440 |
| `severity` | INFO, WARNING, CRITICAL |
| `no_data_policy` | NO_DATA, ALERT, OK |
| `target_profiles` | Recipient profile IDs |
| `pipeline_id`, `owner`, `runbook_url` | Context and response instructions |
| `is_active` | Explicit TRUE enables loading |

`resource_id` is descriptive in this release. SQL templates use `check_config.table`; custom SQL uses its own fully qualified tables. Setting a display name alone does not scope the API query.

## Comparisons and missing data

`GT`, `GE`, `LT`, `LE`, `EQ`, and `NE` have normal numeric meanings. `OUTSIDE_RANGE` alerts below the lower bound or above the upper bound; both boundaries are acceptable. Evaluation uses floating-point values. Use integer minor units for currency and avoid exact equality on computed ratios.

A NULL scalar applies `no_data_policy`. Empty result sets, multiple rows/columns, non-numeric values, and NaN/infinity are errors. Zero is an observation. A zero log count does not prove a process ran.

## SQL templates

Templates require a fully qualified table and simple TIMESTAMP column:

```json
{"table":"source-project.raw.orders","timestamp_column":"created_at"}
```

- `ROW_COUNT`: counts rows in `[window_start, window_end)`.
- `FRESHNESS`: minutes from the latest timestamp before `window_end`. Looks beyond `window_start` so old data is stale rather than missing. Empty tables return NULL; future timestamps are excluded.
- `NULL_RATE`: additionally requires `"column":"customer_id"`; returns a fraction from 0 to 1, or NULL for an empty window.
- `DUPLICATES`: additionally requires `"key_columns":["order_id"]`; counts excess rows within the window. Empty windows return zero; NULL keys follow BigQuery grouping semantics.

For large freshness checks, query a compact load-audit table to reduce scanning.

## Custom KPI

```sql
SELECT SAFE_DIVIDE(COUNTIF(customer_id IS NULL), COUNT(*)) AS value
FROM `source-project.raw.orders`
WHERE created_at >= @window_start AND created_at < @window_end
```

Use `GT` with `0.05` to alert above 5%. Parameters are TIMESTAMP values. Queries must return exactly one row and one numeric column named `value`. Fully qualify all tables; query billing belongs to the hosting project.

BigQuery parses the query in a dry run. The engine rejects non-SELECT statement types, checks estimated scan size, and sets a runtime scan cap. Dataset IAM remains the data-access boundary. SQL authors are trusted operators, especially when using remote functions or external sources.

## Expected execution through logs

Filter for a verified successful-completion event and alert on `LT 1` to approximate an expected execution within a rolling window. Inspect actual service logs before defining the filter. A generic HTTP 200 does not prove downstream pipeline completion. Native status APIs, business-day deadlines, and grace periods are planned separately.

## Validation and versions

```bash
python -m src.cli validate examples/rules.json
```

Validation checks fields, links, duplicate IDs, and template identifiers. Custom SQL syntax/permissions require a live preview. Adapt the example identifiers before live use.

Increment `rule_version` when changing scope, SQL, or thresholds. Each result stores the evaluated rule snapshot. Automatic configuration revision management is not included.
