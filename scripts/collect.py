"""시장 자료 자동 수집 (GitHub Actions에서 실행)
- 매일 정해진 시각에 공개 자료를 받아 data/ 아래에 저장하고 커밋한다.
- 한 자료가 실패해도 나머지는 계속 받는다. 결과는 data/latest/status.json 에 기록.
- 필요 비밀값: FRED_API_KEY (https://fredaccount.stlouisfed.org 에서 무료 발급)
"""
import os, io, json, math, datetime as dt, traceback
import requests, pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LATEST = os.path.join(ROOT, 'data', 'latest')
NOW = dt.datetime.now(dt.timezone.utc)
STAMP = NOW.strftime('%Y-%m-%d')
HIST = os.path.join(ROOT, 'data', 'history', STAMP)
os.makedirs(LATEST, exist_ok=True); os.makedirs(HIST, exist_ok=True)
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
      'Accept': 'application/json,text/csv,text/html;q=0.9,*/*;q=0.8'}
status = {'run_utc': NOW.isoformat(timespec='seconds'), 'ok': [], 'fail': {}}

def save(name, obj, kind='json'):
    for d in (LATEST, HIST):
        p = os.path.join(d, name)
        if kind == 'json':
            json.dump(obj, open(p, 'w'), ensure_ascii=False, indent=1, default=str)
        else:
            open(p, 'w', encoding='utf-8').write(obj)

def job(name):
    def deco(f):
        def run():
            try:
                f(); status['ok'].append(name)
            except Exception as e:
                status['fail'][name] = f'{type(e).__name__}: {e}'[:300]
                traceback.print_exc()
        run.__name__ = name
        return run
    return deco

def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=40, **kw); r.raise_for_status(); return r

# 1. 야후 일봉 (지수·VIX·ETF·원자재·환율) — 최근 2년
YSYM = ['^GSPC', '^NDX', '^IXIC', '^RUT', '^DJI', '^VIX', '^VIX3M', '^VIX1D', '^VXN', '^TNX', '^IRX', '^FVX',
        'QQQ', 'QQQM', 'SPY', 'SPYM', 'HYG', 'IEF', 'GC=F', 'HG=F', 'CL=F', 'BZ=F', 'DX-Y.NYB', 'KRW=X', 'JPY=X', 'BTC-USD']
