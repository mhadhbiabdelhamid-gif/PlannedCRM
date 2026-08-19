"""Financial section: commission payouts to agents. Visible to admins,
managers, and the accountant role (see auth.sees_finance())."""
import os
import uuid

from flask import (Blueprint, current_app, flash, g, redirect,
                   render_template, request, send_file, url_for)

import excel_export
import finance
from auth import finance_required
from db import PAYOUT_STATUS, local_now, log, now, query, to_utc

bp = Blueprint("finance", __name__, url_prefix="/finance")


def _filters():
    return {
        "status": request.args.get("status", ""),
        "agent_id": request.args.get("agent", type=int),
        "q": request.args.get("q", "").strip(),
    }


@bp.route("/")
@finance_required
def index():
    f = _filters()
    rows = finance.payouts(**f)
    agents = query("SELECT id, name FROM users WHERE is_active = 1 ORDER BY name")
    return render_template("finance/index.html", rows=rows, totals=finance.summary(rows),
                           agents=agents, payout_status=PAYOUT_STATUS,
                           today=local_now().strftime("%Y-%m-%d"), **f)


@bp.route("/pay", methods=("POST",))
@finance_required
def pay():
    d = request.form
    daid = d.get("deal_agent_id", type=int)
    try:
        amount = float(d.get("amount") or 0)
    except ValueError:
        amount = 0

    if daid is None:
        flash("That payout row could not be found.", "error")
    elif amount <= 0:
        flash("Enter an amount greater than zero.", "error")
    else:
        paid_at = to_utc(d.get("paid_at")) or now()
        finance.record_payment(daid, amount, paid_at, d.get("note", "").strip(),
                               g.user["id"])
        flash("Payout recorded.", "ok")
    return redirect(url_for("finance.index"))


@bp.route("/import", methods=("GET", "POST"))
@finance_required
def import_payouts():
    if request.method == "POST":
        fs = request.files.get("file")
        if not fs or not fs.filename:
            flash("Choose an Excel file first.", "error")
            return redirect(url_for("finance.import_payouts"))

        folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "imports")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{uuid.uuid4().hex}.xlsx")
        fs.save(path)
        try:
            results = finance.import_payouts_file(path, g.user["id"])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

        if "error" in results:
            flash(results["error"], "error")
            return redirect(url_for("finance.import_payouts"))

        log(g.user["id"], "Imported commission payouts",
            detail=f"{results['updated']} updated, "
                   f"{len(results['not_found'])} not found, {results['skipped']} skipped")
        return render_template("finance/import.html", results=results)

    return render_template("finance/import.html", results=None)


@bp.route("/export.xlsx")
@finance_required
def export():
    f = _filters()
    rows = finance.payouts(**f)
    buf = excel_export.payouts_workbook(rows, finance.summary(rows), g.user["name"])
    stamp = local_now().strftime("%Y-%m-%d")
    log(g.user["id"], "Exported commission payouts")
    return send_file(
        buf, as_attachment=True, download_name=f"commission-payouts-{stamp}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
