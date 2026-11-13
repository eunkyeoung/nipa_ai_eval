# AI6 서버 배포 안내

AI6(지도 제어) 담당 개발자가 QA 서버에 직접 배포하기 위한 안내입니다. (작성 2026-11-13)

- 서버 화면: http://192.168.200.115:16850
- 서버 앱 폴더: `/opt/bi/nipa_ai_eval` (컨테이너 `nipa-qa`의 `/app`과 같은 폴더)
- 현재 서버 반영본: 통합 브랜치 `feature/spec-test-ai-da` (AI6 `a19e566` 포함, 11-13 배포)

---

## 1. 서버 접속

배포용 SSH 개인키 `nipa_ai6_deploy_ed25519`는 **별도로 전달**합니다(이 문서·git에는 없음). 다른 사람과 공유하지 마세요.

1. 개인키를 `~/.ssh/`에 둡니다. (Windows: `C:\Users\<사용자>\.ssh\`, Linux·Mac은 `chmod 600 ~/.ssh/nipa_ai6_deploy_ed25519`)
2. `~/.ssh/config`에 추가합니다.

   ```
   Host app
     HostName 192.168.200.115
     User bi
     Port 22
     IdentityFile ~/.ssh/nipa_ai6_deploy_ed25519
     IdentitiesOnly yes
   ```

3. 확인: `ssh app 'hostname'` → `r7715`가 나오면 됩니다.

## 2. 브랜치

| 브랜치 | 용도 |
|---|---|
| `feature/spec-test-ai6` | AI6 작업 |
| `feature/spec-test-ai-da2` | AI1~AI5·DA2 작업 (예전 이름 `feature/spec-test-ai1`) |
| **`feature/spec-test-ai-da`** | **서버 배포용 통합 브랜치** (위 두 브랜치를 합친 것, AI7도 여기에 합침) |

> **서버에는 반드시 통합 브랜치에서 올립니다.** ai6 브랜치에서 바로 올리면 AI1~AI5·DA2 최근 수정이 서버에서 지워집니다.
> `master`와 다른 담당자 브랜치에는 직접 커밋하지 않습니다.

## 3. 배포 순서

### ① AI6 작업 (자기 브랜치)

```bash
git checkout feature/spec-test-ai6
# 수정 → 커밋
python -m unittest tests.test_ai6        # 통과 확인
git push origin feature/spec-test-ai6
```

### ② 통합 브랜치에 합치기

```bash
git fetch origin
git checkout feature/spec-test-ai-da
git pull origin feature/spec-test-ai-da
git merge origin/feature/spec-test-ai6
git merge origin/feature/spec-test-ai-da2    # 다른 항목 최신 수정도 함께 (새 커밋이 없으면 그냥 지나감)
python -m unittest tests.test_ai6
git push origin feature/spec-test-ai-da
```

- 충돌이 나면 **양쪽 내용을 모두 살립니다.** AI6 외 항목(AI1~AI5·DA2) 코드는 바꾸지 않습니다.
- 판단이 어려우면 AI1~AI5·DA2 담당(kimheunghan)에게 문의합니다.

### ③ 실행 중인 시험이 없는지 확인

```bash
ssh app 'cat /opt/bi/nipa_ai_eval/run_status.json'
```

`"state"`가 `running`이면 끝날 때까지 기다립니다. (재시작하면 실행이 끊김)

### ④ 백업 → 복사 → 재시작 (통합 브랜치 폴더에서)

```bash
# 백업
ssh app 'cd /opt/bi/nipa_ai_eval && B=backup/before_ai6_$(date +%Y%m%d_%H%M) && mkdir -p $B/spec_data && cp ai6_*.py spec_runner_ai6.py spec_page.py spec_eval.py qa_app.py $B/ && cp spec_data/*_ai6.* $B/spec_data/ && echo $B'

# 복사 (바뀐 파일만 올려도 됨)
scp ai6_client.py ai6_eval.py spec_runner_ai6.py spec_page.py spec_eval.py qa_app.py app:/opt/bi/nipa_ai_eval/
scp spec_data/testset_ai6.csv spec_data/answer_ai6.csv spec_data/intent_map_ai6.csv spec_data/freeze_ai6.json app:/opt/bi/nipa_ai_eval/spec_data/

# 재시작 — 200 이 나오면 정상
ssh app 'systemctl --user restart nipa-qa && sleep 10 && curl -s -o /dev/null -w "%{http_code}\n" http://localhost:16850/'
```

- AI6 코드는 표준 라이브러리만 써서 이미지 재빌드가 필요 없습니다. `requirements.txt`·`deploy/`를 바꾸면 재빌드가 필요하니 미리 알려 주세요.
- 시험셋·정답셋을 바꾸면 `python spec_runner_ai6.py --freeze`로 다시 동결해 `freeze_ai6.json`도 함께 올리고, **규격서에 적힌 해시도 고쳐야 합니다.**

### ⑤ 화면 확인

http://192.168.200.115:16850

- AI6 → 📂 시험 데이터: 해시가 `freeze_ai6.json`과 같은지, 50건(공간규제 15 / 도로 접근성 15 / 산지·농지 20)
- 사이드바에 **[사전 점검]**, **[AI 6 지도 제어 시험 실행]** 버튼
- **AI1~AI5·DA2 화면이 예전과 같은지**

### ⑥ 되돌리기

문제가 있으면 ④에서 만든 백업 폴더의 파일을 원래 자리로 복사하고 재시작합니다.

## 4. 시험 실행

- 시험은 **한 번에 한 항목만** 실행합니다. 다른 항목이 실행 중이면 기다립니다.
- 실행 전 **[사전 점검]**으로 공간분석 API(KGEOP)·LLM 상태를 확인합니다. (11-12 18:16부터 KGEOP 시간 초과 이력 있음)
- 결과는 서버 `spec/AI6/<시각>/`에 생깁니다. 끝나면 폴더 이름을 kimheunghan에게 알려 주세요(PC로 동기화).

## 5. AI7

통합 브랜치에서 `feature/spec-test-ai7`을 만들어 작업하고, 위와 같은 방식으로 통합 브랜치에 합쳐 배포합니다.

## 6. 규격서 v.14 대비 남은 일 (AI6)

- testset 해시: 규격서 `444ad389…` ↔ 현재 동결본 `fc15113a…` → 규격서 수정 필요
- 화면 캡처 저장: 규격서는 요구, 현재 구현 없음
- temperature = 0: 확인·기록하는 곳 없음
- 정답셋 검증자·검증일 칸 비어 있음
- 규격서 42쪽 AI6 절에 AI5 문구 3줄(환각 발생률·환각 유형) 섞임, '출처' 문장 중복, 시험방법 (3) 중복 → 작성자에게 전달
