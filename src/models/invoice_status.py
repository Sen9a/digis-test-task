from enum import Enum


class InvoiceStatus(str, Enum):
    """Normalized invoice status across all systems."""

    DRAFT = "draft"
    SENT = "sent"
    PAID = "paid"
    OVERDUE = "overdue"
    VOID = "void"
    CREDIT_NOTE = "credit_note"
