"""LLM 调用累计预算闸：跨会话、按日计数的本地账本（var/llm_budget.json）。

所有消耗 API 的路径（planner / litread 速读 / 经 MCP 暴露的同一工具）调用前
必须过闸；当日超限抛 BudgetExceeded，由调用方决定降级或停止。
上限：环境变量 LLM_DAILY_CALL_CAP（默认 500）；账本路径 LLM_BUDGET_LEDGER 可覆盖。
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

DEFAULT_LEDGER = Path(__file__).resolve().parents[3] / "var" / "llm_budget.json"
DEFAULT_DAILY_CAP = 500


class BudgetExceeded(RuntimeError):
    pass


def _today() -> str:
    return date.today().isoformat()


def record_and_check(n: int = 1, *, ledger: Path | None = None, cap: int | None = None) -> dict:
    """记账 n 次调用并检查当日额度；原子写防并发双写丢失。"""
    ledger = Path(ledger or os.environ.get("LLM_BUDGET_LEDGER", DEFAULT_LEDGER))
    cap = int(cap if cap is not None else os.environ.get("LLM_DAILY_CALL_CAP", DEFAULT_DAILY_CAP))
    today = _today()
    data = json.loads(ledger.read_text(encoding="utf-8")) if ledger.exists() else {}
    used = int(data.get(today, 0))
    if used + n > cap:
        raise BudgetExceeded(f"当日 LLM 调用预算耗尽：{used}/{cap}（{today}，ledger={ledger}）")
    data[today] = used + n
    ledger.parent.mkdir(parents=True, exist_ok=True)
    tmp = ledger.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ledger)
    return {"date": today, "used": used + n, "cap": cap}
