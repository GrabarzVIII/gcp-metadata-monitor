"""Offline demo, rule validation, and live preview commands."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.engine import check_rule
from src.models import Profile, Rule
from src.report_generator import render_report


class DemoBackend:
    """Deterministic synthetic observations; never calls Google Cloud."""

    def execute_kpi(self, rule: Rule, start: datetime, end: datetime) -> float | None:
        if rule.rule_id == "query-error":
            raise RuntimeError("Synthetic query failure")
        return {"freshness": 145.0, "orders": 1480.0, "missing-data": None}.get(rule.rule_id, 0.0)

    def count(self, rule: Rule, start: datetime, end: datetime) -> float:
        return 3.0


def demo(output: Path) -> None:
    backend = DemoBackend()
    end = datetime.now(UTC)
    definitions = [
        ("orders", "Daily order volume", "ROW_COUNT", "LT", 1000),
        ("freshness", "Warehouse freshness (minutes)", "FRESHNESS", "GT", 120),
        ("service-errors", "Cloud Run error logs", "LOG", "GT", 0),
        ("missing-data", "Revenue KPI without observations", "SQL_KPI", "LT", 100),
        ("query-error", "Example query failure", "SQL_KPI", "GT", 0),
    ]
    rules = [
        Rule(
            rule_id=rule_id,
            rule_name=name,
            project_id="example-project",
            service_type="CLOUD_RUN" if kind == "LOG" else "BIGQUERY",
            resource_name="orders-ingestion" if kind == "LOG" else "warehouse.orders",
            check_type=kind,
            comparison_operator=operator,
            threshold_value=threshold,
            log_filter='resource.type="cloud_run_revision"' if kind == "LOG" else None,
            kpi_query="SELECT 1 AS value" if kind == "SQL_KPI" else None,
            target_profiles=["demo"],
            owner="Data Platform",
            pipeline_id="orders",
        )
        for rule_id, name, kind, operator, threshold in definitions
    ]
    results = [check_rule(rule, "offline-demo", end, backend, backend) for rule in rules]
    profile = Profile(profile_id="demo", email_addresses=["demo@example.com"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "daily_report.html").write_text(
        render_report(profile, results, "Europe/Warsaw") or "",
        encoding="utf-8",
    )
    (output / "results.json").write_text(
        json.dumps([result.model_dump(mode="json") for result in results], indent=2),
        encoding="utf-8",
    )
    print(f"Offline demo written to {output.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo_parser = commands.add_parser("demo", help="Generate a report without cloud credentials")
    demo_parser.add_argument("--output", type=Path, default=Path("reports/demo"))
    validate_parser = commands.add_parser("validate", help="Validate a JSON array of rules")
    validate_parser.add_argument("file", type=Path)
    commands.add_parser(
        "preview", help="Evaluate live rules and store reports without sending email"
    )
    args = parser.parse_args()
    if args.command == "demo":
        demo(args.output)
    elif args.command == "validate":
        from src.checks import build_query

        definitions = json.loads(args.file.read_text(encoding="utf-8"))
        ids = set()
        for definition in definitions:
            rule = Rule.model_validate(definition)
            if rule.rule_id in ids:
                raise ValueError(f"Duplicate rule_id: {rule.rule_id}")
            ids.add(rule.rule_id)
            if rule.check_type != "LOG":
                build_query(rule)
        print(f"Validated {len(ids)} rules. SQL_KPI syntax is validated by BigQuery at execution.")
    else:
        from src.bq_client import BigQueryStore
        from src.config import get_settings
        from src.engine import Monitor
        from src.logging_client import LogChecks
        from src.notification import EmailSender
        from src.state import RunState

        settings = get_settings()
        monitor = Monitor(
            settings,
            BigQueryStore(settings),
            LogChecks(settings),
            RunState(settings.state_bucket),
            EmailSender(settings),
        )
        print(json.dumps(monitor.run(str(uuid4()), datetime.now(UTC), preview=True), indent=2))


if __name__ == "__main__":
    main()
