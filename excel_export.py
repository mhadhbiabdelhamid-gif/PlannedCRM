"""Branded Excel exports.

Produces the workbook agreed in the template draft: a company header block on
every sheet, a printed footer, and the property listings split into ours and
everyone else's. Company details come from Settings, so changing the address
there changes every future export.
"""
import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins

from db import get_setting, local_now, to_local

# ------------------------------------------------------------------ brand
GOLD = "C8A24A"
GOLD_PALE = "F6EFDD"
INK = "0B0B0D"
PAPER = "F4F1EA"
LINE = "DBD7CE"
MUTED = "4E4B44"
LINK = "0563C1"
FONT = "Arial"

thin = Side(style="thin", color=LINE)
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)

HEADER_ROW = 8
FIRST_DATA = 9


def company():
    return {
        "name": get_setting("company_name", "Planned Real Estate"),
        "tagline": get_setting("tagline", "Property Consultants · Doha, Qatar"),
        "address": get_setting("address", ""),
        "po_box": get_setting("po_box", ""),
        "phone": get_setting("phone", ""),
        "phone2": get_setting("phone2", ""),
        "email": get_setting("email", ""),
        "cr": get_setting("cr_number", ""),
        "currency": get_setting("currency", "QAR"),
    }


def _join(parts, sep="   ·   "):
    return sep.join(p for p in parts if p)


def brand_header(ws, co, title, subtitle, ncols, exported_by):
    last = get_column_letter(ncols)

    ws.merge_cells(f"A1:{last}1")
    c = ws["A1"]
    c.value = co["name"]
    c.font = Font(name=FONT, size=20, bold=True, color=GOLD)
    c.fill = PatternFill("solid", fgColor=INK)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 34

    ws.merge_cells(f"A2:{last}2")
    c = ws["A2"]
    c.value = co["tagline"]
    c.font = Font(name=FONT, size=9, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor=INK)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[2].height = 16

    lines = [
        _join([co["address"], co["po_box"]]),
        _join([f"Tel {co['phone']}" if co["phone"] else "", co["phone2"],
               co["email"], f"CR {co['cr']}" if co["cr"] else ""]),
    ]
    for row, text in zip((3, 4), lines):
        ws.merge_cells(f"A{row}:{last}{row}")
        c = ws[f"A{row}"]
        c.value = text
        c.font = Font(name=FONT, size=8.5, color=MUTED)
        c.fill = PatternFill("solid", fgColor=GOLD_PALE)
        c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[row].height = 15

    ws.merge_cells(f"A5:{last}5")
    c = ws["A5"]
    c.value = title
    c.font = Font(name=FONT, size=13, bold=True, color=INK)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[5].height = 24

    ws.merge_cells(f"A6:{last}6")
    c = ws["A6"]
    stamp = local_now().strftime("%d %B %Y at %H:%M")
    c.value = f"{subtitle}   |   Exported {stamp} (Doha time) by {exported_by}"
    c.font = Font(name=FONT, size=8.5, italic=True, color=MUTED)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[6].height = 14

    ws.row_dimensions[7].height = 6


def table_header(ws, headers):
    for i, text in enumerate(headers, start=1):
        c = ws.cell(row=HEADER_ROW, column=i, value=text)
        c.font = Font(name=FONT, size=9.5, bold=True, color=INK)
        c.fill = PatternFill("solid", fgColor=GOLD)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    ws.row_dimensions[HEADER_ROW].height = 30


def write_rows(ws, rows, formats):
    for r, record in enumerate(rows, start=FIRST_DATA):
        banded = (r - FIRST_DATA) % 2 == 1
        for i, value in enumerate(record, start=1):
            c = ws.cell(row=r, column=i, value=value)
            c.font = Font(name=FONT, size=9.5, color="000000")
            c.border = BORDER
            c.alignment = Alignment(horizontal=formats[i - 1][1], vertical="center")
            if formats[i - 1][0]:
                c.number_format = formats[i - 1][0]
            if banded:
                c.fill = PatternFill("solid", fgColor=PAPER)
    return FIRST_DATA + len(rows) - 1


