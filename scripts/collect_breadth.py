"""나스닥100 시장 폭(breadth) 수집·계산 — 2026-11-27 원장님 지시로 추가
- 구성 종목 이력: jmccarrell/n100tickers (MIT 라이선스)의 연도별 YAML을 실행 때마다 받아 씀(2003~)
  + data/ref/n100_changes_extra.json 에 직접 적은 변경(원본이 아직 반영 안 한 것)을 덧붙임
- 주가: 야후 차트 API 일봉(분할 반영 종가·거래량)
- 결과: data/series/n100_breadth.json (날짜별 1줄, 작음) — 원본 주가는 저장소에 남기지 않음
- 하루 두 번 실행: 평소엔 최근 2년만 받아 마지막 ~1년을 다시 계산해 덮어씀.
  data/series/n100_breadth.json 이 없거나 REBUILD=1 이면 2003년부터 전체 재계산(약 320종목, 3~4분)
- 실패해도 다른 수집에는 영향 없음

열(cols) 뜻
  n      그날 구성 종목 수(원본 기준)
  priced 그중 주가가 있는 종목 수(커버리지). priced/n 이 0.9 아래면 그날 값은 참고만
  a20/a50/a200  20·50·200일선 위 종목 비율(%) — 이동평균 계산이 가능한 종목 중
  adv/dec/unch  전일 대비 오른·내린·그대로 종목 수
  upv    상승 종목 거래량 / (상승+하락 종목 거래량) (%) — 90% 날 판정용
"""
import os, json, re, time, datetime as dt, requests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERIES = os.path.join(ROOT, 'data', 'series'); os.makedirs(SERIES, exist_ok=True)
REF = os.path.join(ROOT, 'data', 'ref'); os.makedirs(REF, exist_ok=True)
OUT = os.path.join(SERIES, 'n100_breadth.json')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
YAML_URL = 'https://raw.githubusercontent.com/jmccarrell/n100tickers/main/src/nasdaq_100_ticker_history/n100-ticker-changes-{y}.yaml'
FIRST_YEAR = 2003
# 이름이 바뀐 회사: 원본 기호 → 야후에서 과거까지 이어진 기호 (같은 회사만)
ALIAS = {'FB': 'META', 'PCLN': 'BKNG', 'KFT': 'MDLZ', 'JDSU': 'VIAV', 'RIMM': 'BB', 'NLOK': 'GEN', 'SYMC': 'GEN',
         'CTRP': 'TCOM', 'HANS': 'MNST', 'WLTW': 'WTW'}
COLS = ['date', 'n', 'priced', 'a20', 'a50', 'a200', 'adv', 'dec', 'unch', 'upv']


# ---------- 구성 종목 이력 ----------
def parse_yaml(txt):
    """원본 YAML 형식(year / tickers_on_Jan_1 / changes)만 읽는 작은 해석기"""
    jan1, changes, sec, cur_date, cur_op = [], {}, None, None, None
    for raw in txt.splitlines():
        line = raw.split('#', 1)[0].rstrip()
        if not line.strip() or line.strip() == '---':
            continue
        ind = len(line) - len(line.lstrip()); s = line.strip()
        if ind == 0:
            sec = s.rstrip(':').split(':')[0]; cur_date = cur_op = None; continue
        if sec == 'tickers_on_Jan_1' and s.startswith('- '):
            jan1.append(s[2:].strip().strip('"\'')); continue
        if sec == 'changes':
            m = re.match(r'^["\']?(\d{4}-\d{2}-\d{2})["\']?:$', s)
            if m:
                cur_date = m.group(1); changes.setdefault(cur_date, {}); cur_op = None; continue
            if s in ('union:', 'difference:'):
                cur_op = s[:-1]; changes[cur_date].setdefault(cur_op, []); continue
            if s.startswith('- ') and cur_date and cur_op:
                changes[cur_date][cur_op].append(s[2:].strip().strip('"\''))
    return jan1, changes


def load_membership(last_year):
    years = {}
    for y in range(FIRST_YEAR, last_year + 1):
        r = requests.get(YAML_URL.format(y=y), headers=UA, timeout=30)
        if r.status_code != 200:
            if y == last_year:   # 새해 첫날 원본이 아직 없을 수 있음 → 전년도 끝 상태를 이어 씀
                break
            raise RuntimeError(f'구성 종목 {y}년 파일 없음 ({r.status_code})')
        years[y] = parse_yaml(r.text)
    extra_p = os.path.join(REF, 'n100_changes_extra.json')
    extra = json.load(open(extra_p)) if os.path.exists(extra_p) else {}
    return years, extra


def members_on(date_s, years, extra):
    y = int(date_s[:4])
    while y not in years and y > FIRST_YEAR:   # 그해 파일이 없으면 가장 최근 해의 연말 상태
        y -= 1; date_s = f'{y}-12-31'
    jan1, ch = years[y]
    cur = set(jan1)
    allch = dict(ch)
    for d, ops in extra.items():
        if d.startswith(str(y)):
            allch.setdefault(d, {})
            for op, t in ops.items():
                allch[d][op] = list(set(allch[d].get(op, [])) | set(t))
    for d in sorted(allch):
        if d <= date_s:
            cur |= set(allch[d].get('union', [])); cur -= set(allch[d].get('difference', []))
    return cur


