#!/usr/bin/env bash
# AIPS 사전진단 성능테스트 서버 배포 — 서버(bi 계정)에서 실행
#   bash /opt/bi/nipa_ai_eval/deploy/server_deploy.sh
# 통합 브랜치(feature/spec-test-ai-da)를 받아 화면을 재시작합니다.
# 시험 실행 중이면 멈춥니다. 되돌리기: 출력에 나오는 git reset 명령.
set -e
cd /opt/bi/nipa_ai_eval

state=$(python3 -c 'import json;print(json.load(open("run_status.json")).get("state",""))' 2>/dev/null || true)
if [ "$state" = "running" ]; then
  echo "시험 실행 중이라 배포하지 않습니다. 끝난 뒤 다시 실행하세요."; exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "서버에서 직접 바뀐 파일이 있어 배포하지 않습니다:"; git status --short --untracked-files=no; exit 1
fi

before=$(git rev-parse --short HEAD)
git fetch -q origin feature/spec-test-ai-da
git merge -q --ff-only origin/feature/spec-test-ai-da
after=$(git rev-parse --short HEAD)

if [ "$before" = "$after" ]; then
  echo "바뀐 것이 없습니다 ($after)."; exit 0
fi
echo "배포: $before → $after"
git log --oneline "$before..$after"
git diff --stat "$before" "$after" | tail -1

systemctl --user restart nipa-qa
sleep 10
code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:16850/ || true)
echo "화면 응답: $code (200 이면 정상)"
echo "되돌리기: cd /opt/bi/nipa_ai_eval && git reset --hard $before && systemctl --user restart nipa-qa"