def map_links(ws, col, first, last):
    """A short clickable label instead of a wall of URL.

    HYPERLINK() keeps the address readable in the formula bar and survives being
    copied elsewhere, which a plain hyperlink does not.
    """
    for r in range(first, last + 1):
        c = ws.cell(row=r, column=col)
        url = c.value
        if url:
            safe = str(url).replace('"', "%22")
            c.value = f'=HYPERLINK("{safe}","Open map")'
            c.font = Font(name=FONT, size=9.5, color=LINK, underline="single")
        else:
            c.value = "—"
            c.font = Font(name=FONT, size=9.5, color="9B978E")
        c.alignment = Alignment(horizontal="center", vertical="center")


def totals_row(ws, row, ncols, label, formulas):
    ws.cell(row=row, column=1, value=label)
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = Font(name=FONT, size=9.5, bold=True, color=INK)
        cell.fill = PatternFill("solid", fgColor=GOLD_PALE)
        cell.border = Border(top=Side(style="medium", color=GOLD),
                             bottom=Side(style="medium", color=GOLD),
                             left=thin, right=thin)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.cell(row=row, column=1).alignment = Alignment(
        horizontal="left", vertical="center", indent=1)
    for col, (formula, fmt) in formulas.items():
        cell = ws.cell(row=row, column=col, value=formula)
        cell.number_format = fmt
        cell.alignment = Alignment(horizontal="right", vertical="center")
    ws.row_dimensions[row].height = 20


def finish(ws, co, ncols, widths, last_data):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = ws.cell(row=FIRST_DATA, column=1)
    if last_data >= FIRST_DATA:
        ws.auto_filter.ref = (f"A{HEADER_ROW}:"
                              f"{get_column_letter(ncols)}{last_data}")

    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"1:{HEADER_ROW}"
    ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.5, bottom=0.6)
    ws.print_options.horizontalCentered = True

    ws.oddFooter.left.text = f"{co['name']} — Confidential"
    ws.oddFooter.left.size = 8
    ws.oddFooter.left.color = "808080"
    ws.oddFooter.center.text = "Page &P of &N"
    ws.oddFooter.center.size = 8
    ws.oddFooter.center.color = "808080"
    ws.oddFooter.right.text = "&F  ·  &D"
    ws.oddFooter.right.size = 8
    ws.oddFooter.right.color = "808080"

    ws.sheet_view.showGridLines = False


def empty_note(ws, ncols, text):
    ws.merge_cells(start_row=FIRST_DATA, start_column=1,
                   end_row=FIRST_DATA, end_column=ncols)
    c = ws.cell(row=FIRST_DATA, column=1, value=text)
    c.font = Font(name=FONT, size=10, italic=True, color=MUTED)
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[FIRST_DATA].height = 30


def _date(value):
    return to_local(value)


# ===================================================================== sheets
def property_sheet(wb, co, name, title, subtitle, records, exported_by, index=None):
    headers = ["Ref", "Title", "Building", "Flat", "Location", "Map", "Type",
               "Sale / Rent", "Status", f"Price ({co['currency']})", "Size (m²)",
               "Beds", "Baths", "Owner", "Agent", "Listed"]
    formats = [
        (None, "left"), (None, "left"), (None, "left"), ("@", "center"),
        (None, "left"), (None, "center"), (None, "center"), (None, "center"),
        (None, "center"), ("#,##0", "right"), ("#,##0.#", "right"),
        ("0", "center"), ("0", "center"), (None, "left"), (None, "left"),
        ("dd mmm yyyy", "center"),
    ]
    widths = [11, 34, 17, 8, 14, 11, 12, 11, 11, 15, 10, 6, 6, 22, 17, 12]
    MAP_COL = 6

    ws = wb.create_sheet(name) if index is None else wb.create_sheet(name, index)
    rows = [(r["ref"], r["title"], r["building_no"], r["unit_no"], r["area"],
             r["map_url"], r["prop_type"], r["listing_type"], r["status"],
             r["price"], r["size_sqm"], r["bedrooms"], r["bathrooms"],
             r["owner_name"], r["agent_name"], _date(r["created_at"]))
            for r in records]

    brand_header(ws, co, title, subtitle.format(n=len(rows)), len(headers), exported_by)
    table_header(ws, headers)

    if rows:
        end = write_rows(ws, rows, formats)
        map_links(ws, MAP_COL, FIRST_DATA, end)
        totals_row(ws, end + 1, len(headers),
                   f"{len(rows)} listing{'' if len(rows) == 1 else 's'}", {
                       10: (f"=SUM(J{FIRST_DATA}:J{end})", "#,##0"),
                       11: (f"=SUM(K{FIRST_DATA}:K{end})", "#,##0.#"),
                   })
    else:
        end = FIRST_DATA - 1
        empty_note(ws, len(headers), "No listings in this category yet.")
    finish(ws, co, len(headers), widths, end)


