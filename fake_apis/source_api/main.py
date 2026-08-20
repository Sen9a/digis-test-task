"""Fake Source API — simulates an external invoicing system (e.g. QuickBooks / Stripe).

Generates deterministic-but-varied invoice data on startup and serves it with
cursor-based pagination, optional rate-limiting, and token auth.
"""

from __future__ import annotations

import os
import random
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration (env vars)
# ---------------------------------------------------------------------------
INVOICE_COUNT: int = int(os.getenv("INVOICE_COUNT", "25"))
RATE_LIMIT_AFTER: int = int(os.getenv("RATE_LIMIT_AFTER", "0"))  # 0 = disabled

# ---------------------------------------------------------------------------
# Deterministic seed so restarts produce the same dataset
# ---------------------------------------------------------------------------
_rng = random.Random(42)

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
_FIRST = ["Acme", "Globex", "Initech", "Umbrella", "Stark", "Wayne", "Hooli",
          "Pied Piper", "Soylent", "Cyberdyne", "Tyrell", "Wonka", "Oscorp",
          "Aperture", "Black Mesa", "Vault-Tec", "Abstergo", "Nakatomi",
          "Virtucon", "Gringotts"]
_SUFFIX = ["Corp", "Inc", "LLC", "Ltd", "Group", "Industries", "Labs",
           "Solutions", "Systems", "Holdings"]
_CURRENCIES = ["usd", "usd", "usd", "eur", "eur", "gbp", "uah"]
_STATUSES = (
    ["paid"] * 30 + ["sent"] * 30 + ["draft"] * 20
    + ["overdue"] * 10 + ["void"] * 10
)
_LINE_DESCS = [
    "Consulting", "Development", "Design", "Support", "Hosting",
    "Training", "Audit", "Licensing", "Maintenance", "Integration",
    "Migration", "Analysis", "Testing", "Documentation", "Research",
]

# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _build_customer(idx: int) -> dict[str, str] | str:
    """~70 % full object, ~30 % bare string ID."""
    cust_id = f"cust-{_rng.randint(1, 15)}"
    if _rng.random() < 0.3:
        return cust_id
    name = f"{_rng.choice(_FIRST)} {_rng.choice(_SUFFIX)}"
    domain = name.lower().replace(" ", "")[:12]
    return {
        "id": cust_id,
        "name": name,
        "email": f"billing@{domain}.com",
    }


def _build_lines() -> list[dict[str, Any]]:
    count = _rng.randint(1, 5)
    lines: list[dict[str, Any]] = []
    for _ in range(count):
        qty = _rng.randint(1, 40)
        price = round(_rng.uniform(5, 500), 2)
        tax_rate = _rng.choice([0.0, 0.07, 0.10, 0.20, 0.25])
        total = round(qty * price, 2)
        lines.append({
            "desc": _rng.choice(_LINE_DESCS),
            "qty": qty,
            "price": price,
            "total": total,
            "tax_rate": tax_rate,
        })
    return lines


def _build_invoice(idx: int) -> dict[str, Any]:
    inv_id = f"inv-{idx + 1:03d}"
    number = f"INV-{1001 + idx}"
    currency = _rng.choice(_CURRENCIES)
    status = _rng.choice(_STATUSES)

    base_day = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(
        days=_rng.randint(0, 364),
        hours=_rng.randint(0, 23),
        minutes=_rng.randint(0, 59),
    )
    due_day = base_day + timedelta(days=_rng.choice([14, 30, 45, 60, 90]))

    has_lines = _rng.random() < 0.75
    lines = _build_lines() if has_lines else []

    if has_lines:
        amount = round(sum(l["total"] for l in lines), 2)
        tax = round(
            sum(l["total"] * l["tax_rate"] for l in lines), 2
        )
    else:
        amount = round(_rng.uniform(10, 10_000), 2)
        tax = round(amount * _rng.choice([0.0, 0.07, 0.20, 0.25]), 2)

    return {
        "id": inv_id,
        "number": number,
        "customer": _build_customer(idx),
        "amount": amount,
        "tax": tax,
        "currency": currency,
        "status": status,
        "date": _date(base_day),
        "due": _date(due_day),
        "updated": _iso(base_day + timedelta(hours=_rng.randint(0, 48))),
        "lines": lines,
    }


INVOICES: list[dict[str, Any]] = [
    _build_invoice(i) for i in range(INVOICE_COUNT)
]
_INVOICE_MAP: dict[str, dict[str, Any]] = {inv["id"]: inv for inv in INVOICES}

# ---------------------------------------------------------------------------
# Rate limiter (thread-safe counter)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_request_count: int = 0


class RateLimitExceeded(Exception):
    """Raised when the request counter exceeds RATE_LIMIT_AFTER."""

    def __init__(self, retry_after: int = 5) -> None:
        self.retry_after = retry_after


def _check_rate_limit() -> None:
    global _request_count
    if RATE_LIMIT_AFTER <= 0:
        return
    with _lock:
        _request_count += 1
        if _request_count > RATE_LIMIT_AFTER:
            raise RateLimitExceeded(retry_after=5)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
_VALID_TOKENS: set[str] = {"fake-token-123"}


def _verify_token(authorization: str | None) -> None:
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token not in _VALID_TOKENS:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Fake Source API", version="1.0.0")


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded", "retry_after": exc.retry_after},
        headers={"Retry-After": str(exc.retry_after)},
    )


class TokenRequest(BaseModel):
    api_key: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/token")
async def auth_token(body: TokenRequest) -> dict[str, Any]:
    if body.api_key == "invalid":
        raise HTTPException(status_code=401, detail="Invalid API key")
    return {"token": "fake-token-123", "expires_in": 3600}


@app.get("/invoices")
async def list_invoices(
    request: Request,
    authorization: str | None = Header(None),
    cursor: str | None = Query(None),
    limit: int = Query(100, ge=1, le=100),
) -> dict[str, Any]:
    _verify_token(authorization)
    _check_rate_limit()

    start = 0
    if cursor is not None:
        try:
            start = int(cursor)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid cursor")

    page = INVOICES[start : start + limit]
    next_cursor: str | None = None
    if start + limit < len(INVOICES):
        next_cursor = str(start + limit)

    return {"invoices": page, "next_cursor": next_cursor}


@app.get("/invoices/{invoice_id}")
async def get_invoice(
    invoice_id: str,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    _verify_token(authorization)
    _check_rate_limit()

    inv = _INVOICE_MAP.get(invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
    return inv