@job('yahoo_daily')
def yahoo_daily():
    out = {}
    for s in YSYM:
        try:
            d = get(f'https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(s)}?range=2y&interval=1d').json()['chart']['result'][0]
            q = d['indicators']['quote'][0]; adj = d['indicators'].get('adjclose', [{}])[0].get('adjclose')
            rows = []
            for i, t in enumerate(d['timestamp']):
                if q['close'][i] is None: continue
                rows.append([dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%d'), q['close'][i], q['volume'][i], adj[i] if adj else None])
            out[s] = {'market_time': d['meta'].get('regularMarketTime'), 'rows': rows}
        except Exception as e:
            out[s] = {'error': str(e)[:200]}
    save('yahoo_daily.json', out)

# 2. CNN 공포탐욕 (종합 + 7개 세부, 과거값 포함)
@job('cnn_fear_greed')
def cnn():
    start = (NOW - dt.timedelta(days=400)).strftime('%Y-%m-%d')
    h = dict(UA, Referer='https://edition.cnn.com/', Origin='https://edition.cnn.com')
    d = requests.get(f'https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}', headers=h, timeout=40)
    d.raise_for_status(); save('cnn_fear_greed.json', d.json())

# 3. FRED (API 키 필요)
FRED = ['BAMLH0A0HYM2', 'BAMLH0A3HYC', 'BAMLH0A1HYBB', 'BAMLC0A0CM', 'BAA10Y', 'DGS2', 'DGS10', 'DTB3', 'STLFSI4',
        'WALCL', 'WRESBAL', 'WTREGEN', 'RRPONTSYD', 'RPONTSYD', 'M2SL', 'NFCI', 'SOFR', 'IORB', 'DFEDTARU']
@job('fred')
def fred():
    key = os.environ.get('FRED_API_KEY')
    if not key: raise RuntimeError('FRED_API_KEY 비밀값 없음')
    start = (NOW - dt.timedelta(days=800)).strftime('%Y-%m-%d'); out = {}
    for sid in FRED:
        try:
            j = get('https://api.stlouisfed.org/fred/series/observations',
                    params={'series_id': sid, 'api_key': key, 'file_type': 'json', 'observation_start': start}).json()
            out[sid] = [[o['date'], None if o['value'] == '.' else float(o['value'])] for o in j['observations']]
        except Exception as e:
            out[sid] = {'error': str(e)[:200]}
    save('fred.json', out)

# 4. 미 재무부 국채 수익률 (공식 종가, 이번 달·지난달)
@job('treasury_par_yield')
def treasury():
    frames = []
    for m in {NOW.strftime('%Y%m'), (NOW.replace(day=1) - dt.timedelta(days=1)).strftime('%Y%m')}:
        r = get('https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/all/'
                f'{m}?type=daily_treasury_yield_curve&field_tdr_date_value_month={m}&page&_format=csv')
        frames.append(pd.read_csv(io.StringIO(r.text)))
    df = pd.concat(frames).drop_duplicates().sort_values('Date')
    save('treasury_par_yield.csv', df.to_csv(index=False), 'text')

# 5. Cboe — VIX 선물 정산가(최근 5영업일) + 옵션 풋/콜 일일 통계
def recent_days(n=5):
    d, out = NOW.date(), []
    while len(out) < n:
        if d.weekday() < 5: out.append(d)
        d -= dt.timedelta(days=1)
    return out
@job('cboe_vx_settlement')
def cboe_vx():
    out = {}
    for d in recent_days():
        r = requests.get(f'https://www.cboe.com/us/futures/market_statistics/settlement/csv?dt={d}', headers=UA, timeout=40)
        if r.ok and 'VX' in r.text:
            df = pd.read_csv(io.StringIO(r.text)); out[str(d)] = df[df.iloc[:, 0].astype(str).str.strip() == 'VX'].values.tolist()
    if not out: raise RuntimeError('정산가 없음')
    save('cboe_vx_settlement.json', out)
@job('cboe_putcall')
def cboe_pc():
    out = {}
    for d in recent_days():
        r = requests.get(f'https://www.cboe.com/us/options/market_statistics/daily/?dt={d}', headers=UA, timeout=40)
        if not r.ok: continue
        try:
            tabs = pd.read_html(io.StringIO(r.text))
            out[str(d)] = [t.astype(str).values.tolist() for t in tabs[:6]]
        except Exception:
            out[str(d)] = 'no table'
    save('cboe_putcall.json', out)

# 6. 뉴욕연은 역레포
@job('nyfed_rrp')
def rrp():
    s = (NOW - dt.timedelta(days=40)).strftime('%Y-%m-%d'); e = NOW.strftime('%Y-%m-%d')
    j = get(f'https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json?startDate={s}&endDate={e}').json()
    ops = [[o.get('operationDate'), o.get('totalAmtAccepted')] for o in j.get('repo', {}).get('operations', [])]
    save('nyfed_rrp.json', ops)

# 7. QQQ·SPY 옵션 목록 → 풋/콜 비율, 감마 노출(GEX), 감마 플립, 풋 월·콜 월 (자체 계산)
def bs_gamma(S, K, T, iv):
    if T <= 0 or iv <= 0 or S <= 0: return 0.0
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * iv * math.sqrt(T))
def chain_summary(sym, n_exp=8):
    import yfinance as yf
    tk = yf.Ticker(sym)
    h = tk.history(period='5d'); spot = float(h['Close'].iloc[-1]); spot_date = str(h.index[-1].date())
    rows = []
    for e in tk.options[:n_exp]:
        ch = tk.option_chain(e)
        T = max((pd.Timestamp(e) + pd.Timedelta(hours=16) - pd.Timestamp.now('UTC').tz_localize(None)).total_seconds() / 3.156e7, 1 / 365)
        for typ, df in (('C', ch.calls), ('P', ch.puts)):
            for _, r in df.iterrows():
                rows.append([e, typ, float(r['strike']), float(r.get('volume') or 0 if not pd.isna(r.get('volume')) else 0),
                             float(r.get('openInterest') or 0 if not pd.isna(r.get('openInterest')) else 0), float(r.get('impliedVolatility') or 0), T])
    df = pd.DataFrame(rows, columns=['exp', 'type', 'strike', 'vol', 'oi', 'iv', 'T'])
    pc_vol = df[df.type == 'P'].vol.sum() / max(df[df.type == 'C'].vol.sum(), 1)
    pc_oi = df[df.type == 'P'].oi.sum() / max(df[df.type == 'C'].oi.sum(), 1)
    def gex_at(S):   # 딜러가 콜 매수자·풋 매수자의 반대편이라는 통상 가정: 콜 +, 풋 −  (1% 움직임당 달러)
        g = df.apply(lambda r: bs_gamma(S, r.strike, r['T'], r.iv) * r.oi * 100 * S * S * 0.01 * (1 if r.type == 'C' else -1), axis=1)
        return float(g.sum())
    grid = [spot * (0.85 + 0.005 * i) for i in range(61)]
    curve = [[round(s, 2), gex_at(s)] for s in grid]
    flip = None
    for (s1, g1), (s2, g2) in zip(curve, curve[1:]):
        if g1 < 0 <= g2 or g1 > 0 >= g2:
            flip = round(s1 + (s2 - s1) * (0 - g1) / (g2 - g1), 2)
            if abs(flip - spot) < spot * 0.15: break
    by_k = df.assign(g=df.apply(lambda r: bs_gamma(spot, r.strike, r['T'], r.iv) * r.oi * 100 * spot * spot * 0.01, axis=1))
    cg = by_k[(by_k.type == 'C') & (by_k.strike >= spot)].groupby('strike').g.sum()      # 콜 월: 현재가 위 콜 감마 최대 행사가
    pg = by_k[(by_k.type == 'P') & (by_k.strike <= spot)].groupby('strike').g.sum()      # 풋 월: 현재가 아래 풋 감마 최대 행사가
    call_wall = float(cg.idxmax()) if len(cg) else None; put_wall = float(pg.idxmax()) if len(pg) else None
    co = df[(df.type == 'C') & (df.strike >= spot)].groupby('strike').oi.sum(); po = df[(df.type == 'P') & (df.strike <= spot)].groupby('strike').oi.sum()
    oi_call_max = float(co.idxmax()) if len(co) else None; oi_put_max = float(po.idxmax()) if len(po) else None
    return {'symbol': sym, 'spot': spot, 'expirations_used': sorted(df.exp.unique().tolist()),
            'put_call_volume': round(pc_vol, 3), 'put_call_oi': round(pc_oi, 3),
            'call_volume': df[df.type == 'C'].vol.sum(), 'put_volume': df[df.type == 'P'].vol.sum(),
            'call_oi': df[df.type == 'C'].oi.sum(), 'put_oi': df[df.type == 'P'].oi.sum(),
            'net_gex_per_1pct_usd': gex_at(spot), 'gamma_flip': flip, 'call_wall': call_wall, 'put_wall': put_wall,
            'max_call_oi_strike_above': oi_call_max, 'max_put_oi_strike_below': oi_put_max, 'spot_date': spot_date,
            'method': '가까운 만기 8개, 야후 옵션 목록의 미결제약정·내재변동성, 블랙숄즈 감마, 딜러 콜 매도·풋 매수 가정'}
@job('options_qqq_spy')
def options():
    out = {}
    for s in ('QQQ', 'SPY'):
        try: out[s] = chain_summary(s)
        except Exception as e: out[s] = {'error': f'{type(e).__name__}: {e}'[:200]}
    if all('error' in v for v in out.values()): raise RuntimeError(json.dumps(out, ensure_ascii=False))
    save('options_summary.json', out)

if __name__ == '__main__':
    for f in (yahoo_daily, cnn, fred, treasury, cboe_vx, cboe_pc, rrp, options):
        f()
    save('status.json', status)
    print(json.dumps(status, ensure_ascii=False, indent=1))
