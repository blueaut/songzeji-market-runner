"""Free BaoStock daily collection. No fabricated fallback; cache every symbol."""
from pathlib import Path
import sys, socket, json, time, argparse, os
from datetime import datetime, timedelta, timezone, date
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import Counter
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'data-deps'), str(ROOT / 'prototype')]
from selection_model import Instrument, Bar, select_candidates
from selection_explanation import explain_candidate, rule_snapshot
CACHE = ROOT / 'market-data' / 'cache'
FIELDS = 'date,code,open,high,low,close,volume,amount,tradestatus,isST'

def board(code):
    if len(code) != 9 or code[2] != '.' or not code[3:].isdigit(): return None
    if code.startswith('sh.60') or code.startswith('sz.00'): return 'mainboard'
    if code.startswith('sz.300') or code.startswith('sz.301'): return 'chinext'
    return None

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(path)

def rows(rs):
    if rs is None or rs.error_code != '0': raise RuntimeError('数据接口未返回成功状态')
    result = []
    while rs.next(): result.append(dict(zip(rs.fields, rs.get_row_data())))
    if rs.error_code != '0': raise RuntimeError('数据接口分页失败')
    return result

def connect():
    import baostock as bs
    import baostock.common.context as ctx
    old = getattr(ctx, 'default_socket', None)
    if old:
        try: old.close()
        except OSError: pass
    socket.setdefaulttimeout(20)
    if bs.login().error_code != '0': raise RuntimeError('BaoStock连接失败')

def history(item):
    code, asof, start = item
    path = CACHE / 'histories' / f'{code}-{asof}.json'
    if path.exists():
        cached = json.loads(path.read_text(encoding='utf-8'))
        if cached.get('adjustFlag') == '2' and cached.get('start') == start: return code, cached['rows'], None
    import baostock as bs
    for attempt in range(2):
        try:
            if attempt: connect()
            time.sleep(.1)
            data = rows(bs.query_history_k_data_plus(code, FIELDS, start_date=start, end_date=asof, frequency='d', adjustflag='2'))
            if not data: raise RuntimeError('历史行情为空')
            if any(r['date'] > asof or r['code'] != code for r in data): raise RuntimeError('行情日期或代码不符')
            write_json(path, {'source':'BaoStock','asOf':asof,'start':start,'adjustFlag':'2','fetchedAt':datetime.now(timezone.utc).isoformat(),'rows':data})
            return code, data, None
        except Exception as error:
            if attempt: return code, [], type(error).__name__ + ': ' + str(error)[:120]

