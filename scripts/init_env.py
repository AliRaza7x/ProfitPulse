"""Create .env from .env.example, filling every CHANGE_ME with a random secret.

Refuses to overwrite an existing .env. Secrets are never printed.
Usage: python scripts/init_env.py
"""
from __future__ import annotations

import base64
import re
import secrets
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"


def _secret_for(key: str) -> str:
    if key == "AIRFLOW_FERNET_KEY":
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    if key == "KAFKA_CLUSTER_ID":  # Kafka expects a 22-char url-safe base64 UUID
        return base64.urlsafe_b64encode(uuid.uuid4().bytes).decode().rstrip("=")
    return secrets.token_urlsafe(24)


def main() -> int:
    if TARGET.exists():
        print(f"{TARGET} already exists; leaving it untouched.")
        return 0
    out = []
    filled = 0
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(CHANGE_ME.*)$", line)
        if m:
            out.append(f"{m.group(1)}={_secret_for(m.group(1))}")
            filled += 1
        else:
            out.append(line)
    TARGET.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET.name} with {filled} generated secrets (not displayed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
