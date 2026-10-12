"""Explain the existing screener using its shared, unrounded statistics."""
from decimal import Decimal
from pathlib import Path
import json
from selection_model import SelectionPolicy, evaluate_candidate, screening_statistics


def rule_snapshot(policy=None):
    policy=policy or SelectionPolicy.load()
    version=json.loads(Path(__file__).with_name('selection_policy.json').read_text(encoding='utf-8'))['version']
    return {'version':version,'candidateLimit':policy.candidate_limit,
            'minListingDays':policy.min_listing_days,'minHistoryBars':65,
            'minAverageAmount':float(policy.min_average_amount),
            'maxDistanceAboveMa20':float(policy.max_distance_above_ma20),
            'earlyVolumeRatio':float(policy.early_volume_ratio),
            'earlyCrossLookback':policy.early_cross_lookback,'maRiseLookback':5,
            'sampleBasis':'有效成交日线（历史停牌日剔除）',
            'ranking':['return20_desc','averageAmount20_desc','code_asc'],
            'branchPriority':['trend','early'],'usesMoneyFlow':False,'usesNews':False}


def explain_candidate(instrument,bars,*,as_of,rank,qualified_count,policy=None):
    policy=policy or SelectionPolicy.load()
    bars=tuple(bars)
    assessment=evaluate_candidate(instrument,bars,as_of=as_of,daily_data_complete=True,policy=policy)
    if assessment.candidate is None:
        raise ValueError('未通过筛选，不能生成入选解释：'+assessment.reasons[0])
    candidate=assessment.candidate
    stats=screening_statistics(bars,policy)
    def row(key,label,value,unit,rule,passed):
        return {'key':key,'label':label,'value':value,'unit':unit,'rule':rule,'passed':bool(passed)}
    basic=[
        row('board','股票范围','创业板' if instrument.board=='chinext' else '沪深主板','',
            '沪深主板或创业板；排除科创板与北交所',instrument.board in ('mainboard','chinext')),
        row('status','股票状态','非风险警示、非退市整理、未停牌','','三项全部满足',
            not (instrument.is_st or instrument.delisting or instrument.suspended)),
        row('listing','可验证上市历史下限',instrument.listing_trading_days,'个市场交易日',
            f'至少{policy.min_listing_days}个市场交易日',instrument.listing_trading_days>=policy.min_listing_days),
        row('history','有效日线样本',len(bars),'条','至少65条',len(bars)>=65),
        row('date','最新数据日期',bars[-1].day.isoformat(),'',f'等于筛选日期 {as_of}',bars[-1].day==as_of),
        row('validation','价量额与日期检查','已通过','','价格有效、价量额非负、日期递增且无未来记录',True)]
    common=[
        row('amount','20日平均成交额',float(stats['averageAmount20']),'亿元',
            f'≥ {float(policy.min_average_amount)/1e8:g}亿元',stats['averageAmount20']>=policy.min_average_amount),
        row('price','收盘价',float(bars[-1].close),'元',
            f'> MA20（{float(stats["ma20"]):.4f}元）',bars[-1].close>stats['ma20']),
        row('distance','收盘价高于MA20的幅度',float(stats['distanceAboveMa20']),'percent',
            f'≤ {float(policy.max_distance_above_ma20)*100:g}%',bars[-1].close<=stats['ma20']*(1+policy.max_distance_above_ma20)),
        row('rise','当前MA20',float(stats['ma20']),'元',
            f'> 5个有效成交日前的MA20（{float(stats["ma20Before5"]):.4f}元）',stats['ma20']>stats['ma20Before5'])]
    trend=[
        row('ma20vs60','MA20',float(stats['ma20']),'元',f'> MA60（{float(stats["ma60"]):.4f}元）',stats['ma20']>stats['ma60']),
        row('volume5vs20','5日平均成交量',float(stats['volume5']),'万股',
            f'≥ 20日平均成交量（{float(stats["volume20"])/10000:,.2f}万股）',stats['volume5']>=stats['volume20'])]
    early=[
        row('cross','近期上穿20日均价',list(stats['crossDates']),'dates',
            f'最近{policy.early_cross_lookback}个有效成交日内至少一次',bool(stats['crossDates'])),
        row('ma5vs20','MA5',float(stats['ma5']),'元',f'> MA20（{float(stats["ma20"]):.4f}元）',stats['ma5']>stats['ma20']),
        row('priorVolume','此前20日平均成交量',float(stats['priorVolume20']),'万股','> 0（不含当天）',stats['priorVolume20']>0),
        row('volumeRatio','当日量 / 此前20日均量',float(stats['lastVolumeRatio']) if stats['lastVolumeRatio'] is not None else None,'倍',
            f'≥ {policy.early_volume_ratio}倍',stats['priorVolume20']>0 and stats['lastVolume']>=stats['priorVolume20']*policy.early_volume_ratio)]
    def serial(value):
        if isinstance(value,Decimal): return float(value)
        if isinstance(value,tuple): return list(value)
        return value
    return {'rank':rank,'qualifiedCount':qualified_count,
            'matchedBranch':'trend' if candidate.kind=='趋势延续观察' else 'early',
            'basicChecks':basic,'commonChecks':common,
            'branches':{'trend':{'passed':all(c['passed'] for c in trend),'checks':trend},
                        'early':{'passed':all(c['passed'] for c in early),'checks':early}},
            'indicators':{key:serial(value) for key,value in stats.items()},
            'listingBasis':'缓存最早日线至筛选日的交易所开市日数量，仅为上市历史下限，不是精确上市天数。',
            'sampleBasis':'5/20/60日使用有成交的有效日线样本；历史停牌日剔除。',
            'comparisonBasis':'判断使用未四舍五入指标，显示值仅为阅读作了舍入。'}
