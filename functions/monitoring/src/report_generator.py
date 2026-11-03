"""Render escaped HTML reports, restricted to each recipient profile."""

from datetime import datetime
from importlib.resources import files
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.models import Profile, Result, Status


def render_report(profile: Profile, results: list[Result], timezone: str) -> str | None:
    selected = [
        result
        for result in results
        if profile.profile_id in result.target_profiles
        and (not profile.only_problems or result.status != Status.OK)
    ]
    if not selected:
        return None
    environment = Environment(
        loader=FileSystemLoader(str(files("src").joinpath("templates"))),
        autoescape=select_autoescape(["html"]),
    )
    environment.filters["localtime"] = lambda dt: dt.astimezone(ZoneInfo(timezone)).strftime(
        "%Y-%m-%d %H:%M %Z"
    )
    return environment.get_template("daily_report.html").render(
        results=selected,
        profile=profile,
        generated_at=datetime.now(ZoneInfo(timezone)),
        problems=sum(result.status != Status.OK for result in selected),
    )
