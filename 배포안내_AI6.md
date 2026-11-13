# AI6 서버 배포 안내

## 1. 접속
개인키 `nipa_ai6_deploy_ed25519`는 따로 전달합니다. `~/.ssh/`에 두고 `~/.ssh/config`에 추가하세요.
```
Host app
  HostName 192.168.200.115
  User bi
  IdentityFile ~/.ssh/nipa_ai6_deploy_ed25519
  IdentitiesOnly yes
```
확인: `ssh app 'hostname'` → `r7715`. 화면은 http://192.168.200.115:16850 입니다.

## 2. 브랜치
- AI6 작업: `feature/spec-test-ai6`
- **서버 배포: `feature/spec-test-ai-da`(통합 브랜치)에서만.** ai6 브랜치에서 바로 올리면 다른 항목 수정이 지워집니다.

## 3. 배포
```bash
# 통합 브랜치에 합치기
git checkout feature/spec-test-ai-da && git pull
git merge origin/feature/spec-test-ai6 && git push

# 실행 중인 시험이 없는지 확인 (running 이면 대기)
ssh app 'cat /opt/bi/nipa_ai_eval/run_status.json'

# 복사 → 재시작
scp ai6_*.py spec_runner_ai6.py spec_page.py spec_eval.py qa_app.py app:/opt/bi/nipa_ai_eval/
scp spec_data/*_ai6.* app:/opt/bi/nipa_ai_eval/spec_data/
ssh app 'systemctl --user restart nipa-qa'
```
배포 후 화면에서 AI6와 다른 항목이 정상인지 확인해 주세요. 시험은 한 번에 한 항목만 실행합니다.
