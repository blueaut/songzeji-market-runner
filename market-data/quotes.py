"""Download actual unadjusted Sina quotations; never fill missing prices."""
import argparse
import hashlib
import json
import math
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'market-data' / 'cache' / 'quotes'
CODE = re.compile(r'^(sh\.60\d{4}|sz\.00\d{4}|sz\.30[01]\d{3})$')

def parse(raw, codes, now):
    wanted, seen, result, errors = set(codes), set(), [], {}
    for match in re.finditer(r'(?:var\s+)?hq_str_((?:sh|sz)\d{6})\s*=\s*"([^"\r\n]*)"\s*;', raw):
        symbol = match[1]
        code = symbol[:2] + '.' + symbol[2:]
        if code not in wanted or not CODE.fullmatch(code):
            continue
        if code in seen:
            raise ValueError('duplicate symbol in source response')
        seen.add(code)
        try:
            fields = match[2].split(',')
            if len(fields) < 33:
                raise ValueError('incomplete source fields')
            def number(index):
                if not re.fullmatch(r'\d+(?:\.\d+)?', fields[index]):
                    raise ValueError('invalid source number')
                value = float(fields[index])
                if not math.isfinite(value):
                    raise ValueError('non-finite source number')
                return value
            price, previous = number(3), number(2)
            high, low, volume, amount = number(4), number(5), number(8), number(9)
            quoted = datetime.strptime(fields[30]+' '+fields[31], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=8)))
            if quoted > now + timedelta(minutes=2):
                raise ValueError('source timestamp is in the future')
            if not 0 < price <= 1000000 or previous <= 0:
                raise ValueError('no valid latest price')
            if volume > 0 and (low <= 0 or high < low or price < low-.005 or price > high+.005):
                raise ValueError('price inconsistent with daily range')
            result.append(dict(code=code, price=price, previousClose=previous, high=high, low=low,
                               volume=volume, amount=amount, quotedAt=quoted.isoformat(),
                               source='新浪财经', sourceUrl='https://finance.sina.com.cn/',
                               priceBasis='不复权实际报价', hasTrades=volume>0))
        except (ValueError, IndexError) as error:
            errors[code] = str(error)
    for code in wanted-seen:
        errors[code] = 'source did not return symbol'
    return result, errors

def download_batch(codes):
    url = 'https://hq.sinajs.cn/list='+','.join(c.replace('.', '') for c in codes)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={'Referer':'https://finance.sina.com.cn/', 'User-Agent':'Mozilla/5.0'})
    for attempt in range(2):
        try:
            if attempt:
                time.sleep(2)
            with opener.open(request, timeout=15) as response:
                raw = response.read(500001)
            if len(raw) > 500000:
                raise ValueError('source response too large')
            now = datetime.now(timezone.utc)
            day = now.astimezone(timezone(timedelta(hours=8))).date().isoformat()
            folder = CACHE / day
            folder.mkdir(parents=True, exist_ok=True)
            identifier = hashlib.sha256(','.join(codes).encode()).hexdigest()[:16]
            (folder / (identifier+'.txt')).write_bytes(raw)
            result, errors = parse(raw.decode('gbk'), codes, now)
            time.sleep(.5)
            return result, errors
        except Exception as error:
            if attempt:
                return [], {code:type(error).__name__ for code in codes}

def collect(codes):
    if not codes or len(codes)!=len(set(codes)) or any(not CODE.fullmatch(c) for c in codes):
        raise ValueError('invalid quotation universe')
    batches = [codes[i:i+100] for i in range(0,len(codes),100)]
    result, errors = [], {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        for quotes, problems in executor.map(download_batch, batches):
            result.extend(quotes)
            errors.update(problems)
    if not result:
        raise RuntimeError('no valid quotations received; previous snapshot is retained')
    return dict(schema=1, simulation=False, source='新浪财经', priceBasis='不复权实际报价',
                fetchedAt=datetime.now(timezone.utc).isoformat(), expectedCount=len(codes),
                coveredCount=len(result), complete=len(result)==len(codes),
                quotes=sorted(result,key=lambda q:q['code']), errors=errors)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--market',type=Path,default=ROOT/'trading-ui'/'dist'/'market.json')
    parser.add_argument('--output',type=Path,default=ROOT/'trading-ui'/'dist'/'quotes.json')
    args=parser.parse_args()
    market=json.loads(args.market.read_text(encoding='utf-8'))
    snapshot=collect([s['code'] for s in market['stocks']])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    tmp=args.output.with_suffix('.tmp')
    tmp.write_text(json.dumps(snapshot,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    tmp.replace(args.output)
    print(f'Actual quotations: {snapshot["coveredCount"]}/{snapshot["expectedCount"]}; no invented replacements')

if __name__=='__main__':
    main()
