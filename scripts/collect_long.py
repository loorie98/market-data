"""장기 일봉 수집 (1990년~) — 일일 보고의 '차트로 본 바닥 신호'·'과거 비슷한 장과 비교'용
- 결과: data/latest/yahoo_long.json  {심볼: {'rows': [[날짜, 종가, 거래량, 수정종가], ...]}}
- 용량이 커서 날짜별 기록(data/history)에는 남기지 않음. 실패해도 다른 수집에는 영향 없음.
"""
import os, json, datetime as dt, requests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LATEST = os.path.join(ROOT, 'data', 'latest'); os.makedirs(LATEST, exist_ok=True)
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
SYM = ['^NDX', '^GSPC', '^VIX', '^VIX3M', '^TNX', '^IRX', 'SPY', 'QQQ', 'VWEHX']
P1 = 631152000   # 1990-01-01
out, fail = {}, {}
for s in SYM:
    try:
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(s)}?period1={P1}&period2={int(dt.datetime.now().timestamp())}&interval=1d'
        d = requests.get(url, headers=UA, timeout=60).json()['chart']['result'][0]
        q = d['indicators']['quote'][0]; adj = d['indicators'].get('adjclose', [{}])[0].get('adjclose')
        rows = []
        for i, t in enumerate(d['timestamp']):
            c = q['close'][i]
            if c is None: continue
            rows.append([dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%d'), round(c, 4), q['volume'][i],
                         round(adj[i], 4) if adj and adj[i] is not None else None])
        out[s] = {'rows': rows}
    except Exception as e:
        fail[s] = str(e)[:200]
out['_meta'] = {'run_utc': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'fail': fail,
                'n': {s: len(v['rows']) for s, v in out.items() if isinstance(v, dict) and 'rows' in v}}
json.dump(out, open(os.path.join(LATEST, 'yahoo_long.json'), 'w'), separators=(',', ':'))
print('yahoo_long', json.dumps(out['_meta'], ensure_ascii=False))
