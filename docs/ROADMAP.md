# Roadmap

The first release implements scheduled log and SQL/data-quality checks with durable results.

## Monitoring coverage

- Native Dataform and Workflows execution-status adapters.
- Cloud Monitoring metrics with explicit alignment/reduction.
- Expected completion deadlines, business calendars, and grace periods.
- Previous local-calendar-day windows with DST handling.
- Independently provisioned heartbeat alerts.

## Incident handling

- Consecutive-failure thresholds, recovery notifications, reminders.
- Maintenance windows that preserve observations while suppressing notifications.
- Incident records, acknowledgements, and pipeline dependencies.
- Delivery ledger handling ambiguous SMTP acceptance.

## Scale and authoring

- Per-rule schedules and separate notification schedules.
- Queue-backed, resumable execution with bounded concurrency.
- Historical baselines with seasonality and minimum samples.
- Rule-authoring UI, previews, revision history, and dashboard.

These are planned capabilities, not current guarantees. New adapters need contract tests and live integration tests before being described as supported.
