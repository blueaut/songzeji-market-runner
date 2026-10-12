"""Daily screening experiment. All data must be supplied; no live data fetch."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
import json

from risk_model import decimal_value, whole_number


@dataclass(frozen=True)
class SelectionPolicy:
    candidate_limit: int
    min_listing_days: int
    min_average_amount: Decimal
    max_distance_above_ma20: Decimal
    early_volume_ratio: Decimal
    early_cross_lookback: int
    max_new_plans_per_day: int

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path else Path(__file__).with_name("selection_policy.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.pop("mode") != "simulation_only":
            raise ValueError("筛选原型仅支持模拟")
        data.pop("version")
        if set(data) != set(cls.__dataclass_fields__):
            raise ValueError("筛选参数不完整或存在未知字段")
        integers = {"candidate_limit", "min_listing_days", "early_cross_lookback", "max_new_plans_per_day"}
        values = {key: whole_number(value) if key in integers else decimal_value(value)
                  for key, value in data.items()}
        if any(values[key] < 1 for key in integers):
            raise ValueError("数量参数必须为正整数")
        if not 1 <= values["early_cross_lookback"] <= 5:
            raise ValueError("转强观察窗口必须在1至5之间")
        if values["min_average_amount"] <= 0 or values["early_volume_ratio"] <= 1:
            raise ValueError("成交条件无效")
        if not 0 < values["max_distance_above_ma20"] < 1:
            raise ValueError("价格距离参数无效")
        return cls(**values)


@dataclass(frozen=True)
class Instrument:
    code: str
    name: str
    board: str
    listing_trading_days: int
    is_st: bool
    delisting: bool
    suspended: bool
    metadata_verified: bool
    source: str


@dataclass(frozen=True)
class Bar:
    day: date
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal

    def __post_init__(self):
        if type(self.day) is not date:
            raise ValueError("日行情需要日期，不含盘中时间")
        for field in ("high", "low", "close", "volume", "amount"):
            object.__setattr__(self, field, decimal_value(getattr(self, field)))

    @classmethod
    def create(cls, day, high, low, close, volume, amount):
        return cls(day, *(decimal_value(value) for value in (high, low, close, volume, amount)))


@dataclass(frozen=True)
class Candidate:
    code: str
    name: str
    kind: str
    as_of: date
    reference_high: Decimal
    return_20: Decimal
    average_amount_20: Decimal
    source: str
    reasons: tuple


@dataclass(frozen=True)
class Assessment:
    candidate: Candidate | None
    reasons: tuple


def mean(values):
    values = list(values)
    return sum(values, Decimal(0)) / len(values)


def screening_statistics(bars, policy=None):
    """Shared unrounded indicators for screening and its visible explanation."""
    policy = policy or SelectionPolicy.load()
    bars = tuple(bars)
    if len(bars) < 65:
        raise ValueError("指标计算至少需要65个有效日线样本")
    closes = [bar.close for bar in bars]
    ma5, ma20, ma60 = (mean(closes[-n:]) for n in (5, 20, 60))
    previous_ma20 = mean(closes[-25:-5])
    volume5 = mean(bar.volume for bar in bars[-5:])
    volume20 = mean(bar.volume for bar in bars[-20:])
    prior_volume20 = mean(bar.volume for bar in bars[-21:-1])
    crosses = []
    for index in range(len(closes) - policy.early_cross_lookback, len(closes)):
        before = mean(closes[index - 20:index])
        today = mean(closes[index - 19:index + 1])
        if closes[index - 1] <= before and closes[index] > today:
            crosses.append(bars[index].day.isoformat())
    return {'ma5': ma5, 'ma20': ma20, 'ma60': ma60,
            'ma20Before5': previous_ma20, 'volume5': volume5, 'volume20': volume20,
            'priorVolume20': prior_volume20, 'lastVolume': bars[-1].volume,
            'lastVolumeRatio': bars[-1].volume / prior_volume20 if prior_volume20 > 0 else None,
            'averageAmount20': mean(bar.amount for bar in bars[-20:]),
            'distanceAboveMa20': closes[-1] / ma20 - 1,
            'return20': closes[-1] / closes[-21] - 1, 'crossDates': tuple(crosses)}


def evaluate_candidate(instrument, bars, *, as_of, daily_data_complete, policy=None):
    """Caller supplies prices adjusted consistently as of the evaluation date.

    Metadata and completeness are trusted inputs from a future data adapter;
    this function validates chronology and price fields, not vendor quality.
    """
    policy = policy or SelectionPolicy.load()

    def excluded(reason):
        return Assessment(None, (reason,))

    if not isinstance(daily_data_complete, bool):
        raise ValueError("数据完整状态必须为布尔值")
    if not daily_data_complete:
        return excluded("收盘数据尚未完整")
    if not isinstance(as_of, date):
        raise ValueError("需要筛选日期")
    for field in (instrument.is_st, instrument.delisting, instrument.suspended, instrument.metadata_verified):
        if not isinstance(field, bool):
            raise ValueError("股票状态必须明确为布尔值")
    if not instrument.metadata_verified or not instrument.source:
        return excluded("股票状态或数据来源未经确认")
    if instrument.board not in ("mainboard", "chinext"):
        return excluded("研究范围为沪深主板和创业板，排除科创板与北交所")
    if instrument.is_st or instrument.delisting or instrument.suspended:
        return excluded("风险警示、退市整理或停牌股票不进入候选")
    if whole_number(instrument.listing_trading_days) < policy.min_listing_days:
        return excluded("上市交易日数量不足")
    bars = tuple(bars)
    if len(bars) < 65:
        return excluded("至少需要65个有效交易日样本")
    if bars[-1].day != as_of:
        return excluded("历史行情未更新到筛选日期或含有未来数据")
    previous_day = None
    for bar in bars:
        if not isinstance(bar.day, date) or (previous_day is not None and bar.day <= previous_day):
            return excluded("行情日期重复或未按时间递增")
        if bar.day > as_of:
            return excluded("行情包含未来数据")
        previous_day = bar.day
        high, low, close, volume, amount = (
            decimal_value(value) for value in (bar.high, bar.low, bar.close, bar.volume, bar.amount)
        )
        if not 0 < low <= close <= high or min(volume, amount) < 0:
            return excluded("行情价格、成交量或成交额无效")
    if bars[-1].volume <= 0 or bars[-1].amount <= 0:
        return excluded("当日没有有效成交数据")

    stats = screening_statistics(bars, policy)
    ma5, ma20, ma60 = (stats[key] for key in ('ma5', 'ma20', 'ma60'))
    previous_ma20 = stats['ma20Before5']
    close = bars[-1].close
    average_amount = stats['averageAmount20']
    if average_amount < policy.min_average_amount:
        return excluded("近20日平均成交额不足")
    if not ma20 < close <= ma20 * (1 + policy.max_distance_above_ma20):
        return excluded("未站上20日均价或距离该均价过远")
    if ma20 <= previous_ma20:
        return excluded("20日均价尚未抬升")

    volume5, volume20 = stats['volume5'], stats['volume20']
    if ma20 > ma60 and volume5 >= volume20:
        kind = "趋势延续观察"
        window = 20
        reasons = ("20日均价高于60日均价且抬升", "5日均量不低于20日均量")
    else:
        previous_volume20 = stats['priorVolume20']
        if not (stats['crossDates'] and ma5 > ma20 and previous_volume20 > 0
                and bars[-1].volume >= previous_volume20 * policy.early_volume_ratio):
            return excluded("未满足趋势延续或近期转强条件")
        kind = "近期转强观察"
        window = 5
        reasons = (f"近{policy.early_cross_lookback}日内从20日均价下方转到上方", "5日均价高于20日均价",
                   f"当日成交量达到此前20日均量的{policy.early_volume_ratio}倍")
    candidate = Candidate(instrument.code, instrument.name, kind, as_of,
                          max(bar.high for bar in bars[-window:]),
                          stats['return20'], average_amount, instrument.source,
                          reasons + ("入选仅供观察，买入须另行满足盘中条件和风险额度",))
    return Assessment(candidate, candidate.reasons)


def select_candidates(items, *, as_of, daily_data_complete, policy=None):
    policy = policy or SelectionPolicy.load()
    assessments = {}
    for instrument, bars in items:
        if instrument.code in assessments:
            raise ValueError("重复股票代码")
        assessments[instrument.code] = evaluate_candidate(
            instrument, bars, as_of=as_of, daily_data_complete=daily_data_complete, policy=policy)
    candidates = [item.candidate for item in assessments.values() if item.candidate is not None]
    candidates.sort(key=lambda candidate: (-candidate.return_20, -candidate.average_amount_20, candidate.code))
    return candidates[:policy.candidate_limit], assessments
