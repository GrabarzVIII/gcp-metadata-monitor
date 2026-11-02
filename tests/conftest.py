import pytest

from src.config import Settings
from src.models import Rule


@pytest.fixture
def settings():
    return Settings(_env_file=None, google_cloud_project="monitor-project")


@pytest.fixture
def rule():
    return Rule(
        rule_id="orders",
        rule_name="Order count",
        project_id="source-project",
        service_type="BIGQUERY",
        resource_name="orders",
        check_type="SQL_KPI",
        kpi_query="SELECT COUNT(*) AS value FROM `source-project.raw.orders`",
        comparison_operator="LT",
        threshold_value=100,
        target_profiles=["ops"],
    )
