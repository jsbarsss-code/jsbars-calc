#!/usr/bin/env bash
# 예비 경로: 이 PC에서 데이터를 만들어 data 브랜치로 올린다(GitHub Actions에서 공공데이터 API가 막히거나 Secret이 없을 때).
# 사용법(web-calc 폴더에서): bash scripts/push_data_local.sh
# 인증키는 블로그 폴더 config/public-data-api.json 에서 읽고, 저장소에는 데이터 JSON만 올라간다.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf site
PYTHONIOENCODING=utf-8 python -I scripts/build_data.py --out site/data --config ../config/public-data-api.json
cd site/data
git init -q -b data
git -c user.name=jsbarsss -c user.email=jsbarsss@gmail.com add -A
git -c user.name=jsbarsss -c user.email=jsbarsss@gmail.com commit -qm "data $(date +%F) (local)"
git push -qf https://github.com/jsbarsss-code/jsbars-calc.git data
cd ../..
gh workflow run deploy.yml -R jsbarsss-code/jsbars-calc
echo "data 브랜치 갱신 + 배포 실행 요청 완료"
