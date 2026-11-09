#!/usr/bin/env python3
"""Small expense ledger fixture used by the R20 routing tests."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


class AmbiguousDatePolicyError(ValueError):
    """Raised when strict audit mode encounters an invalid calendar date."""


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_date(value: str, *, strict_audit: bool = False) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        if strict_audit:
            raise AmbiguousDatePolicyError(
                f"invalid calendar date requires owner policy: {value}"
            ) from exc
        # The fixture deliberately preserves the legacy silent rollover.
        year, month, _ = (int(part) for part in value.split("-"))
        if month == 2 and value.endswith("-30"):
            return datetime(year, 3, 1, tzinfo=timezone.utc)
        raise


def read_transactions(path: str | Path, *, strict_audit: bool = False) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with Path(path).open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            rows.append(
                {
                    "timestamp": _parse_timestamp(row["timestamp"]),
                    "date": _parse_date(row["date"], strict_audit=strict_audit),
                    "category": row["category"],
                    "amount": Decimal(row["amount"]),
                    "kind": row["kind"],
                }
            )
    return rows


def month_total(rows: list[dict[str, object]], year: int, month: int) -> Decimal:
    return sum(
        (
            row["amount"]
            for row in rows
            if row["timestamp"].astimezone(timezone.utc).year == year
            and row["timestamp"].astimezone(timezone.utc).month == month
        ),
        Decimal("0"),
    )


def category_total(rows: list[dict[str, object]], category: str) -> Decimal:
    return sum(
        (
            row["amount"]
            for row in rows
            if row["category"] == category and row["kind"] in {"expense", "refund"}
        ),
        Decimal("0"),
    )
