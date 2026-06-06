"""Tests for the ITC engine (claim / defer / time-barred block)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.tax.blocked_credits import BlockedResult, LineBlockOutcome, compute_blocked
from app.tax.itc import PeriodContext, compute_itc
from app.tax.types import LineItem, Party, Transaction
from app.utils.date_utils import FilingPeriod
from app.utils.decimal_utils import ZERO


def _line(*, cgst: str = "180", sgst: str = "180", igst: str = "0") -> LineItem:
    return LineItem(
        description="widget",
        hsn_sac="8517",
        taxable_value=Decimal("2000"),
        cgst_amount=Decimal(cgst),
        sgst_amount=Decimal(sgst),
        igst_amount=Decimal(igst),
    )


def _tx(
    *lines: LineItem,
    document_type: str = "B2B_INVOICE",
    invoice_date: date | None = None,
) -> Transaction:
    return Transaction(
        document_type=document_type,
        vendor=Party(gstin=None),
        buyer=Party(gstin=None),
        invoice_date=invoice_date,
        line_items=lines,
    )


def _no_block(num_lines: int) -> BlockedResult:
    return BlockedResult(
        block_amount=ZERO,
        per_line=tuple(
            LineBlockOutcome(line_index=i, blocked=False, blocked_tax=ZERO)
            for i in range(num_lines)
        ),
    )


class TestClaim:
    def test_simple_claim(self) -> None:
        tx = _tx(_line(), invoice_date=date(2025, 4, 15))
        result = compute_itc(
            tx,
            blocked=_no_block(1),
            period_context=PeriodContext(current=FilingPeriod(2025, 4), as_of=date(2025, 5, 1)),
        )
        assert result.claim_amount == Decimal("360.00")
        assert result.defer_amount == Decimal("0.00")
        assert result.additional_block_amount == Decimal("0.00")
        assert result.claim_reasons == ("section_16_eligible",)

    def test_blocked_portion_is_subtracted(self) -> None:
        tx = _tx(_line(cgst="900", sgst="900"), invoice_date=date(2025, 4, 15))
        # Blocked engine reported the whole line tax as blocked already.
        blocked = BlockedResult(
            block_amount=Decimal("1800.00"),
            per_line=(
                LineBlockOutcome(
                    line_index=0,
                    blocked=True,
                    blocked_tax=Decimal("1800.00"),
                    category_code="motor_vehicle",
                ),
            ),
        )
        result = compute_itc(
            tx,
            blocked=blocked,
            period_context=PeriodContext(current=FilingPeriod(2025, 4), as_of=date(2025, 5, 1)),
        )
        assert result.claim_amount == Decimal("0.00")


class TestDefer:
    def test_cross_period_locked_invoice_defers(self) -> None:
        # Invoice was March 2025; March is already locked. We're now in
        # April 2025. Defer the ITC.
        tx = _tx(_line(), invoice_date=date(2025, 3, 20))
        ctx = PeriodContext(
            current=FilingPeriod(2025, 4),
            locked_periods=frozenset({(3, 2025)}),
            as_of=date(2025, 4, 10),
        )
        result = compute_itc(tx, blocked=_no_block(1), period_context=ctx)
        assert result.defer_amount == Decimal("360.00")
        assert result.claim_amount == Decimal("0.00")
        assert result.defer_reasons == ("cross_period_defer",)

    def test_cross_period_but_open_claims_now(self) -> None:
        # Invoice March 2025; March NOT locked → still claimable in
        # current period.
        tx = _tx(_line(), invoice_date=date(2025, 3, 20))
        ctx = PeriodContext(
            current=FilingPeriod(2025, 4),
            locked_periods=frozenset(),
            as_of=date(2025, 4, 10),
        )
        result = compute_itc(tx, blocked=_no_block(1), period_context=ctx)
        assert result.claim_amount == Decimal("360.00")
        assert result.defer_amount == Decimal("0.00")


class TestTimeBarred:
    def test_past_section_16_4_cutoff(self) -> None:
        # FY 2023-24 invoice → cutoff 30 Nov 2024. We're in Dec 2024.
        tx = _tx(_line(), invoice_date=date(2024, 3, 15))
        ctx = PeriodContext(current=FilingPeriod(2024, 12), as_of=date(2024, 12, 5))
        result = compute_itc(tx, blocked=_no_block(1), period_context=ctx)
        assert result.additional_block_amount == Decimal("360.00")
        assert result.claim_amount == Decimal("0.00")
        assert result.block_reasons == ("time_barred",)

    def test_at_cutoff_still_claimable(self) -> None:
        tx = _tx(_line(), invoice_date=date(2024, 3, 15))
        ctx = PeriodContext(current=FilingPeriod(2024, 11), as_of=date(2024, 11, 30))
        result = compute_itc(tx, blocked=_no_block(1), period_context=ctx)
        assert result.additional_block_amount == Decimal("0.00")
        # Cutoff is 30 Nov 2024; we're AT the cutoff, not past → still claim.
        assert result.claim_amount == Decimal("360.00")


def test_integration_with_compute_blocked() -> None:
    """Smoke test: feed real BlockedResult through compute_itc."""
    tx = _tx(
        _line(cgst="900", sgst="900"),
        _line(cgst="90", sgst="90"),
        document_type="B2B_INVOICE",
        invoice_date=date(2025, 4, 15),
    )
    # Replace first line's HSN with a motor-vehicle prefix.
    motor_line = LineItem(
        description="car",
        hsn_sac="87031010",
        taxable_value=Decimal("10000"),
        cgst_amount=Decimal("900"),
        sgst_amount=Decimal("900"),
        igst_amount=ZERO,
    )
    tx = Transaction(
        document_type="B2B_INVOICE",
        vendor=Party(gstin=None),
        buyer=Party(gstin=None),
        invoice_date=date(2025, 4, 15),
        line_items=(motor_line, tx.line_items[1]),
    )
    blocked = compute_blocked(tx)
    itc = compute_itc(
        tx,
        blocked=blocked,
        period_context=PeriodContext(current=FilingPeriod(2025, 4), as_of=date(2025, 5, 1)),
    )
    assert blocked.block_amount == Decimal("1800.00")
    # Only the eligible second line remains: 90 + 90 = 180.00.
    assert itc.claim_amount == Decimal("180.00")
