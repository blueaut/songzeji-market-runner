"""Generate a clearly labelled example without market data or network access."""

from pathlib import Path
from decimal import Decimal
from datetime import date, timedelta
from dataclasses import replace
from risk_model import Policy, plan_buy, stop_alert
from selection_model import Bar, Instrument, select_candidates


policy = Policy.load()
conservative = Policy.load(Path(__file__).with_name("risk_policy_conservative.json"))
moderate = Policy.load(Path(__file__).with_name("risk_policy_moderate.json"))
account = dict(equity="100000", cash="100000", held_value="0", held_count=0,
                  open_planned_risk="0", peak_equity="100000",
                  entry_price="20", stop_price="18.80")
result = plan_buy(**account, policy=policy)
baseline = plan_buy(**account, policy=conservative)
middle = plan_buy(**account, policy=moderate)
risk_budget = Decimal(account["equity"]) * policy.risk_per_trade
next_quantity = result.quantity + 100
next_notional = Decimal(account["entry_price"]) * next_quantity
next_buffer = max(next_notional * policy.cost_buffer_rate, policy.minimum_cost_buffer)
next_loss = (Decimal(account["entry_price"]) - Decimal(account["stop_price"])) * next_quantity + next_buffer
t1 = stop_alert(last_price="18.70", stop_price="18.80", quantity=result.quantity,
                sellable_quantity=0, quote_age_seconds=10)
stale = stop_alert(last_price="18.70", stop_price="18.80", quantity=result.quantity,
                   sellable_quantity=result.quantity, quote_age_seconds=300)
report = f"""# 风险规则演示：全部为虚构数据

这是一份程序计算示例，不含真实股票、实时行情或投资建议。

| 示例项目 | 计算结果 |
| --- | --- |
| 模拟账户权益与现金 | 100,000元 |
| 假设买入价格 | 20.00元/股 |
| 假设止损参考价 | 18.80元/股（价格距离6%） |
| 当前单笔计划风险预算 | {risk_budget:.2f}元（账户权益{policy.risk_per_trade * 100:.2f}%） |
| 本次模拟数量上限 | {result.quantity}股 |
| 对应股票市值 | {result.notional:.2f}元 |
| 成本与滑点缓冲 | {result.cost_buffer:.2f}元（假设值） |
| 到止损参考价的计划亏损，含缓冲 | {result.planned_loss:.2f}元 |
| 含全程缓冲的现金占用 | {result.cash_required_with_buffer:.2f}元 |

按100股一组向下取整。增加到{next_quantity}股，含假设缓冲的计划损失为{next_loss:.2f}元，超过{risk_budget:.2f}元预算，因此不能取{next_quantity}股。

## 同一虚构场景下的新旧方案

| 比较项 | 原保守方案 | 上一版适度进取 | 当前积极型 |
| --- | --- | --- | --- |
| 单笔计划风险预算 | {Decimal(account['equity']) * conservative.risk_per_trade:.2f}元 | {Decimal(account['equity']) * moderate.risk_per_trade:.2f}元 | {risk_budget:.2f}元 |
| 模拟数量上限 | {baseline.quantity}股 | {middle.quantity}股 | {result.quantity}股 |
| 股票市值 | {baseline.notional:.2f}元 | {middle.notional:.2f}元 | {result.notional:.2f}元 |
| 含缓冲的计划损失 | {baseline.planned_loss:.2f}元 | {middle.planned_loss:.2f}元 | {result.planned_loss:.2f}元 |

当前方案单股{policy.max_single_position * 100:.0f}%、总仓位{policy.max_total_position * 100:.0f}%都是上限。风险预算和实际信号会进一步限制投入，并不是要求把这些额度用满。组合回撤暂停线仍为{policy.pause_drawdown * 100:.0f}%。

## 两类异常也能区分

- 当前无可卖股份：{t1['message']}。
- 行情已经过期：{stale['message']}。

## 如何理解这个数字

{result.planned_loss:.2f}元是指定入场价、止损参考价和成本假设下的计划值。若跳空、停牌、跌停、无法及时卖出或实际费用较高，实际亏损可以超过这个值，甚至超过{risk_budget:.2f}元预算。

风险模块只完成模拟仓位计算与止损提醒分类；每日筛选另有输入数据的计算原型。没有接通真实行情、手机通知或券商。
"""
output = Path(__file__).resolve().parents[1] / "风险规则演示.md"
output.write_text(report, encoding="utf-8")
print(f"已生成虚构数据演示：{output}")


def mock_bars(closes, boosted=False):
    days = []
    day = date(2026, 10, 9)
    while len(days) < len(closes):
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    bars = []
    for index, (day, close) in enumerate(zip(days, closes)):
        volume = Decimal("20000000") if boosted and index == len(closes) - 1 else Decimal("10000000")
        bars.append(Bar.create(day, close * Decimal("1.01"), close * Decimal("0.99"),
                               close, volume, close * volume))
    return bars


trend_bars = mock_bars([Decimal("10") + Decimal(index) * Decimal("0.03") for index in range(65)])
early_bars = mock_bars([Decimal("14")] * 40 + [Decimal("10")] * 22
                       + [Decimal("10.1"), Decimal("10.2"), Decimal("10.3")], True)
early_bars[50] = replace(early_bars[50], high=Decimal("11"))
trend_stock = Instrument("DEMO-A", "虚构趋势甲", "mainboard", 200, False, False, False, True, "虚构数据")
early_stock = replace(trend_stock, code="DEMO-B", name="虚构转强乙")
candidates, _ = select_candidates([(trend_stock, trend_bars), (early_stock, early_bars)],
                                  as_of=trend_bars[-1].day, daily_data_complete=True)
rows = "\n".join(f"| {candidate.name} | {candidate.code} | {candidate.kind} | {candidate.reference_high:.2f}元 |"
                 for candidate in candidates)
details = "\n\n".join(f"**{candidate.name}**：{'；'.join(candidate.reasons)}。" for candidate in candidates)
selection_report = f"""# 选股规则演示：全部为虚构数据

这里的名称、代码、价格、成交数据和观测日期仅用于程序演示。日期序列不代表经过核实的交易所日历。

## 两类候选可以分别解释

| 虚构股票 | 演示代码 | 观察类型 | 后续盘中参考价 |
| --- | --- | --- | --- |
{rows}

{details}

趋势延续类采用截至筛选日的20日高点；近期转强类采用5日高点，作为下一交易日待观察的盘中条件。这些类型名称只描述计算条件，不预测未来上涨。

当前候选上限10只、每天新仓计划上限2个。每日新仓额度、盘中条件确认、风险预留和通知发送需要在完整系统中实现，本示例未实现这些流程。

筛选会排除不完整、过期、含未来日期的数据，以及风险警示、停牌、上市时间不足、成交不足、价格距离均价过远等样本。

这是计算流程演示，不是历史回测或真实选股结果。允许当日没有候选，也不要求每日进行交易。
"""
selection_output = output.with_name("选股规则演示.md")
selection_output.write_text(selection_report, encoding="utf-8")
print(f"已生成虚构选股演示：{selection_output}")
