"""Business report builders: PDF (HTML -> WeasyPrint) and XLSX (openpyxl).

Both read the same data bundle as the live UI, so a number in the report is
always a number on screen.
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..analytics.explain import METRICS, fmt_value
from . import data, svg
from .narrative import LIMITATIONS, TYPE_LABELS, executive_summary, finding_phrase, headline, short

HERE = Path(__file__).parent
STATIC = HERE / "static"
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2}


def bundle() -> dict[str, Any]:
    """Everything the report needs, gathered once."""
    ov = data.overview()
    return {
        "overview": ov, "leakage": data.leakage(), "branches": data.branches(), "suppliers": data.suppliers(),
        "products": data.products(), "inventory": data.inventory(), "health": data.pipeline_health(),
        "currency": data.currency(), "generated_at": datetime.now().strftime("%d %B %Y, %H:%M"),
    }


# ---------------------------------------------------------------------------- PDF
def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]))
    env.filters["money"] = lambda v, cur="PKR": short(v, cur)
    env.filters["full"] = lambda v: f"{v:,.0f}" if v is not None else "n/a"
    env.filters["pct"] = lambda v, d=1: f"{v * 100:.{d}f}%" if v is not None else "n/a"
    env.filters["signed_pct"] = lambda v, d=1: f"{v * 100:+.{d}f}%" if v is not None else "n/a"
    env.filters["num"] = lambda v, d=1: f"{v:,.{d}f}" if v is not None else "n/a"
    env.filters["type_label"] = lambda t: TYPE_LABELS.get(t, t)
    return env


def _strip_for(finding: dict[str, Any]) -> str:
    if finding["detection_method"] != "PEER":
        return ""
    strip = data.peer_strip(finding["check_id"], finding["entity_id"])
    if not strip:
        return ""
    return svg.peer_strip(strip["points"], strip["focus"], finding["baseline_value"],
                          lambda v, m=finding["metric"]: fmt_value(m, v))


def render_pdf(b: dict[str, Any] | None = None) -> bytes:
    from weasyprint import HTML

    b = b or bundle()
    ov, cur = b["overview"], b["currency"]
    findings = [a for a in data.anomalies() if a["leakage_type"] or a["detection_method"] == "TEMPORAL"]
    for a in findings:
        a["strip_svg"] = _strip_for(a)
        a["phrase"] = finding_phrase(a)
        a["metric_label"] = METRICS.get(a["metric"], {}).get("label", a["metric"].replace("_", " ")).capitalize()

    monthly = ov["monthly"]
    charts = {
        "revenue": svg.line_chart([m["year_month"] for m in monthly],
                                  {"Net revenue": [m["net_revenue"] for m in monthly],
                                   "Gross profit": [m["gross_profit"] for m in monthly]},
                                  {"Net revenue": svg.TEAL, "Gross profit": svg.INK}, height=170),
        "leakage": svg.hbar_chart([(TYPE_LABELS.get(t["leakage_type"], t["leakage_type"]), t["exposure_amount"])
                                   for t in ov["leakage_by_type"] if t["leakage_type"] != "LOW_MARGIN_SHORTFALL"],
                                  color=svg.RASP, value_fmt=lambda v: short(v, cur)),
        "branch_scores": svg.score_bars([{"label": f"{r['branch_id']} {r['city']}", "score": r["performance_score"],
                                          "band": r["performance_band"]} for r in b["branches"]["scorecards"]]),
        "movement": svg.line_chart([m["year_month"] for m in b["inventory"]["movement"]],
                                   {"Purchased": [m["units_purchased"] for m in b["inventory"]["movement"]],
                                    "Sold": [m["units_sold"] for m in b["inventory"]["movement"]]},
                                   {"Purchased": svg.TEAL, "Sold": svg.INK}, y_fmt=lambda v: f"{v / 1000:.0f}K"),
    }
    summary = executive_summary(ov, b["branches"], b["suppliers"], b["products"], b["inventory"], b["health"])
    kp = ov["kpis"]
    detected = [t for t in ov["leakage_by_type"] if t["leakage_type"] != "LOW_MARGIN_SHORTFALL"]
    html = _env().get_template("report.html").render(
        b=b, ov=ov, cur=cur, charts=charts, findings=findings, summary=summary, headline=headline(ov),
        k=lambda key: (kp.get(key) or {}).get("value_numeric"), kt=lambda key: (kp.get(key) or {}).get("value_text"),
        detected=detected, limitations=LIMITATIONS, weights=b["branches"]["weights"],
        checks_passed=sum(1 for c in b["health"]["checks"] if c["status"] == "PASS"),
        checks_total=len(b["health"]["checks"]))
    return HTML(string=html, base_url=str(STATIC)).write_pdf()


# ---------------------------------------------------------------------------- XLSX
def render_xlsx(b: dict[str, Any] | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    b = b or bundle()
    ov, cur = b["overview"], b["currency"]
    kp = ov["kpis"]
    # "Slate" palette, shared with the dashboard and the PDF.
    ink, steel, risk, brass, muted = "16223A", "3D5A84", "C2513A", "22C55E", "6C788D"
    band_fill = PatternFill("solid", fgColor="F5F7FA")          # zebra rows
    rule = Side(style="thin", color="E2E6ED")
    font = "Calibri"
    head_font = Font(name=font, bold=True, color="FFFFFF", size=10)
    head_fill = PatternFill("solid", fgColor="1E2B45")
    head_border = Border(bottom=Side(style="medium", color="3D5A84"))
    body_font = Font(name=font, size=10, color=ink)
    money_fmt, pct_fmt, int_fmt, dec_fmt = '#,##0', '0.0%', '#,##0', '#,##0.0'

    wb = Workbook()

    def sheet(title: str, headers: list[tuple[str, str, int]], rows: list[list[Any]], note: str | None = None):
        ws = wb.create_sheet(title)
        ws.sheet_properties.tabColor = steel
        ncol = max(len(headers), 4)
        # Title band: sheet name on navy, the note underneath in muted italics.
        ws.cell(1, 1, title).font = Font(name=font, bold=True, size=14, color="FFFFFF")
        for j in range(1, ncol + 1):
            ws.cell(1, j).fill = head_fill
        ws.cell(1, 1).alignment = Alignment(vertical="center", indent=1)
        ws.row_dimensions[1].height = 30
        r0 = 3
        if note:
            ws.cell(2, 1, note).font = Font(name=font, italic=True, size=9, color=muted)
            ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncol)
            ws.cell(2, 1).alignment = Alignment(wrap_text=True, vertical="center", indent=1)
            ws.row_dimensions[2].height = 32
            r0 = 4
        for j, (name, fmt, width) in enumerate(headers, 1):
            c = ws.cell(r0, j, name)
            c.font, c.fill, c.border = head_font, head_fill, head_border
            c.alignment = Alignment(horizontal="left" if not fmt else "right", vertical="center", wrap_text=True, indent=1 if not fmt else 0)
            ws.column_dimensions[get_column_letter(j)].width = width
        ws.row_dimensions[r0].height = 30
        for i, row in enumerate(rows, r0 + 1):
            zebra = (i - r0) % 2 == 0
            for j, val in enumerate(row, 1):
                c = ws.cell(i, j, val)
                c.font, c.border = body_font, Border(bottom=rule)
                if zebra:
                    c.fill = band_fill
                fmt = headers[j - 1][1]
                if fmt:
                    c.number_format = fmt
                if isinstance(val, str) and len(val) > 60:
                    c.alignment = Alignment(wrap_text=True, vertical="top")
                else:
                    c.alignment = Alignment(vertical="top", indent=0 if fmt else 1)
            ws.cell(i, 1).font = Font(name=font, size=10, bold=True, color=ink)
        ws.freeze_panes = ws.cell(r0 + 1, 1)
        ws.auto_filter.ref = f"A{r0}:{get_column_letter(len(headers))}{r0 + len(rows)}"
        ws.sheet_view.showGridLines = False
        return ws, r0

    # Summary
    ws = wb.active
    ws.title = "Summary"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = ink
    for r in (1, 2, 3):                                     # navy cover band across the top
        for col in range(1, 5):
            ws.cell(r, col).fill = head_fill
    ws["A1"] = "ProfitPulse"
    ws["A1"].font = Font(name=font, size=10, bold=True, color="22C55E")
    ws["A2"] = "Business report"
    ws["A2"].font = Font(name=font, size=20, bold=True, color="FFFFFF")
    ws["A3"] = f"Data {kp['period_start']['value_text']} to {kp['as_of_date']['value_text']}   |   Currency {cur}   |   Generated {b['generated_at']}"
    ws["A3"].font = Font(name=font, size=9, color="A9B5C8")
    for r, hgt in ((1, 24), (2, 34), (3, 24)):
        ws.row_dimensions[r].height = hgt
        ws.cell(r, 1).alignment = Alignment(vertical="center", indent=1)
    for col in range(1, 5):
        ws.cell(4, col).fill = PatternFill("solid", fgColor=brass)
    ws.row_dimensions[4].height = 3
    h = headline(ov)
    ws["A6"] = h["lead"]
    ws["A6"].font = Font(name=font, bold=True, size=13, color=ink)
    ws["A7"] = h["support"]
    ws["A7"].font = Font(name=font, size=10, color=muted)
    ws.merge_cells("A6:D6")
    ws.merge_cells("A7:D7")
    ws["A6"].alignment = Alignment(wrap_text=True, vertical="center", indent=1)
    ws["A7"].alignment = Alignment(wrap_text=True, vertical="top", indent=1)
    ws.row_dimensions[6].height = 36
    ws.row_dimensions[7].height = 32
    row = 9
    section = None
    for key, k in kp.items():
        if k["section"] == "Meta":
            continue
        if k["section"] != section:
            section = k["section"]
            if row > 9:
                row += 1
            for col in range(1, 4):
                ws.cell(row, col).border = Border(bottom=Side(style="medium", color=ink))
            ws.cell(row, 1, section).font = Font(name=font, bold=True, size=11, color=steel)
            ws.cell(row, 1).alignment = Alignment(indent=1)
            ws.row_dimensions[row].height = 22
            row += 1
        bad = key in ("leakage_total", "leakage_annualised")
        lc = ws.cell(row, 1, k["label"])
        lc.font, lc.alignment = Font(name=font, size=10, color=ink), Alignment(indent=1)
        c = ws.cell(row, 2, k["value_numeric"])
        c.number_format = {"currency": money_fmt, "percent": pct_fmt, "count": int_fmt}.get(k["unit"], "General")
        c.font = Font(name=font, size=10, bold=True, color=risk if bad else ink)
        uc = ws.cell(row, 3, cur if k["unit"] == "currency" else None)
        uc.font = Font(name=font, size=9, color=muted)
        for col in range(1, 4):
            ws.cell(row, col).border = Border(bottom=rule)
        row += 1
    ws.column_dimensions["A"].width = 62
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 8
    ws.column_dimensions["D"].width = 30

    # Findings
    findings = data.anomalies()
    sev_fill = {"critical": "F8E6E1", "high": "F6ECD9", "medium": "EAEEF4"}
    sev_font = {"critical": risk, "high": "82560C", "medium": muted}
    _, r0 = sheet("Findings", [
        ("Severity", "", 11), ("Entity", "", 22), ("Check", "", 26), ("Metric", "", 20), ("Observed", "0.0000", 12),
        ("Peer / own baseline", "0.0000", 14), ("Robust z", dec_fmt, 10), ("Exposure (" + cur + ")", money_fmt, 16),
        ("Per year (" + cur + ")", money_fmt, 16), ("Leakage type", "", 24), ("Explanation", "", 110)],
        [[a["severity"], a["entity_label"], a["check_id"], a["metric"], a["observed_value"], a["baseline_value"], a["robust_z"],
          a["estimated_exposure"], a["annualised_exposure"], TYPE_LABELS.get(a["leakage_type"], a["leakage_type"]), a["explanation"]]
         for a in findings],
        note="Statistical findings with plain-English explanations. Exposure is an estimate; findings can overlap.")
    ws_f = wb["Findings"]
    for i, a in enumerate(findings, r0 + 1):
        ws_f.cell(i, 1).fill = PatternFill("solid", fgColor=sev_fill.get(a["severity"], "FFFFFF"))
        ws_f.cell(i, 1).font = Font(name=font, size=10, bold=True, color=sev_font.get(a["severity"], ink))
        ws_f.row_dimensions[i].height = 48

    lk = b["leakage"]
    sheet("Leakage", [("Type", "", 28), ("Entity", "", 24), ("Severity", "", 11), ("Exposure (" + cur + ")", money_fmt, 18),
                      ("Per year (" + cur + ")", money_fmt, 18), ("How it was calculated", "", 90)],
          [[TYPE_LABELS.get(i["leakage_type"], i["leakage_type"]), i["entity_label"], i["severity"] or "", i["exposure_amount"],
            i["annualised_exposure"], i["basis"]] for i in lk["items"]],
          note="Detected leakage plus the low-margin pricing opportunity (kept apart in the summary).")

    sc = b["branches"]["scorecards"]
    wsb, r0 = sheet("Branches", [
        ("Rank", int_fmt, 7), ("Branch", "", 9), ("City", "", 14), ("Net revenue", money_fmt, 16), ("Gross profit", money_fmt, 16),
        ("Margin", pct_fmt, 9), ("Discount rate", pct_fmt, 11), ("Return rate", pct_fmt, 11), ("Shrink rate", pct_fmt, 11),
        ("Growth", pct_fmt, 9), ("Score", dec_fmt, 8), ("Band", "", 9), ("Risk", "", 9), ("Anomalies", int_fmt, 10)],
        [[r["performance_rank"], r["branch_id"], r["city"], r["net_revenue"], r["gross_profit"], r["gross_margin"], r["discount_rate"],
          r["return_rate_units"], r["shrink_rate"], r["growth_rate"], r["performance_score"], r["performance_band"],
          r["operational_risk"], r["anomaly_count"]] for r in sc],
        note="Score = 100 x weighted robust z-scores vs the other branches (typical 0.5, far worse 0, far better 1) of margin, discount rate, return rate, shrink rate and growth. Weights: "
             + ", ".join(f"{k} {v['weight']:.0%}" for k, v in b["branches"]["weights"]["components"].items()))
    wsb.conditional_formatting.add(f"L{r0 + 1}:L{r0 + len(sc)}", CellIsRule(operator="equal", formula=['"weak"'], fill=PatternFill("solid", bgColor="F8E6E1"),
                                                                                         font=Font(color=risk, bold=True)))

    sup = b["suppliers"]["scorecards"]
    sheet("Suppliers", [
        ("Supplier", "", 10), ("Products", int_fmt, 9), ("Purchases", int_fmt, 10), ("Spend", money_fmt, 16), ("Spend at standard cost", money_fmt, 18),
        ("Above standard", money_fmt, 15), ("Variance %", pct_fmt, 10), ("Share above standard", pct_fmt, 12), ("Bought above list", int_fmt, 11),
        ("Return rate of products", pct_fmt, 12), ("Risk", "", 9)],
        [[s["supplier_id"], s["products_supplied"], s["purchase_events"], s["purchase_spend"], s["purchase_standard_spend"], s["ppv_amount"],
          s["ppv_pct"], s["share_above_standard"], s["purchases_above_list"], s["product_return_rate"], s["risk_level"]] for s in sup])

    pr = b["products"]
    prod_rows = []
    for seg in ("problematic", "low_margin", "high_margin", "high_return", "high_discount", "declining"):
        for p in pr[seg]:
            prod_rows.append([seg.replace("_", " "), p["product_id"], p["category"], p["supplier_id"], p["net_revenue"], p["gross_margin"],
                              p["discount_rate"], p["return_rate_units"], p["growth_rate"], p["problem_reasons"]])
    sheet("Products", [("Segment", "", 16), ("Product", "", 10), ("Category", "", 15), ("Supplier", "", 10), ("Net revenue", money_fmt, 15),
                       ("Margin", pct_fmt, 9), ("Discount", pct_fmt, 9), ("Return rate", pct_fmt, 11), ("Growth", pct_fmt, 9), ("Why flagged", "", 80)],
          prod_rows, note="Top entries per segment; the full product scorecard is in the analytics.product_scorecard table.")

    inv = b["inventory"]
    sheet("Inventory risk", [("Product", "", 10), ("Category", "", 15), ("Supplier", "", 10), ("Velocity/day", dec_fmt, 12), ("Replenishment", pct_fmt, 13),
                             ("Days since purchase", int_fmt, 14), ("Risk score", dec_fmt, 10), ("Level", "", 10), ("Reorder review", "", 12), ("Why", "", 100)],
          [[r["product_id"], r["category"], r["supplier_id"], r["velocity_per_day"], r["replenishment_ratio"], r["days_since_last_purchase"],
            r["stockout_risk_score"], r["stockout_risk_level"], "yes" if r["reorder_flag"] else "", r["explanation"]] for r in inv["risk_top"]],
          note="Movement-based PROXY: the source has no stock-on-hand, so this ranks products by sales velocity, purchase coverage and purchase recency.")
    sheet("Monthly", [("Month", "", 10), ("Net revenue", money_fmt, 16), ("Gross profit", money_fmt, 16), ("Margin", pct_fmt, 9),
                      ("Discount rate", pct_fmt, 11), ("Return rate", pct_fmt, 11), ("Profit after returns and damage", money_fmt, 20)],
          [[m["year_month"], m["net_revenue"], m["gross_profit"], m["gross_margin"], m["discount_rate"], m["return_rate_units"], m["adjusted_gross_profit"]]
           for m in ov["monthly"]])

    hl = b["health"]
    sheet("Data quality", [("Layer", "", 12), ("Check", "", 46), ("Status", "", 9), ("Observed", "#,##0.00", 18), ("Expected", "#,##0.00", 18), ("Detail", "", 80)],
          [[c["layer"], c["check_name"], c["status"], c["observed"], c["expected"], c["message"]] for c in hl["checks"]])
    sheet("Quarantine", [("Rule", "", 36), ("Severity", "", 10), ("Disposition", "", 24), ("Messages", int_fmt, 12)],
          [[r["rule_code"], r["severity"], r["disposition"], r["failures"]] for r in hl["rules"]],
          note="Rejected and warned messages by rule. Rejected messages are kept verbatim in quarantine.dq_failures.")

    ws = wb.create_sheet("Methodology")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = muted
    ws.column_dimensions["A"].width = 130
    ws["A1"] = "Limitations and method notes"
    ws["A1"].font = Font(name=font, bold=True, size=14, color="FFFFFF")
    ws["A1"].fill = head_fill
    ws["A1"].alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[1].height = 30
    for i, text in enumerate(LIMITATIONS, 3):
        c = ws.cell(i, 1, text)
        c.font, c.border = Font(name=font, size=10, color=ink), Border(bottom=rule)
        c.alignment = Alignment(wrap_text=True, vertical="center", indent=1)
        ws.row_dimensions[i].height = 36

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