# ---------- 주가 ----------
def fetch(sym, p1):
    ys = ALIAS.get(sym, sym)
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(ys)}?period1={p1}&period2={int(time.time())}&interval=1d'
    for k in range(3):
        try:
            j = requests.get(url, headers=UA, timeout=40).json()
            res = j['chart']['result']
            if not res:
                return None
            d = res[0]; q = d['indicators']['quote'][0]
            out = {}
            for i, t in enumerate(d.get('timestamp') or []):
                c, v = q['close'][i], q['volume'][i]
                if c is None: continue
                out[dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%d')] = (c, v or 0)
            return out
        except Exception:
            time.sleep(2 + 3 * k)
    return None


def main():
    today = dt.date.today()
    rebuild = os.environ.get('REBUILD') == '1' or not os.path.exists(OUT)
    years, extra = load_membership(today.year)
    p1 = int(dt.datetime(2002, 1, 1).timestamp()) if rebuild else int((dt.datetime.now() - dt.timedelta(days=730)).timestamp())

    # 지수 거래일 달력(^NDX)
    cal = fetch('^NDX', p1)
    days = sorted(d for d in cal if d >= (f'{FIRST_YEAR}-01-01' if rebuild else '0'))
    # 필요한 종목 = 계산 구간 동안 한 번이라도 구성 종목이었던 것
    calc_from = days[0] if rebuild else days[max(0, len(days) - 260)]
    mem = {d: members_on(d, years, extra) for d in days if d >= calc_from}
    need = sorted(set().union(*mem.values()))

    # 편입 기간(연속 구간)마다 따로 확인: 그 구간 거래일의 80% 이상에 주가가 있어야 그 구간을 씀
    # (같은 기호를 나중에 다른 종목이 쓰는 경우 — 예: 옛 SanDisk와 2025년 새 SNDK — 를 구간별로 가름)
    mdays = sorted(mem)
    px, miss, partial = {}, {}, {}
    for s in need:
        r = fetch(s, p1)
        if not r:
            miss[s] = 'no data'; continue
        eps, cur = [], []
        for d in mdays:
            if s in mem[d]: cur.append(d)
            elif cur: eps.append(cur); cur = []
        if cur: eps.append(cur)
        ok = set()
        for e in eps:
            if sum(1 for d in e if d in r) / len(e) >= 0.8: ok.update(e)
        if not ok:
            miss[s] = 'no data in member period'; continue
        if len(ok) < sum(len(e) for e in eps): partial[s] = f'{len(ok)}/{sum(len(e) for e in eps)}일'
        # 이동평균 계산용으로 편입 전 주가도 남기되, 집계는 ok 날짜만
        px[s] = (r, ok)
        time.sleep(0.15)

    # 이동평균용 종가 배열
    idx = {d: i for i, d in enumerate(days)}
    arr = {}
    for s, (r, okset) in px.items():
        c = [r.get(d, (None, 0))[0] for d in days]; v = [r.get(d, (None, 0))[1] for d in days]
        arr[s] = (c, v, okset)

    def ma_above(c, i, w):
        win = [x for x in c[i - w + 1:i + 1] if x is not None] if i >= w - 1 else []
        if len(win) < w * 0.9 or c[i] is None: return None
        return c[i] > sum(win) / len(win)

    rows = []
    for d in days:
        if d < calc_from: continue
        i = idx[d]; m = mem[d]
        pr = [s for s in m if s in arr and d in arr[s][2] and arr[s][0][i] is not None]
        ab = {20: [], 50: [], 200: []}; adv = dec = unch = 0; uv = dv = 0
        for s in pr:
            c, v, _ = arr[s]
            for w in ab:
                a = ma_above(c, i, w)
                if a is not None: ab[w].append(a)
            if i > 0 and c[i - 1] is not None:
                if c[i] > c[i - 1]: adv += 1; uv += v[i]
                elif c[i] < c[i - 1]: dec += 1; dv += v[i]
                else: unch += 1
        pct = lambda L: round(100 * sum(L) / len(L), 1) if L else None
        rows.append([d, len(m), len(pr), pct(ab[20]), pct(ab[50]), pct(ab[200]), adv, dec, unch,
                     round(100 * uv / (uv + dv), 1) if uv + dv > 0 else None])

    if not rebuild:   # 옛 값 위에 새로 계산한 구간만 덮어씀
        old = json.load(open(OUT))
        keep = [r for r in old['rows'] if r[0] < calc_from]
        rows = keep + rows
    meta = {'run_utc': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'rebuild': rebuild,
            'members_source': 'github.com/jmccarrell/n100tickers (MIT) + data/ref/n100_changes_extra.json',
            'members_years': [min(years), max(years)], 'tickers_needed': len(need), 'tickers_priced': len(px),
            'missing': miss, 'partial': partial, 'last': rows[-1] if rows else None,
            'note': '옛 종목 일부는 야후에 주가가 없어 빠짐 — priced/n(커버리지)이 낮은 해(대략 2017년 이전)는 참고만'}
    json.dump({'cols': COLS, 'rows': rows, '_meta': meta}, open(OUT, 'w'), separators=(',', ':'))
    print('n100_breadth', json.dumps({k: meta[k] for k in ('rebuild', 'tickers_needed', 'tickers_priced', 'last')}, ensure_ascii=False),
          'missing', len(miss))


if __name__ == '__main__':
    main()
