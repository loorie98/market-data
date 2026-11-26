# 미국 증시(NYSE·나스닥) 공식 거래 일정 — 모든 스크립트가 함께 쓰는 단일 출처 (2026-11-26 1차 수정)
# 출처: NYSE Holidays & Trading Hours (nyse.com/trade/hours-calendars, 2026-11-26 확인)
# 해가 바뀌기 전(매년 11~12월) NYSE 공지로 다음 해를 추가할 것. 목록 밖 날짜를 물으면 경고를 출력한다.
import datetime as dt
from zoneinfo import ZoneInfo
ET = ZoneInfo('America/New_York')
VERSION = 'nyse-2026-2027 (2026-11-26 확인)'
COVER = (dt.date(2026, 1, 1), dt.date(2027, 12, 31))
HOLIDAYS = {
    '2026-01-01': '새해', '2026-01-19': '마틴 루서 킹 데이', '2026-02-16': '대통령의 날', '2026-04-03': '성금요일',
    '2026-05-25': '메모리얼 데이', '2026-06-19': '준틴스', '2026-07-03': '독립기념일(대체)', '2026-09-07': '노동절',
    '2026-11-26': '추수감사절', '2026-12-25': '크리스마스',
    '2027-01-01': '새해', '2027-01-18': '마틴 루서 킹 데이', '2027-02-15': '대통령의 날', '2027-03-26': '성금요일',
    '2027-05-31': '메모리얼 데이', '2027-06-18': '준틴스(대체)', '2027-07-05': '독립기념일(대체)', '2027-09-06': '노동절',
    '2027-11-25': '추수감사절', '2027-12-24': '크리스마스(대체)',
}
HALF_DAYS = {   # 13:00 ET 마감
    '2026-11-27': '추수감사절 다음 날', '2026-12-24': '크리스마스 이브', '2027-11-26': '추수감사절 다음 날',
}
def _d(x):
    if isinstance(x, dt.datetime): return x.date()
    if isinstance(x, dt.date): return x
    if hasattr(x, 'date') and callable(x.date): return x.date()      # pandas Timestamp
    return dt.date.fromisoformat(str(x)[:10])
def _check(d):
    if not (COVER[0] <= d <= COVER[1]): print(f'주의: 거래일 목록 범위({COVER[0]}~{COVER[1]}) 밖 날짜 {d} — 주말만 빼고 판단함. us_calendar.py에 그 해 휴장일을 추가할 것')
def is_trading(x):
    d = _d(x); _check(d)
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS
def is_half(x): return _d(x).isoformat() in HALF_DAYS
def next_td(x, n=1):
    d = _d(x); k = 0
    while k < n:
        d += dt.timedelta(days=1)
        if is_trading(d): k += 1
    return d
def prev_td(x, n=1):
    d = _d(x); k = 0
    while k < n:
        d -= dt.timedelta(days=1)
        if is_trading(d): k += 1
    return d
def nth_td(x, n): return next_td(x, n)            # x 다음 거래일부터 세어 n번째(5 = 1주, 21 = 1개월, 63 = 3개월)
def open_et(x): d = _d(x); return dt.datetime.combine(d, dt.time(9, 30), ET)
def close_et(x): d = _d(x); return dt.datetime.combine(d, dt.time(13, 0) if is_half(d) else dt.time(16, 0), ET)
def trading_days(a, b):
    d, out = _d(a), []
    while d <= _d(b):
        if is_trading(d): out.append(d)
        d += dt.timedelta(days=1)
    return out
def session_of(ts):
    """시각 ts(타임존 포함)의 점수·값이 어느 거래일 장에 해당하는지: 그날 개장(09:30 ET) 뒤면 그날, 아니면 직전 거래일"""
    t = ts.astimezone(ET); d = t.date()
    if is_trading(d) and t.time() >= dt.time(9, 30): return d
    return prev_td(d)
def load():
    return __import__(__name__)
