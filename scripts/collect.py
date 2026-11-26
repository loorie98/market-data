"""시장 자료 자동 수집 (GitHub Actions에서 실행)
- 매일 정해진 시각에 공개 자료를 받아 data/ 아래에 저장하고 커밋한다.
- 한 자료가 실패해도 나머지는 계속 받는다. 결과는 data/latest/status.json 에 기록.
- 필요 비밀값: FRED_API_KEY (https://fredaccount.stlouisfed.org 에서 무료 발급)
"""
import os, io, json, math, datetime as dt, traceback
import sys
import requests, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import us_calendar as CAL   # 미국 공식 거래 일정(scripts/us_calendar.py, 프로젝트 claude/us_calendar.py와 같은 내용)
# 2026-11-26 1차 오류 수정: 지표별 수집 결과(status.detail) · CNN 날짜를 거래일 기준으로 · 옵션 만기 시각 뉴욕 기준 · 당일 만기 최소 1일 제거 · 감마 플립은 현재가에 가장 가까운 교차점 · 딜러 가정 설명 정정

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LATEST = os.path.join(ROOT, 'data', 'latest')
NOW = dt.datetime.now(dt.timezone.utc)
STAMP = NOW.strftime('%Y-%m-%d')
HIST = os.path.join(ROOT, 'data', 'history', STAMP)
os.makedirs(LATEST, exist_ok=True); os.makedirs(HIST, exist_ok=True)
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
      'Accept': 'application/json,text/csv,text/html;q=0.9,*/*;q=0.8'}
import re
def _clean(e):   # 오류 문구에서 API 키를 지움(공개 저장소에 키가 남지 않게, 2026-11-26)
    return re.sub(r'(api_key=)[^&\s\'"]+', r'\1***', str(e))
status = {'run_utc': NOW.isoformat(timespec='seconds'), 'ok': [], 'fail': {}, 'detail': {}}   # detail = 자료 묶음 안의 지표별 성공·실패

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
                status['fail'][name] = _clean(f'{type(e).__name__}: {e}')[:300]
                print(_clean(traceback.format_exc()))
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
            status['detail'].setdefault('yahoo_daily', {})[s] = f'ok {rows[-1][0]}' if rows else 'empty'
        except Exception as e:
            out[s] = {'error': str(e)[:200]}; status['detail'].setdefault('yahoo_daily', {})[s] = 'fail ' + str(e)[:120]
    save('yahoo_daily.json', out)

# 2. CNN 공포탐욕 (종합 + 7개 세부, 과거값 포함)
@job('cnn_fear_greed')
def cnn():
    start = (NOW - dt.timedelta(days=400)).strftime('%Y-%m-%d')
    h = dict(UA, Referer='https://edition.cnn.com/', Origin='https://edition.cnn.com')
    d = requests.get(f'https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}', headers=h, timeout=40)
    d.raise_for_status(); j = d.json(); save('cnn_fear_greed.json', j)
    # 세부 7개 '점수'는 CNN이 그날 것만 주므로 매일 모아 둠 (data/series/cnn_sub_scores.json, 날짜 = 미국 기준일)
    p = os.path.join(ROOT, 'data', 'series', 'cnn_sub_scores.json'); os.makedirs(os.path.dirname(p), exist_ok=True)
    h = json.load(open(p)) if os.path.exists(p) else {}
    # CNN은 지표마다 새 자료가 들어올 때 바로 다시 계산함(정해진 주기 없음). 장 마감 뒤에도 정크본드(약 18:30 ET)·신고가/시장 폭(약 19:00 ET)이 늦게 들어옴
    # → 점수가 속한 거래일(session) = 그날 개장(09:30 ET) 뒤면 그날, 아니면(장 전·휴장일) 직전 거래일. 그 거래일 19:00 ET 이후 값이면 '확정', 그 전이면 '잠정'.
    #   같은 날짜는 더 늦은 시각 값으로만 덮어씀 (2026-11-26 수정: 예전엔 달력 날짜를 그대로 써서 장 전·휴장일 값이 엉뚱한 날짜에 붙을 수 있었음)
    ts = pd.Timestamp(j['fear_and_greed']['timestamp']).tz_convert('America/New_York')
    sess = CAL.session_of(ts.to_pydatetime())
    day = sess.isoformat(); final = ts.to_pydatetime() >= dt.datetime.combine(sess, dt.time(19, 0), CAL.ET)
    keys = ['market_momentum_sp500', 'stock_price_strength', 'stock_price_breadth', 'put_call_options', 'market_volatility_vix', 'safe_haven_demand', 'junk_bond_demand']
    rec = {'score': round(j['fear_and_greed']['score'], 2), **{k: round(j[k]['score'], 2) for k in keys if k in j},
           'ts_et': ts.strftime('%Y-%m-%d %H:%M'), 'final': final}
    if day not in h or h[day].get('ts_et', '') <= rec['ts_et']: h[day] = rec
    # 장 시작 전(미국 새벽)에 받으면 timestamp가 이미 다음 날일 수 있음 → CNN이 주는 '전일 마감'을 직전 거래일 확정 종합 점수로 기록
    pc = j['fear_and_greed'].get('previous_close')
    if pc is not None:
        pd_ = CAL.prev_td(sess).isoformat()        # '전일 마감' = 점수가 속한 거래일의 직전 거래일(휴장일 건너뜀)
        if pd_ in h: h[pd_]['close_official'] = round(pc, 2); h[pd_]['close_official_from'] = rec['ts_et']
    json.dump(dict(sorted(h.items())), open(p, 'w'), indent=0)