def build_snapshot(universe, histories, errors, calendar, asof, fetched):
    items, stocks, source = [], [], 'BaoStock日线（前复权）'
    for stock in universe:
        code, name = stock['code'], stock['code_name']
        data = histories.get(code, [])
        latest = data[-1] if data else None
        status = '数据缺失' if not latest else '日期落后' if latest['date'] != asof else '已覆盖'
        instrument = None
        bars = []
        try:
            valid = [r for r in data if r['tradestatus'] == '1' and Decimal(r['close']) > 0 and Decimal(r['low']) > 0]
            bars = [Bar.create(date.fromisoformat(r['date']),r['high'],r['low'],r['close'],r['volume'],r['amount']) for r in valid]
            # Earliest fetched trading bar is a verified lower bound on listing age.
            age = sum(d >= data[0]['date'] and d <= asof for d in calendar) if data else 0
            instrument = Instrument(code,name,board(code),age,latest['isST']=='1', '退' in name,
                stock['tradeStatus'] != '1' or latest['tradestatus'] != '1', True, source) if latest else None
            if instrument: items.append((instrument,bars))
        except (ValueError, KeyError, TypeError, ArithmeticError): status = '数据异常'
        stocks.append({'code':code,'name':name,'board':board(code),'status':status,
            'close':float(latest['close']) if latest and latest.get('close') else None,
            'asOf':latest['date'] if latest else None,'st':latest['isST']=='1' if latest else None,
            'suspended':instrument.suspended if instrument else None,'historyCount':len(bars),
            'amount':float(latest['amount']) if latest and latest.get('amount') else None})
    candidates, assessments = select_candidates(items, as_of=date.fromisoformat(asof), daily_data_complete=True)
    cdata = []
    qualified_count=sum(a.candidate is not None for a in assessments.values())
    item_map={instrument.code:(instrument,bars) for instrument,bars in items}
    for rank,c in enumerate(candidates,1):
        data = histories[c.code]
        instrument,bars=item_map[c.code]
        cdata.append({'code':c.code,'name':c.name,'board':board(c.code),'kind':c.kind,'asOf':asof,
            'close':float(data[-1]['close']),'referenceHigh':float(c.reference_high),'return20':float(c.return_20),
            'averageAmount20':float(c.average_amount_20),'reasons':list(c.reasons),
            'history':[float(r['close']) for r in data[-24:]],'source':source,
            'explanation':explain_candidate(instrument,bars,as_of=date.fromisoformat(asof),
                rank=rank,qualified_count=qualified_count)})
    exclusions = Counter(a.reasons[0] for a in assessments.values() if a.candidate is None)
    selected_codes={c.code for c in candidates}
    for s in stocks:
        a = assessments.get(s['code'])
        s['assessment'] = (a.candidate.kind if s['code'] in selected_codes else '满足观察条件，排序未进入前10') if a and a.candidate else a.reasons[0] if a else s['status']
    return {'schema':1,'simulation':False,'source':'BaoStock','sourceUrl':'https://baostock.com/api/',
        'asOf':asof,'fetchedAt':fetched,'builtAt':datetime.now(timezone.utc).isoformat(),
        'scope':'沪深主板及创业板A股；排除科创板、北交所、B股、基金和指数',
        'priceBasis':'前复权日线；盘中实际价格需独立核验，不直接用作下单价',
        'expectedCount':len(universe),'coveredCount':sum(s['status']=='已覆盖' for s in stocks),
        'complete':all(s['status']=='已覆盖' for s in stocks),'qualifiedCount':qualified_count,'boardCounts':dict(Counter(s['board'] for s in stocks)),
        'excludedReasons':dict(exclusions),'errors':errors,'candidates':cdata,'stocks':stocks,
        'tradeDates':calendar[-30:],'nextTradeDay':next((d for d in calendar if d>asof),None),
        'selectionRules':rule_snapshot(),
        'signalStatus':'盘中提醒未启用，当前为真实日线观察名单','historicalBacktest':False}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--workers',type=int,default=2); parser.add_argument('--limit',type=int);args=parser.parse_args()
    if not 1 <= args.workers <= 2: raise ValueError('免费数据采集最多两个连接')
    CACHE.mkdir(parents=True,exist_ok=True)
    import baostock as bs
    connect()
    now = datetime.now(timezone(timedelta(hours=8)))
    calendar = rows(bs.query_trade_dates(start_date=(now.date()-timedelta(days=450)).isoformat(),end_date=(now.date()+timedelta(days=20)).isoformat()))
    days = [r['calendar_date'] for r in calendar if r['is_trading_day']=='1']
    # Wait for daily publication, never treat today's intraday bar as completed daily data.
    cutoff = now.date().isoformat() if now.hour >= 21 else (now.date()-timedelta(days=1)).isoformat()
    asof = max(d for d in days if d <= cutoff)
    listing = rows(bs.query_all_stock(day=asof))
    if len(listing) < 5000 or len({s['code'] for s in listing}) != len(listing): raise RuntimeError('证券列表异常，停止全市场筛选')
    universe = [s for s in listing if board(s['code'])]
    if len(universe)<3000: raise RuntimeError('目标股票池数量异常')
    write_json(CACHE/'universe.json',{'asOf':asof,'calendar':days,'rows':universe,'listingCount':len(listing)})
    bs.logout()
    fetched=datetime.now(timezone.utc).isoformat()
    start=(date.fromisoformat(asof)-timedelta(days=340)).isoformat()
    tasks=[(s['code'],asof,start) for s in universe]
    if args.limit: tasks=tasks[:args.limit]
    histories, errors = {}, {}
    print(f'开始采集：数据日 {asof}，目标 {len(universe)}只，沪深主板与创业板；本次 {len(tasks)}只',flush=True)
    with ProcessPoolExecutor(max_workers=args.workers,initializer=connect) as executor:
        futures=[executor.submit(history,t) for t in tasks]
        for n,future in enumerate(as_completed(futures),1):
            code,data,error=future.result()
            histories[code]=data
            if error: errors[code]=error
            if n%100==0 or n==len(tasks):
                print(f'进度 {n}/{len(tasks)}，接口失败 {len(errors)}',flush=True)
                write_json(CACHE/'progress.json',{'done':n,'total':len(tasks),'errors':len(errors),'asOf':asof})
    snapshot=build_snapshot(universe,histories,errors,days,asof,fetched)
    output=ROOT/'trading-ui'/'dist'/'market.json'
    write_json(output,snapshot)
    write_json(CACHE/'collection-report.json',{k:v for k,v in snapshot.items() if k not in ('stocks','candidates')})
    print(f'输出 {output}，覆盖 {snapshot["coveredCount"]}/{snapshot["expectedCount"]}，候选 {len(snapshot["candidates"])}，完整 {snapshot["complete"]}',flush=True)

if __name__=='__main__': main()
