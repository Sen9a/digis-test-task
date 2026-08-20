"""Fake Target Accounting API — simulates an external accounting system (NetSuite/Xero-like).

The sync engine exports invoices TO this system.
Configurable behaviour via environment variables:

    SUPPORTS_IDEMPOTENCY   Honor Idempotency-Key header (default true)
    SUPPORTS_UPDATE        Allow PUT /invoices/{id}     (default true)
    REQUIRES_REVERSAL      If true, updates return 405  (default false)
    REJECTS_DUPLICATES     Return 409 on duplicate invoice_number (default true)
    RATE_LIMIT_AFTER       Return 429 after N requests  (default 0 = disabled)
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration (env vars)
# ---------------------------------------------------------------------------

def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in ("true", "1", "yes")

SUPPORTS_IDEMPOTENCY: bool = _env_bool("SUPPORTS_IDEMPOTENCY", True)
SUPPORTS_UPDATE: bool = _env_bool("SUPPORTS_UPDATE", True)
REQUIRES_REVERSAL: bool = _env_bool("REQUIRES_REVERSAL", False)
REJECTS_DUPLICATES: bool = _env_bool("REJECTS_DUPLICATES", True)
RATE_LIMIT_AFTER: int = int(os.getenv("RATE_LIMIT_AFTER", "0"))

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

invoices: dict[str, dict[str, Any]] = {}           # target_id → invoice record
invoice_number_index: dict[str, str] = {}           # invoice_number → target_id
idempotency_keys: dict[str, str] = {}               # idempotency_key → target_id
request_counter: int = 0                             # global mutable counter for rate limiting

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TokenRequest(BaseModel):
    api_key: str


class TokenResponse(BaseModel):
    token: str
    expires_in: int = 3600


class InvoiceLine(BaseModel):
    description: str
    quantity: str
    unit_price: str
    total: str
    tax_rate: str | None = None


class InvoicePayload(BaseModel):
    external_id: str | None = None
    invoice_number: str
    customer_id: str | None = None
    customer_name: str | None = None
    currency: str = "USD"
    total: str
    tax_total: str | None = None
    status: str = "sent"
    issue_date: str | None = None
    due_date: str | None = None
    lines: list[InvoiceLine] = []


class ReverseRequest(BaseModel):
    reason: str = "reversal requested"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Fake Target Accounting API", version="1.0.0")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_rate_limit() -> None:
    """Increment request counter and raise 429 if over the limit."""
    global request_counter
    if RATE_LIMIT_AFTER <= 0:
        return
    request_counter += 1
    if request_counter > RATE_LIMIT_AFTER:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": "1"},
        )


def _verify_token(authorization: str | None) -> str:
    """Extract and validate Bearer token. Returns the token string."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")
    token = parts[1]
    if token != "fake-target-token-456":
        raise HTTPException(status_code=401, detail="Invalid token")
    return token


def _next_id() -> str:
    return f"tgt-{uuid.uuid4().hex[:8]}"


def _invoice_response(record: dict[str, Any], status_override: str | None = None) -> dict[str, Any]:
    """Build the public representation of an invoice record."""
    resp = {
        "id": record["id"],
        "external_id": record.get("external_id"),
        "invoice_number": record["invoice_number"],
        "customer_id": record.get("customer_id"),
        "customer_name": record.get("customer_name"),
        "currency": record.get("currency", "USD"),
        "total": record["total"],
        "tax_total": record.get("tax_total"),
        "status": status_override or record["status"],
        "issue_date": record.get("issue_date"),
        "due_date": record.get("due_date"),
        "lines": record.get("lines", []),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }
    return resp


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/token", response_model=TokenResponse)
def get_token(body: TokenRequest):
    if body.api_key == "invalid":
        raise HTTPException(status_code=401, detail="Invalid API key")
    return TokenResponse(token="fake-target-token-456", expires_in=3600)


@app.post("/invoices", status_code=201)
def create_invoice(
    payload: InvoicePayload,
    authorization: str | None = Header(None),
    idempotency_key: str | None = Header(None),
):
    _verify_token(authorization)
    _check_rate_limit()

    # Idempotency check
    if SUPPORTS_IDEMPOTENCY and idempotency_key:
        if idempotency_key in idempotency_keys:
            existing_id = idempotency_keys[idempotency_key]
            existing = invoices[existing_id]
            return JSONResponse(
                status_code=200,
                content=_invoice_response(existing),
            )

    # Duplicate invoice_number check
    if REJECTS_DUPLICATES and payload.invoice_number in invoice_number_index:
        raise HTTPException(
            status_code=409,
            detail=f"Invoice number '{payload.invoice_number}' already exists",
        )

    # Create record
    target_id = _next_id()
    now = _now_iso()
    record: dict[str, Any] = {
        "id": target_id,
        **payload.model_dump(),
        "created_at": now,
        "updated_at": now,
        "history": [{"action": "created", "at": now}],
    }
    invoices[target_id] = record
    invoice_number_index[payload.invoice_number] = target_id

    if SUPPORTS_IDEMPOTENCY and idempotency_key:
        idempotency_keys[idempotency_key] = target_id

    return _invoice_response(record)


@app.get("/invoices")
def list_invoices(authorization: str | None = Header(None)):
    _verify_token(authorization)
    _check_rate_limit()
    all_invoices = [_invoice_response(r) for r in invoices.values()]
    return {"invoices": all_invoices, "total": len(all_invoices)}


@app.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: str, authorization: str | None = Header(None)):
    _verify_token(authorization)
    _check_rate_limit()
    record = invoices.get(invoice_id)
    if not record:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return _invoice_response(record)


@app.put("/invoices/{invoice_id}")
def update_invoice(
    invoice_id: str,
    payload: InvoicePayload,
    authorization: str | None = Header(None),
):
    _verify_token(authorization)
    _check_rate_limit()

    if REQUIRES_REVERSAL:
        raise HTTPException(
            status_code=405,
            detail="Updates not supported; use POST /invoices/{id}/reverse instead",
        )
    if not SUPPORTS_UPDATE:
        raise HTTPException(status_code=405, detail="Updates not supported by this target")

    record = invoices.get(invoice_id)
    if not record:
        raise HTTPException(status_code=404, detail="Invoice not found")

    now = _now_iso()
    # Update fields (keep original id and created_at)
    record.update(payload.model_dump())
    record["id"] = invoice_id
    record["updated_at"] = now
    record.setdefault("history", []).append({"action": "updated", "at": now})

    # Update invoice_number index if it changed
    if payload.invoice_number != record.get("invoice_number"):
        old_number = record.get("invoice_number")
        if old_number and old_number in invoice_number_index:
            del invoice_number_index[old_number]
        invoice_number_index[payload.invoice_number] = invoice_id

    return _invoice_response(record, status_override="updated")


@app.post("/invoices/{invoice_id}/reverse")
def reverse_invoice(
    invoice_id: str,
    body: ReverseRequest | None = None,
    authorization: str | None = Header(None),
):
    _verify_token(authorization)
    _check_rate_limit()

    record = invoices.get(invoice_id)
    if not record:
        raise HTTPException(status_code=404, detail="Invoice not found")

    now = _now_iso()
    reason = body.reason if body else "reversal requested"
    record["status"] = "reversed"
    record["updated_at"] = now
    record.setdefault("history", []).append({"action": "reversed", "at": now, "reason": reason})

    return _invoice_response(record, status_override="reversed")
