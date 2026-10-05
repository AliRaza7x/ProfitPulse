"""ProfitPulse web service: JSON API for the dashboard + downloadable PDF/XLSX reports."""
from __future__ import annotations

import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import data, report
from .narrative import headline

log = logging.getLogger("profitpulse.web")
HERE = Path(__file__).parent
REPORT_DIR = Path(os.environ.get("PP_REPORT_DIR", "/app/reports"))

app = FastAPI(title="ProfitPulse", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

_cache: dict[str, tuple[float, Any]] = {}
CACHE_SECONDS = 20


def cached(key: str, fn: Callable[[], Any]) -> Any:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        value = fn()
    except Exception as exc:   # a missing table means the pipeline has not run yet
        log.exception("data query failed for %s", key)
        raise HTTPException(503, "The analytics tables are not available yet. Run the pipeline, then reload.") from exc
    _cache[key] = (time.monotonic(), value)
    return value


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
def status() -> dict[str, Any]:
    try:
        ok = data.ready()
    except Exception:
        ok = False
    return {"ready": ok, "currency": data.currency()}


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    ov = cached("overview", data.overview)
    return {**ov, "headline": headline(ov)}


@app.get("/api/leakage")
def leakage() -> dict[str, Any]:
    return cached("leakage", data.leakage)


@app.get("/api/inventory")
def inventory() -> dict[str, Any]:
    return cached("inventory", data.inventory)


@app.get("/api/branches")
def branches() -> dict[str, Any]:
    return cached("branches", data.branches)


@app.get("/api/suppliers")
def suppliers() -> dict[str, Any]:
    return cached("suppliers", data.suppliers)


@app.get("/api/products")
def products() -> dict[str, Any]:
    return cached("products", data.products)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return cached("health", data.pipeline_health)


@app.get("/api/peer-strip")
def peer_strip(check: str = Query(...), entity: str = Query(...)) -> dict[str, Any]:
    strip = cached(f"strip:{check}:{entity}", lambda: data.peer_strip(check, entity))
    if strip is None:
        raise HTTPException(404, "Unknown check")
    return strip


# ------------------------------------------------------------------ reports
def _filename(ext: str) -> str:
    return f"ProfitPulse_report_{date.today().isoformat()}.{ext}"


def _attachment(content: bytes, media_type: str, filename: str) -> Response:
    return Response(content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@app.get("/api/reports/pdf")
def report_pdf() -> Response:
    cached("overview", data.overview)       # surfaces the friendly 503 if nothing has been published yet
    return _attachment(report.render_pdf(), "application/pdf", _filename("pdf"))


@app.get("/api/reports/xlsx")
def report_xlsx() -> Response:
    cached("overview", data.overview)
    return _attachment(report.render_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       _filename("xlsx"))


@app.post("/api/reports/snapshot")
def report_snapshot() -> JSONResponse:
    """Render both formats into the shared reports volume (called by the analytics DAG)."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    bundle = report.bundle()
    stamp = date.today().isoformat()
    written = []
    for ext, fn in (("pdf", report.render_pdf), ("xlsx", report.render_xlsx)):
        path = REPORT_DIR / f"ProfitPulse_report_{stamp}.{ext}"
        path.write_bytes(fn(bundle))
        written.append({"file": path.name, "bytes": path.stat().st_size})
    return JSONResponse({"snapshot": stamp, "files": written})


@app.get("/api/reports/snapshots")
def list_snapshots() -> list[dict[str, Any]]:
    if not REPORT_DIR.exists():
        return []
    return [{"file": p.name, "bytes": p.stat().st_size, "modified": p.stat().st_mtime}
            for p in sorted(REPORT_DIR.glob("ProfitPulse_report_*"), reverse=True)][:20]


@app.get("/api/reports/snapshots/{name}")
def get_snapshot(name: str) -> FileResponse:
    path = (REPORT_DIR / name).resolve()
    if path.parent != REPORT_DIR.resolve() or not path.exists() or not name.startswith("ProfitPulse_report_"):
        raise HTTPException(404, "Snapshot not found")
    return FileResponse(path, filename=name)
