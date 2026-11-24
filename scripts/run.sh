#!/usr/bin/env bash
# GitHub Actions가 실행하는 본체 — 설치 → 수집 → 저장소에 올리기
set -uo pipefail
pip install --quiet requests pandas lxml yfinance
python scripts/collect.py
git config user.name "market-data-bot"
git config user.email "actions@users.noreply.github.com"
git add data
git diff --cached --quiet && { echo "바뀐 자료 없음"; exit 0; }
git commit -q -m "data $(date -u +%Y-%m-%dT%H:%MZ)"
# 실행 중에 저장소가 바뀌었으면(스크립트 수정·다른 실행) 최신을 받아 이번 자료를 위에 얹고 올림. 최대 3번
for i in 1 2 3; do
  if git pull --rebase -X theirs origin main && git push; then exit 0; fi
  sleep 10
done
exit 1
