"""Admin > Reports: one agent's tasks, work and deals for a chosen period,
plus an Excel version of the same figures. Limited to managers and admins,
same as the other reporting screens."""
from flask import Blueprint, g, redirect, render_template, request, send_file, url_for

import excel_export
import marketing
import reports
from auth import manager_required
from db import LOST_REASON_LABELS, log, query, utc_day_bounds

bp = Blueprint("reports", __name__, url_prefix="/admin/reports")


def _period_type():
    period_type = request.args.get("period_type", "week")
    return period_type if period_type in dict(reports.PERIOD_TYPES) else "week"


def _pick_agent(agents):
    """Which agent is selected, falling back to the first one alphabetically
    so the page always has something to show without an extra click."""
    if not agents:
        return None
    requested = request.args.get("agent", type=int)
    ids = [a["id"] for a in agents]
    agent_id = requested if requested in ids else agents[0]["id"]
    return next(a for a in agents if a["id"] == agent_id)


def _agents():
    return query("SELECT id, name, role, job_title, department, photo FROM users"
                 " WHERE is_active = 1 ORDER BY name")


@bp.route("/")
@manager_required
def index():
    period_type = _period_type()
    ref = request.args.get("ref", "")
    agents = _agents()
    agent = _pick_agent(agents)
    report = reports.agent_report(agent["id"], period_type, ref) if agent else None
    overview = reports.agent_overview(agent["id"]) if agent else None
    return render_template("admin/reports.html", agents=agents, agent=agent,
                           report=report, overview=overview,
                           period_types=reports.PERIOD_TYPES,
                           lost_labels=LOST_REASON_LABELS,
                           period_type=period_type)


@bp.route("/export.xlsx")
@manager_required
def export():
    period_type = _period_type()
    ref = request.args.get("ref", "")
    agent = _pick_agent(_agents())
    if agent is None:
        return redirect(url_for("reports.index"))

    report = reports.agent_report(agent["id"], period_type, ref)
    buf = excel_export.agent_report_workbook(report, agent, g.user["name"])
    stamp = report["period"]["start"].isoformat()
    name = agent["name"].replace(" ", "-").lower()
    log(g.user["id"], "Exported an agent report", "user", agent["id"],
        f"{period_type} · {report['period']['label']}")
    return send_file(
        buf, as_attachment=True,
        download_name=f"agent-report-{name}-{period_type}-{stamp}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/marketing")
@manager_required
def marketing_report():
    range_key = request.args.get("range", "last_30d")
    if range_key not in dict(marketing.RANGES):
        range_key = "last_30d"
    date_from, date_to = marketing.range_dates(range_key)

    connector = "facebook"
    info = marketing.CONNECTORS[connector]
    ok, result = (False, None)
    summary = None
    key_missing = not marketing.api_key_present()
    if not key_missing:
        ok, result = marketing.fetch(connector, date_from, date_to)
        if ok:
            summary = marketing.summarize(result)

    # The CRM's own count of leads from the channels this connector covers,
    # over the same window, so spend can be set next to what it actually
    # produced without waiting on Meta's own conversion tracking (which
    # this account isn't set up to report - see actions_lead in the raw
    # feed) to be configured.
    leads_count = None
    if summary is not None:
        window_start = utc_day_bounds(date_from)[0]
        window_end = utc_day_bounds(date_to)[1]
        placeholders = ",".join("?" * len(info["lead_sources"]))
        row = query(
            f"SELECT COUNT(*) AS n FROM leads WHERE created_at >= ?"
            f" AND created_at < ? AND source IN ({placeholders})",
            [window_start, window_end] + info["lead_sources"], one=True)
        leads_count = row["n"] if row else 0

    return render_template(
        "admin/reports_marketing.html",
        ranges=marketing.RANGES, range_key=range_key,
        date_from=date_from, date_to=date_to,
        connector_label=info["label"], lead_sources=info["lead_sources"],
        key_missing=key_missing, ok=ok, error=(result if not ok else None),
        summary=summary, leads_count=leads_count)
