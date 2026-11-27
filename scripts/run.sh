#!/usr/bin/env bash
# GitHub Actions가 실행하는 본체 — 설치 → 수집 → 저장소에 올리기
# 모든 출력은 data/latest/run_log.txt 에도 남김(Claude가 실행 기록 화면 없이도 원인을 볼 수 있게)
set -u
mkdir -p data/latest
LOG=data/latest/run_log.txt
{
  echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ) · commit $(git rev-parse --short HEAD) · python $(python --version 2>&1)"
  echo "--- pip install"
  pip install --quiet --disable-pip-version-check requests pandas lxml yfinance 2>&1 | tail -20
  echo "pip exit=${PIPESTATUS[0]}"
  echo "--- collect.py"
  python scripts/collect.py 2>&1 | tail -80
  echo "collect exit=${PIPESTATUS[0]}"
  echo "--- collect_long.py (1990~ 장기 일봉)"
  python scripts/collect_long.py 2>&1 | tail -20
  echo "collect_long exit=${PIPESTATUS[0]}"
  echo "--- collect_breadth.py (나스닥100 시장 폭, 2026-11-27 추가)"
  python scripts/collect_breadth.py 2>&1 | tail -20
  echo "collect_breadth exit=${PIPESTATUS[0]}"
} > /tmp/run_log.txt 2>&1
cat /tmp/run_log.txt

# 수집 결과를 임시로 옮겨 두고, 저장소 최신 상태 위에 다시 얹어서 올림 (실행 중 저장소가 바뀌어도 충돌 없음)
git config user.name "market-data-bot"
git config user.email "actions@users.noreply.github.com"
rm -rf /tmp/newdata && cp -r data /tmp/newdata
for i in 1 2 3; do
  git fetch -q origin main && git reset -q --hard origin/main
  rm -rf data && cp -r /tmp/newdata data
  cp /tmp/run_log.txt "$LOG"
  echo "--- push try $i $(date -u +%H:%M:%SZ)" >> "$LOG"
  # 올리기 직전 차단(2026-11-26): 비밀 키 값이 data/ 어디에든 있으면 가리고, 그래도 남으면 이번 회차는 올리지 않음
  if ! python scripts/secret_guard.py data; then echo "키 값 발견 — 올리기 중단"; exit 1; fi
  git add -A data
  if git diff --cached --quiet; then echo "바뀜 자료 없음"; exit 0; fi
  git commit -q -m "data $(date -u +%Y-%m-%dT%H:%MZ)"
  if git push -q origin HEAD:main; then echo "저장 완료"; exit 0; fi
  sleep 15
done
echo "저장 실패"; exit 1
