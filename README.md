# market-data

매일 공개 시장 자료를 자동으로 모아 `data/latest/`(최신)와 `data/history/<날짜>/`(기록)에 저장합니다.
개인 정보·계좌 정보는 넣지 않습니다(공개 시장 자료만).

- 실행: GitHub Actions `collect-market-data` (한국 06:35, 20:35 · 평일) — Actions 탭에서 "Run workflow"로 바로 실행 가능
- 결과 점검: `data/latest/status.json` (ok = 받은 자료, fail = 실패와 이유)
- 비밀값: `FRED_API_KEY` (Settings → Secrets and variables → Actions)
