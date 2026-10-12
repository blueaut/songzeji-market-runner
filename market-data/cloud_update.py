"""Ordinary cloud job: download, validate, update, and read back. No AI SDK/API."""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from quotes import collect, CODE

ROOT=Path(__file__).resolve().parents[1]
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Site redirected; credentials were not forwarded')

def request_site(path, payload=None):
    origin=os.environ.get('SITE_URL','https://ashare-opportunity-lab.kindkite3.chatgpt.site').rstrip('/')
    if origin!='https://ashare-opportunity-lab.kindkite3.chatgpt.site':
        raise RuntimeError('unexpected Site origin; credentials were not sent')
    token=os.environ.get('SITE_SERVICE_TOKEN','')
    key=os.environ.get('SITE_SYNC_KEY','')
    if not token or not key:
        raise RuntimeError('configure GitHub repository secrets SITE_SERVICE_TOKEN and SITE_SYNC_KEY')
    headers={'OAI-Sites-Authorization':'Bearer '+token,'X-Sync-Key':key}
    body=None
    if payload is not None:
        headers['Content-Type']='application/json'
        body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
    req=urllib.request.Request(origin+path,data=body,headers=headers)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(req,timeout=60) as response:
        if response.headers.get('content-type','').split(';')[0]!='application/json':
            raise RuntimeError('Site service access not authenticated; received non-JSON response')
        return json.load(response)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--daily',action='store_true');args=parser.parse_args()
    payload={}
    if os.environ.get('GITHUB_ACTIONS')=='true':
        payload['runner']={'provider':'GitHub Actions','url':'https://github.com/'+os.environ['GITHUB_REPOSITORY']+'/actions/runs/'+os.environ['GITHUB_RUN_ID']}
    if args.daily:
        subprocess.run([sys.executable,'-X','utf8','-u',str(ROOT/'market-data'/'collect.py')],check=True)
        market=json.loads((ROOT/'trading-ui'/'dist'/'market.json').read_text(encoding='utf-8'))
        if not market['complete']:
            raise RuntimeError('daily coverage incomplete; no stale or invented replacement was uploaded')
        payload['market']=market
    else:
        market=request_site('/market.json')
    codes=[s['code'] for s in market['stocks']]
    if len(codes)<3000 or any(not CODE.fullmatch(code) for code in codes):
        raise RuntimeError('unexpected market universe')
    payload['quotes']=collect(codes)
    if payload['quotes']['coveredCount']<len(codes)*.9:
        raise RuntimeError('quotation coverage below 90%; retain previous result and fail the job')
    result=request_site('/api/update',payload)
    if not result.get('ok'):
        raise RuntimeError('Site update was not accepted')
    readback=request_site('/quotes.json')
    if readback['fetchedAt']!=payload['quotes']['fetchedAt'] or readback['quotes']!=payload['quotes']['quotes']:
        raise RuntimeError('readback differs from downloaded quotations')
    if args.daily and request_site('/market.json')['asOf']!=market['asOf']:
        raise RuntimeError('daily market readback failed')
    print('Verified cloud update: quotations '+str(len(readback['quotes']))+'; model/API calls: 0')

if __name__=='__main__':main()
