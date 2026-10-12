"""Simulation-only risk sizing. No market connection, orders or notifications."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import json
from pathlib import Path


def decimal_value(value):
    if isinstance(value, bool):
        raise ValueError("数值不能使用布尔值")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("需要有效数字") from exc
    if not result.is_finite():
        raise ValueError("数值必须有限")
    return result


def whole_number(value):
    result = decimal_value(value)
    if result < 0 or result != result.to_integral_value():
        raise ValueError("数量必须是非负整数")
    return int(result)


@dataclass(frozen=True)
class Policy:
    risk_per_trade: Decimal
    max_single_position: Decimal
    max_total_position: Decimal
    max_positions: int
    max_open_planned_risk: Decimal
    pause_drawdown: Decimal
    cost_buffer_rate: Decimal
    minimum_cost_buffer: Decimal

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path else Path(__file__).with_name("risk_policy.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.pop("mode") != "simulation_only":
            raise ValueError("原型仅支持模拟研究")
        data.pop("version")
        fields = cls.__dataclass_fields__
        if set(data) != set(fields):
            raise ValueError("风险参数缺失或含有未知字段")
        values = {key: decimal_value(value) for key, value in data.items()}
        values["max_positions"] = whole_number(data["max_positions"])
        for key in fields:
            if key not in ("max_positions", "minimum_cost_buffer"):
                if not 0 < values[key] < 1:
                    raise ValueError("比例参数必须在0与1之间")
        if values["max_positions"] < 1 or values["minimum_cost_buffer"] < 0:
            raise ValueError("持仓数或成本缓冲无效")
        if values["max_single_position"] > values["max_total_position"]:
            raise ValueError("单股上限不能超过总仓位上限")
        if values["risk_per_trade"] > values["max_open_planned_risk"]:
            raise ValueError("单笔风险不能超过组合计划风险上限")
        return cls(**values)


@dataclass(frozen=True)
class BuyPlan:
    quantity: int
    notional: Decimal
    planned_loss: Decimal
    cost_buffer: Decimal
    cash_required_with_buffer: Decimal
    reason: str
    pause_required: bool = False


def plan_buy(*, equity, cash, held_value, held_count, open_planned_risk,
             peak_equity, entry_price, stop_price, already_held=False,
             paused=False, policy=None):
    """Inputs must include pending plans/reservations in portfolio exposure.

    Returns a research sizing ceiling, not a trade recommendation. Costs use an
    assumed buffer, not a broker fee schedule. Execution losses may exceed it.
    """
    policy = policy or Policy.load()
    equity, cash, held_value, open_planned_risk, peak_equity, entry_price, stop_price = (
        decimal_value(value) for value in
        (equity, cash, held_value, open_planned_risk, peak_equity, entry_price, stop_price)
    )
    held_count = whole_number(held_count)
    if equity <= 0 or peak_equity <= 0:
        raise ValueError("权益及历史最高权益必须大于0")
    if min(cash, held_value, open_planned_risk) < 0:
        raise ValueError("现金、持仓及计划风险不能为负")
    if not 0 < stop_price < entry_price:
        raise ValueError("止损价格必须大于0且低于入场价格")
    if not isinstance(already_held, bool) or not isinstance(paused, bool):
        raise ValueError("状态必须是布尔值")

    def blocked(reason, pause_required=False):
        return BuyPlan(0, Decimal(0), Decimal(0), Decimal(0), Decimal(0), reason, pause_required)

    drawdown = max(Decimal(0), (peak_equity - equity) / peak_equity)
    if drawdown >= policy.pause_drawdown:
        return blocked("组合回撤达到暂停阈值，需复盘后人工解除", True)
    if paused:
        return blocked("买入提醒处于人工复盘暂停状态", True)
    if already_held:
        return blocked("第一版不对已有持仓追加买入")
    if held_count >= policy.max_positions:
        return blocked("已达到持仓数量上限")

    risk_budget = min(equity * policy.risk_per_trade,
                      equity * policy.max_open_planned_risk - open_planned_risk)
    value_budget = min(equity * policy.max_single_position,
                       equity * policy.max_total_position - held_value, cash)
    if risk_budget <= 0 or value_budget <= 0:
        return blocked("剩余现金、仓位或计划风险额度不足")

    # Main-board prototype only: buy in whole lots of 100 shares.
    upper_lots = int((value_budget / (entry_price * 100)).to_integral_value(rounding=ROUND_FLOOR))

    def amounts(lots):
        quantity = lots * 100
        notional = entry_price * quantity
        buffer = max(notional * policy.cost_buffer_rate, policy.minimum_cost_buffer)
        loss = (entry_price - stop_price) * quantity + buffer
        return quantity, notional, buffer, loss

    # Search the largest quantity meeting every cap, including cost allowance.
    low, high = 0, upper_lots
    while low < high:
        middle = (low + high + 1) // 2
        _, notional, buffer, loss = amounts(middle)
        if loss <= risk_budget and notional + buffer <= cash:
            low = middle
        else:
            high = middle - 1
    if low == 0:
        return blocked("剩余额度不足以覆盖100股及成本缓冲")
    quantity, notional, buffer, loss = amounts(low)
    return BuyPlan(quantity, notional, loss, buffer, notional + buffer,
                   "满足模拟额度；仍需有效买入信号、最新数据及人工确认")


def stop_alert(*, last_price, stop_price, quantity, sellable_quantity,
               quote_age_seconds, max_quote_age_seconds=120):
    """Stop alert respects supplied broker/ledger sellable shares; no T+1 guess."""
    last_price, stop_price = decimal_value(last_price), decimal_value(stop_price)
    quantity, sellable_quantity = whole_number(quantity), whole_number(sellable_quantity)
    age, max_age = decimal_value(quote_age_seconds), decimal_value(max_quote_age_seconds)
    if min(last_price, stop_price) <= 0 or age < 0 or max_age <= 0:
        raise ValueError("价格与行情时间无效")
    if sellable_quantity > quantity:
        raise ValueError("可卖数量不能超过总持仓")
    if age > max_age:
        return {"kind": "data_error", "sellable_quantity": 0,
                "message": "行情过期，暂停价格信号并检查数据源"}
    if quantity == 0 or last_price > stop_price:
        return {"kind": "none", "sellable_quantity": 0, "message": "未触发止损条件"}
    if sellable_quantity == 0:
        return {"kind": "risk_only", "sellable_quantity": 0,
                "message": "触及止损，但当前无可卖股份；保留风险提醒"}
    return {"kind": "exit_condition", "sellable_quantity": sellable_quantity,
            "message": "触及止损条件；可卖数量已列出，实际成交仍需确认"}
