"""올리기 직전 차단 — data/ 아래 모든 파일에서 비밀 키 값을 찾아 가린다(2026-11-26).
가린 뒤 다시 검사해 하나라도 남으면 종료 코드 1 → run.sh가 이번 회차를 올리지 않음.
키 값 자체는 어디에도 출력하지 않는다."""
import os, sys
keys = [v for v in (os.environ.get('FRED_API_KEY'),) if v and len(v) >= 8]
root = sys.argv[1] if len(sys.argv) > 1 else 'data'
if not keys: print('secret_guard: 검사할 키 없음(환경변수 없음) — 통과'); sys.exit(0)
bk = [k.encode() for k in keys]
def scan(fix):
    hit = []
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f); b = open(p, 'rb').read()
            if any(k in b for k in bk):
                hit.append(p)
                if fix:
                    for k in bk: b = b.replace(k, b'***')
                    open(p, 'wb').write(b)
    return hit
h = scan(True)
if h: print(f'secret_guard: 키 값이 든 파일 {len(h)}개를 가림 — {h[:5]}')
left = scan(False)
if left: print(f'secret_guard: 가린 뒤에도 남음 {left[:5]} — 중단'); sys.exit(1)
print('secret_guard: 통과')
