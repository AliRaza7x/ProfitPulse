# Web UI + report service (FastAPI). WeasyPrint needs pango for PDF rendering.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfontconfig1 curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 app && mkdir -p /app/reports && chown -R app /app
WORKDIR /app

RUN pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.30" "jinja2>=3.1" "weasyprint>=62" "openpyxl>=3.1" \
      "psycopg2-binary>=2.9" "pyyaml>=6" "numpy>=1.26" "pandas>=2.2"

USER app
ENV PYTHONPATH=/app/src PP_REPORT_DIR=/app/reports PP_CONFIG_DIR=/app/config PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD curl -fsS http://localhost:8000/api/status || exit 1
CMD ["uvicorn", "profitpulse.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
