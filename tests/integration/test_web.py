"""The running web service: JSON API, the peer-strip data, and both report downloads.

Talks to the `web` container over the Compose network (override with PP_WEB_INTERNAL_URL); skipped if it is not running.
"""
from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
import zipfile

import pytest

pytestmark = pytest.mark.integration
BASE = os.environ.get("PP_WEB_INTERNAL_URL", "http://web:8000")


def get(path: str, timeout: int = 90) -> tuple[int, dict, bytes]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:      # noqa: S310 (internal service)
            return r.status, {k.title(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.title(): v for k, v in exc.headers.items()}, exc.read()


@pytest.fixture(scope="module", autouse=True)
def web_ready():
    try:
        status, _, body = get("/api/status", timeout=5)
    except Exception:
        pytest.skip("web service not reachable")
    if status != 200 or not json.loads(body)["ready"]:
        pytest.skip("analytics not published yet")


def j(path):
    status, _, body = get(path)
    assert status == 200, f"{path} returned {status}"
    return json.loads(body)


def test_every_page_endpoint_returns_data():
    assert j("/api/overview")["kpis"]["net_revenue"]["value_numeric"] > 0
    assert len(j("/api/branches")["scorecards"]) == 15
    assert j("/api/suppliers")["scorecards"]
    assert j("/api/products")["counts"]["total"] == 1000
    assert j("/api/inventory")["risk_top"]
    assert j("/api/leakage")["items"]
    assert j("/api/health")["checks"]


def test_headline_is_a_real_sentence_drawn_from_the_data():
    h = j("/api/overview")["headline"]
    assert "PKR" in h["lead"] and "a year" in h["lead"] and h["support"].startswith("The largest is")


def test_overview_findings_carry_everything_a_case_file_needs():
    for f in j("/api/overview")["findings"]:
        assert f["explanation"] and f["entity_label"] and f["estimated_exposure"] > 0
        if f["detection_method"] == "PEER":
            s = f["strip"]
            assert s["median"] is not None and s["scale"] > 0 and s["threshold_z"] >= 3.5
            assert f["entity_id"] in {p["id"] for p in s["points"]}


def test_peer_strip_contains_all_peers_and_a_sensible_median():
    s = j("/api/peer-strip?check=branch_discount_rate&entity=BR07")
    assert len(s["points"]) == 15 and s["direction"] == "high"
    values = sorted(p["value"] for p in s["points"])
    assert s["median"] == pytest.approx(values[len(values) // 2])
    status, *_ = get("/api/peer-strip?check=no_such_check&entity=X")
    assert status == 404


def test_pdf_report_download():
    status, headers, body = get("/api/reports/pdf")
    assert status == 200 and headers["Content-Type"] == "application/pdf"
    assert "attachment" in headers["Content-Disposition"] and ".pdf" in headers["Content-Disposition"]
    assert body.startswith(b"%PDF") and len(body) > 50_000


def test_excel_report_download_is_a_real_workbook():
    status, headers, body = get("/api/reports/xlsx")
    assert status == 200 and "spreadsheetml" in headers["Content-Type"] and ".xlsx" in headers["Content-Disposition"]
    names = zipfile.ZipFile(io.BytesIO(body)).namelist()
    assert "xl/workbook.xml" in names and sum(n.startswith("xl/worksheets/") for n in names) >= 11


def test_ui_shell_and_assets_are_served():
    status, _, body = get("/")
    assert status == 200 and b"ProfitPulse" in body and b"/static/app.js" in body
    for asset in ("/static/app.js", "/static/app.css", "/static/fonts/BricolageGrotesque.ttf", "/static/fonts/SourceSerif4.ttf"):
        assert get(asset)[0] == 200, asset


def test_snapshot_listing_and_path_traversal_is_refused():
    assert isinstance(j("/api/reports/snapshots"), list)
    assert get("/api/reports/snapshots/..%2F..%2Fetc%2Fpasswd")[0] == 404
