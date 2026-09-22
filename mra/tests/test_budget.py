"""预算闸行为检查：超限熔断、按日滚动、原子账本。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from mra.budget import BudgetExceeded, record_and_check


def test_daily_cap_trips_and_rolls_over(tmp_path):
    ledger = tmp_path / "b.json"
    assert record_and_check(1, ledger=ledger, cap=2)["used"] == 1
    assert record_and_check(1, ledger=ledger, cap=2)["used"] == 2
    with pytest.raises(BudgetExceeded):
        record_and_check(1, ledger=ledger, cap=2)
    # 昨日记录不占今日额度
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    ledger.write_text('{"%s": 999}' % yesterday, encoding="utf-8")
    assert record_and_check(1, ledger=ledger, cap=2)["used"] == 1


def test_ledger_survives_and_counts(tmp_path):
    ledger = tmp_path / "b.json"
    record_and_check(3, ledger=ledger, cap=10)
    record_and_check(4, ledger=ledger, cap=10)
    assert record_and_check(1, ledger=ledger, cap=10)["used"] == 8
