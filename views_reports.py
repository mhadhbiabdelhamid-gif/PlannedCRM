"""Admin > Reports: one agent's tasks, work and deals for a chosen period,
plus an Excel version of the same figures. Limited to managers and admins,
same as the other reporting screens."""
from flask import Blueprint, g, redirect, render_template, request, send_file, url_for

import excel_export
import reports
from auth import manager_required
from db import log, query

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
    return render_template("admin/reports.html", agents=agents, agent=agent,
                           report=report, period_types=reports.PERIOD_TYPES,
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
