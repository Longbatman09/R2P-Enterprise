"""Minimal Stripe-backed invoicing service for school subscriptions.

Create a one-time invoice for a school or a recurring subscription per
plan (Basic / Pro / Enterprise).  This is intentionally thin — the school
dashboard calls these helpers via the MCP backend's /api/invoice/* REST
endpoints after we add them in main.py.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("r2p.invoicing")

# Optional — only imported if stripe is installed.
try:
    import stripe  # type: ignore

    _STRIPE_AVAILABLE = True
except ImportError:
    _STRIPE_AVAILABLE = False


@dataclass
class Invoice:
    id: str
    school_id: str
    amount_cents: int
    currency: str = "usd"
    status: str = "draft"
    stripe_invoice_id: str | None = None
    pdf_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "school_id": self.school_id,
            "amount_cents": self.amount_cents,
            "currency": self.currency,
            "status": self.status,
            "stripe_invoice_id": self.stripe_invoice_id,
            "pdf_url": self.pdf_url,
            "metadata": self.metadata,
        }


class InvoicingService:
    """Create, fetch, and void invoices via Stripe (or a local stub)."""

    def __init__(self, api_key: str | None = None) -> None:
        if _STRIPE_AVAILABLE and api_key:
            stripe.api_key = api_key
        self._api_key = api_key or os.environ.get("STRIPE_API_KEY", "")
        self._store: dict[str, Invoice] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_invoice(
        self,
        *,
        school_id: str,
        amount_cents: int,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Invoice:
        inv = Invoice(
            id=f"inv_{school_id}_{amount_cents}",
            school_id=school_id,
            amount_cents=amount_cents,
            metadata=metadata or {},
        )
        if _STRIPE_AVAILABLE and self._api_key:
            try:
                stripe_inv = stripe.Invoice.create(
                    customer=school_id,
                    auto_advance=True,
                    metadata={"school_id": school_id, "description": description},
                )
                # Add line item
                stripe.InvoiceItem.create(
                    customer=school_id,
                    amount=amount_cents,
                    currency="usd",
                    description=description or "R2P-Enterprise",
                )
                stripe_inv = stripe.Invoice.finalize_invoice(stripe_inv.id)
                inv.stripe_invoice_id = stripe_inv.id
                inv.status = "open"
                inv.pdf_url = stripe_inv.invoice_pdf
                log.info("Created Stripe invoice %s", stripe_inv.id)
            except Exception as exc:
                log.error("Stripe invoice creation failed: %s", exc)
                inv.status = "failed"
        else:
            inv.status = "draft"
            log.warning("Stripe not configured — invoice %s is draft only", inv.id)
        self._store[inv.id] = inv
        return inv

    def get(self, invoice_id: str) -> Invoice | None:
        return self._store.get(invoice_id)

    def list_for_school(self, school_id: str) -> list[Invoice]:
        return [inv for inv in self._store.values() if inv.school_id == school_id]

    def void(self, invoice_id: str) -> Invoice | None:
        inv = self._store.get(invoice_id)
        if not inv:
            return None
        if _STRIPE_AVAILABLE and inv.stripe_invoice_id and self._api_key:
            try:
                stripe.Invoice.void_invoice(inv.stripe_invoice_id)
                inv.status = "voided"
            except Exception as exc:
                log.error("Stripe void failed: %s", exc)
        else:
            inv.status = "voided"
        return inv