# 3. FRED (API 키 필요)
FRED = ['BAMLH0A0HYM2', 'BAMLH0A3HYC', 'BAMLH0A1HYBB', 'BAMLC0A0CM', 'BAA10Y', 'DGS2', 'DGS10', 'DTB3', 'STLFSI4',
        'WALCL', 'WRESBAL', 'WTREGEN', 'RRPONTSYD', 'RPONTSYD', 'M2SL', 'NFCI', 'SOFR', 'IORB', 'DFEDTARU',
        'SAHMREALTIME', 'ICSA', 'T10YIE', 'T5YIE']   # 경기 칸(2026-11-24 추가): 샴 룰(월간)·신규 실업수당(주간)·10년/5년 기대인플레(일간)
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
            last = next((r[0] for r in reversed(out[sid]) if r[1] is not None), None)
            status['detail'].setdefault('fred', {})[sid] = f'ok {last}' if last else 'empty'
        except Exception as e:
            out[sid] = {'error': _clean(e)[:200]}; status['detail'].setdefault('fred', {})[sid] = 'fail ' + _clean(e)[:120]
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
        exp_at = CAL.close_et(e) if CAL.is_trading(e) else dt.datetime.combine(dt.date.fromisoformat(e), dt.time(16, 0), CAL.ET)   # 만기 = 뉴욕 장 마감(반나절이면 13:00)
        T = (exp_at - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3.156e7
        if T <= 0: continue                                    # 이미 끝난 만기는 뺌
        T = max(T, 15 * 60 / 3.156e7)                          # 당일 만기: 실제 남은 시간 그대로(감마 폭주만 막으려 최소 15분). 예전엔 최소 1일
        for typ, df in (('C', ch.calls), ('P', ch.puts)):
            for _, r in df.iterrows():
                rows.append([e, typ, float(r['strike']), float(r.get('volume') or 0 if not pd.isna(r.get('volume')) else 0),
                             float(r.get('openInterest') or 0 if not pd.isna(r.get('openInterest')) else 0), float(r.get('impliedVolatility') or 0), T])
    df = pd.DataFrame(rows, columns=['exp', 'type', 'strike', 'vol', 'oi', 'iv', 'T'])
    pc_vol = df[df.type == 'P'].vol.sum() / max(df[df.type == 'C'].vol.sum(), 1)
    pc_oi = df[df.type == 'P'].oi.sum() / max(df[df.type == 'C'].oi.sum(), 1)
    def gex_at(S):   # 업계 통상 가정: 딜러는 콜을 사 들고(고객이 콜 매도) 풋을 팔아 둔 상태(고객이 풋 매수) → 콜 +, 풋 −  (1% 움직임당 달러, 추정치)
        g = df.apply(lambda r: bs_gamma(S, r.strike, r['T'], r.iv) * r.oi * 100 * S * S * 0.01 * (1 if r.type == 'C' else -1), axis=1)
        return float(g.sum())
    grid = [spot * (0.85 + 0.005 * i) for i in range(61)]
    curve = [[round(s, 2), gex_at(s)] for s in grid]
    flips = []                                              # 부호가 바뀌는 곳을 모두 찾고, 현재가에 가장 가까운 것을 감마 플립으로
    for (s1, g1), (s2, g2) in zip(curve, curve[1:]):
        if (g1 < 0 <= g2 or g1 > 0 >= g2) and g2 != g1:
            flips.append(round(s1 + (s2 - s1) * (0 - g1) / (g2 - g1), 2))
    flip = min(flips, key=lambda f: abs(f - spot)) if flips else None
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
            'net_gex_per_1pct_usd': gex_at(spot), 'gamma_flip': flip, 'gamma_flips_all': flips, 'call_wall': call_wall, 'put_wall': put_wall,
            'max_call_oi_strike_above': oi_call_max, 'max_put_oi_strike_below': oi_put_max, 'spot_date': spot_date,
            'method': '가까운 만기 8개(뉴욕 장 마감 기준 남은 시간), 야후 옵션 목록의 미결제약정·내재변동성, 블랙숄즈 감마, 딜러 콜 매수·풋 매도 가정(업계 통상)',
            'note': '추정 모델 — 공개 미결제약정에 딜러 포지션을 가정해 계산한 값이며 실제 딜러 포지션이 아님'}
