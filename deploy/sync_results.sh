#!/usr/bin/env bash
# PC ↔ APP 서버 실행 결과(spec/) 동기화 — Git Bash에서 실행:  bash deploy/sync_results.sh
# 1) 서버 → PC: 서버를 기준으로 덮어씀 (서버에서 실행한 결과·진행 중인 결과 포함)
# 2) PC → 서버: PC에만 있는 실행 폴더를 서버에 추가 (서버에 이미 있는 파일은 건드리지 않음)
set -e
cd "$(dirname "$0")/.."
ssh -o BatchMode=yes app 'cd /opt/bi/nipa_ai_eval && tar -czf - spec' | tar -xzf - -C .
tar -czf - spec | ssh -o BatchMode=yes app 'cd /opt/bi/nipa_ai_eval && tar -xzf - --skip-old-files'
echo "동기화 완료 $(date +%T): PC $(find spec -mindepth 2 -maxdepth 2 -type d | wc -l)개 / 서버 $(ssh app 'find /opt/bi/nipa_ai_eval/spec -mindepth 2 -maxdepth 2 -type d | wc -l')개 실행 폴더"
