from __future__ import annotations

import json
from pathlib import Path


class CostCapExceeded(RuntimeError):
    pass


def token_cost(input_tokens: int, output_tokens: int, price_in: float, price_out: float) -> float:
    return (input_tokens * price_in + output_tokens * price_out) / 1e6


def estimate_input_tokens(messages: list[dict]) -> int:
    return sum(len(m["content"]) for m in messages) // 3 + 50


def spent_from_logs(paths) -> float:
    total = 0.0
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            if line.strip():
                total += float(json.loads(line).get("cost_usd") or 0.0)
    return total


class CostTracker:
    def __init__(self, cap_usd: float, spent_usd: float = 0.0, multiplier: float = 1.0):
        self.cap = float(cap_usd)
        self.spent = float(spent_usd)
        self.multiplier = float(multiplier)
        self._reserved: dict[int, float] = {}
        self._next = 0
        self.aborted = False

    @property
    def outstanding(self) -> float:
        return sum(self._reserved.values())

    def reserve(self, amount: float) -> int:
        amount *= self.multiplier
        if self.aborted:
            raise CostCapExceeded("run already aborted at cost cap")
        if self.spent + self.outstanding + amount > self.cap:
            self.aborted = True
            raise CostCapExceeded(
                f"cap ${self.cap:.2f} would be exceeded: spent ${self.spent:.4f} + reserved "
                f"${self.outstanding:.4f} + next ${amount:.4f}")
        self._next += 1
        self._reserved[self._next] = amount
        return self._next

    def settle(self, token: int, actual: float) -> None:
        self._reserved.pop(token, None)
        self.spent += float(actual) * self.multiplier

    def release(self, token: int) -> None:
        self._reserved.pop(token, None)


class GroupedTracker(CostTracker):
    def __init__(self, cap_usd: float, spent_usd: float = 0.0, multiplier: float = 1.0, group: CostTracker | None = None,
                 name: str = ""):
        super().__init__(cap_usd, spent_usd, multiplier)
        self.group, self.name = group, name
        self._group_tokens: dict[int, int] = {}

    def reserve(self, amount: float) -> int:
        g = self.group.reserve(amount * self.multiplier) if self.group is not None else None
        try:
            tok = super().reserve(amount)
        except CostCapExceeded:
            if g is not None:
                self.group.release(g)
            raise
        if g is not None:
            self._group_tokens[tok] = g
        return tok

    def settle(self, token: int, actual: float) -> None:
        super().settle(token, actual)
        g = self._group_tokens.pop(token, None)
        if self.group is not None:
            if g is not None:
                self.group._reserved.pop(g, None)
            self.group.spent += float(actual) * self.multiplier

    def release(self, token: int) -> None:
        super().release(token)
        g = self._group_tokens.pop(token, None)
        if g is not None and self.group is not None:
            self.group.release(g)

    def add_untracked(self, amount: float) -> None:
        self.spent += amount
        if self.group is not None:
            self.group.spent += amount


class AnthropicLedger(GroupedTracker):
    def __init__(self, cap_usd: float, spent_usd: float = 0.0, group: CostTracker | None = None):
        super().__init__(cap_usd, spent_usd, 1.0, group, "anthropic")


class OpenRouterLedger(GroupedTracker):
    def __init__(self, cap_usd: float, logged_spent_usd: float, account_fn, baseline_usage_usd: float,
                 every: int = 500, group: CostTracker | None = None, history: list | None = None):
        super().__init__(cap_usd, logged_spent_usd, 1.0, group, "openrouter")
        self.account_fn = account_fn
        self.baseline = float(baseline_usage_usd)
        self.every = every
        self.calls = 0
        self.history = history if history is not None else []
        self.account_spent = None

    def reconcile(self, when: str = "") -> dict | None:
        if self.account_fn is None:
            return None
        acct = self.account_fn()
        run = acct["usage_usd"] - self.baseline
        gap = run - self.spent
        if gap > 0:
            self.add_untracked(gap)
        self.account_spent = run
        rec = {"when": when, "calls_since_start": self.calls, "account_usage_usd": acct["usage_usd"],
               "account_run_spend_usd": run, "ledger_spent_usd": self.spent, "gap_added_usd": max(0.0, gap)}
        self.history.append(rec)
        if self.spent > self.cap:
            self.aborted = True
        return rec

    def settle(self, token: int, actual: float) -> None:
        super().settle(token, actual)
        self.calls += 1

    async def after_call(self) -> None:
        if self.account_fn is not None and self.every and self.calls and self.calls % self.every == 0:
            import asyncio
            await asyncio.to_thread(self.reconcile, f"every {self.every} calls")