def leads_sheet(wb, co, records, exported_by):
    headers = ["Ref", "Name", "Phone", "Email", "Source", "Stage",
               f"Budget ({co['currency']})", "Property of interest", "Agent",
               "First contact", "Last update"]
    formats = [
        (None, "left"), (None, "left"), ("@", "left"), (None, "left"),
        (None, "center"), (None, "center"), ("#,##0", "right"), (None, "left"),
        (None, "left"), ("dd mmm yyyy", "center"), ("dd mmm yyyy", "center"),
    ]
    widths = [11, 22, 16, 26, 15, 12, 15, 30, 17, 13, 13]

    ws = wb.create_sheet("Leads")
    rows = [(r["ref"], r["full_name"], r["phone"], r["email"], r["source"],
             r["status"], r["budget"], r["prop_title"], r["agent_name"],
             _date(r["created_at"]), _date(r["updated_at"])) for r in records]

    brand_header(ws, co, "Leads Pipeline",
                 f"All leads · every source · every stage · {len(rows)} records",
                 len(headers), exported_by)
    table_header(ws, headers)
    if rows:
        end = write_rows(ws, rows, formats)
        totals_row(ws, end + 1, len(headers), f"{len(rows)} leads",
                   {7: (f"=SUM(G{FIRST_DATA}:G{end})", "#,##0")})
    else:
        end = FIRST_DATA - 1
        empty_note(ws, len(headers), "No leads captured yet.")
    finish(ws, co, len(headers), widths, end)


def deals_sheet(wb, co, records, exported_by):
    headers = ["Ref", "Property", "Client", "Agent", "Type",
               f"Deal value ({co['currency']})", "Comm. %",
               f"Commission ({co['currency']})", "Status", "Closed on"]
    formats = [
        (None, "left"), (None, "left"), (None, "left"), (None, "left"),
        (None, "center"), ("#,##0", "right"), ("0.00%", "center"),
        ("#,##0.00", "right"), (None, "center"), ("dd mmm yyyy", "center"),
    ]
    widths = [11, 34, 21, 17, 10, 17, 10, 17, 12, 13]

    ws = wb.create_sheet("Deals")
    rows = [(r["ref"], r["prop_title"], r["lead_name"], r["agent_name"],
             r["deal_type"], r["value"], (r["commission_pct"] or 0) / 100.0,
             r["commission_amt"], r["status"],
             _date(r["closed_at"] or r["created_at"])) for r in records]

    brand_header(ws, co, "Deals and Commission",
                 f"All deals · every status · {len(rows)} records",
                 len(headers), exported_by)
    table_header(ws, headers)
    if rows:
        end = write_rows(ws, rows, formats)
        totals_row(ws, end + 1, len(headers), f"{len(rows)} deals", {
            6: (f"=SUM(F{FIRST_DATA}:F{end})", "#,##0"),
            8: (f"=SUM(H{FIRST_DATA}:H{end})", "#,##0.00"),
        })
    else:
        end = FIRST_DATA - 1
        empty_note(ws, len(headers), "No deals recorded yet.")
    finish(ws, co, len(headers), widths, end)


def build_workbook(properties, leads, deals, exported_by):
    """The whole workbook, in memory, ready to send to the browser."""
    co = company()
    wb = Workbook()
    wb.remove(wb.active)

    ours = [p for p in properties if p["is_own"]]
    theirs = [p for p in properties if not p["is_own"]]

    property_sheet(wb, co, "Our Properties", "Our Properties",
                   f"Owned and managed by {co['name']} · {{n}} listings",
                   ours, exported_by)
    property_sheet(wb, co, "Other Properties", "Other Properties",
                   "Third-party owners · {n} listings", theirs, exported_by)
    leads_sheet(wb, co, leads, exported_by)
    deals_sheet(wb, co, deals, exported_by)

    wb.properties.title = f"{co['name']} — CRM export"
    wb.properties.creator = co["name"]
    wb.properties.created = datetime.now()

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