@job('options_qqq_spy')
def options():
    # 야후는 미국 새벽(장 시작 전)에 미결제약정을 0으로 비워 둘 때가 있음 → 그때는 직전 정상값을 그대로 두고 'stale' 표시
    prev_p = os.path.join(LATEST, 'options_summary.json')
    prev = json.load(open(prev_p)) if os.path.exists(prev_p) else {}
    out = {}
    for s in ('QQQ', 'SPY'):
        try:
            r = chain_summary(s)
            if (r.get('call_oi', 0) + r.get('put_oi', 0)) == 0:
                old = dict(prev.get(s, {})); old['stale'] = f'{NOW:%Y-%m-%dT%H:%MZ} 미결제약정 0(야후 미갱신) — 직전 값 유지'
                out[s] = old
            else:
                r['as_of_utc'] = NOW.isoformat(timespec='minutes'); out[s] = r
        except Exception as e:
            out[s] = dict(prev.get(s, {}), error=f'{type(e).__name__}: {e}'[:200])
    if all('error' in v for v in out.values()): raise RuntimeError(json.dumps(out, ensure_ascii=False, default=str))
    status['detail']['options_qqq_spy'] = {k: ('fail ' + v['error'][:80]) if 'error' in v else ('stale' if v.get('stale') else f"ok {v.get('spot_date')}") for k, v in out.items()}
    save('options_summary.json', out)

# 8. 과거값 채우기(백필) — 하루 한 번씩 비어 있는 날짜를 조금씩 채움 (data/series/)
SER = os.path.join(ROOT, 'data', 'series'); os.makedirs(SER, exist_ok=True)
def bdays_back(n):
    d, out = NOW.date() - dt.timedelta(days=1), []
    while len(out) < n:
        if d.weekday() < 5: out.append(d)
        d -= dt.timedelta(days=1)
    return out
@job('backfill_cboe_putcall')
def backfill_pc():
    p = os.path.join(SER, 'cboe_putcall.json'); h = json.load(open(p)) if os.path.exists(p) else {}
    todo = [d for d in bdays_back(140) if str(d) not in h][:45]
    for d in todo:
        r = requests.get(f'https://www.cboe.com/us/options/market_statistics/daily/?dt={d}', headers=UA, timeout=40)
        if not r.ok: continue
        try:
            t = pd.read_html(io.StringIO(r.text))[0].astype(str).values.tolist()
            h[str(d)] = {row[0].replace(' PUT/CALL RATIO', ''): float(row[1]) for row in t if 'RATIO' in row[0] and row[1] not in ('nan', '')}
        except Exception:
            h[str(d)] = None          # 휴장일 등 표 없음
    json.dump(dict(sorted(h.items())), open(p, 'w'), ensure_ascii=False, indent=0)
def vx_expiries(start='2026-03-01', months=14):
    out, m = [], pd.Timestamp(start)
    for _ in range(months):
        nxt = (m + pd.offsets.MonthBegin(1))
        fri = pd.date_range(nxt, nxt + pd.Timedelta(days=27), freq='W-FRI')[2]   # 다음 달 셋째 금요일
        out.append((fri - pd.Timedelta(days=30)).date()); m = nxt
    return out
@job('backfill_vx_front')
def backfill_vx():
    rows = {}
    for e in vx_expiries():
        try:
            r = requests.get(f'https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_{e}.csv', headers=UA, timeout=40)
            if not r.ok: continue
            df = pd.read_csv(io.StringIO(r.text))
            for _, x in df.iterrows():
                d = str(x['Trade Date'])[:10]
                if d < str(e) and float(x['Settle']) > 0 and (d not in rows or rows[d][0] > str(e)):
                    rows[d] = [str(e), float(x['Settle'])]          # 그날 가장 가까운 만기(근월물) 정산가
        except Exception:
            continue
    if not rows: raise RuntimeError('VX 과거 정산가 못 받음')
    json.dump(dict(sorted(rows.items())), open(os.path.join(SER, 'vx_front.json'), 'w'), indent=0)

if __name__ == '__main__':
    for f in (yahoo_daily, cnn, fred, treasury, cboe_vx, cboe_pc, rrp, options, backfill_pc, backfill_vx):
        f()
    save('status.json', status)
    print(json.dumps(status, ensure_ascii=False, indent=1))
