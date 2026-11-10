# QA 화면 서버 이관 (podman 컨테이너)

| 항목 | 값 |
|---|---|
| 서버 | APP 서버 `192.168.200.115` (Ubuntu 24.04, podman 4.9.3) |
| 배포 위치 | `/opt/bi/nipa_ai_eval` (BI 배정) |
| 포트 | **16850** (BI 배정 16000~16999 중 빈 포트, 2026-11-09 확인) |
| 접속 주소 | **http://192.168.200.115:16850** |
| 이미지 | `localhost/nipa-qa:latest` = Python 3.12 + `deploy/requirements-lock.txt` 고정 버전 (PC에서 검증한 streamlit 1.66.1, pandas 3.1.0 등) |

이미지에는 Python과 패키지만 들어 있습니다. 프로그램, 시험 데이터(`spec_data/`), 실행 결과(`spec/`)는 서버 폴더 `/opt/bi/nipa_ai_eval`에 두고 컨테이너의 `/app`에 연결해서 씁니다. 파일을 바꿀 때는 서버 폴더에 덮어쓰고 컨테이너만 다시 시작하면 되고, 이미지를 다시 만들 필요는 없습니다.

## 1. PC에서 이관 파일 만들기

PowerShell에서 다음을 실행합니다. **실행 결과·로그·상태 파일까지 전부** 넣고, 서버에서 새로 만드는 `.venv`, `.git`, `__pycache__`만 뺍니다.

```powershell
cd D:tar --exclude=nipa_ai_eval/.venv --exclude=nipa_ai_eval/.git --exclude="*/__pycache__" `
    -czf nipa_ai_eval.tgz nipa_ai_eval
```

| 들어가는 것 | 내용 | git에 있음 |
|---|---|---|
| 프로그램·설정 | `*.py`, `.streamlit/`, `requirements.txt`, `deploy/` | ✅ |
| 시험 데이터 | `spec_data/` (AI1~AI7 시험셋·정답셋, 원본 엑셀, 선정내역, 조작적 정의서) | ✅ |
| 백업·문서 | `backup/`, 변경내역·아키텍처·추출 기준·보완 요청서 등 | ✅ |
| **실행 결과·로그** | `spec/` (실행 폴더별 응답 원문 `raw/`, 로그 `diagnose_log_*.jsonl`, `result.xlsx`, `progress.xlsx`, `summary.json`, `meta.json`, 데이터 사본 `data/`) | ❌ 압축 파일로만 |
| 실행 상태·기록 | `run_status.json`, `run.log`, `dashboard/`, `check_data/` | ❌ 압축 파일로만 |
| **기준 문서 원본** | `참고문서/` (규격서 v1.51, 시험의뢰서 v.15, 수행계획서 PDF) | ❌ 기관 문서라 GitHub에 올리지 않음 |

- 크기는 약 70MB입니다(결과 46MB + 수행계획서 PDF 20MB 등).
- 계정·비밀번호가 들어 있는 엑셀(설치정보, 개발 관리운영 사이트 현황)은 **넣지 않습니다.**

## 2. 서버로 복사하고 풀기

```bash
scp nipa_ai_eval.tgz <계정>@192.168.200.115:/tmp/
ssh <계정>@192.168.200.115
sudo mkdir -p /opt/bi && sudo chown <계정> /opt/bi      # 쓰기 권한이 없을 때만
tar -xzf /tmp/nipa_ai_eval.tgz -C /opt/bi
```

## 3. 이미지 만들기

**서버가 인터넷에 접속되는 경우:**
```bash
cd /opt/bi/nipa_ai_eval
podman build -t nipa-qa:latest -f deploy/Containerfile .
```

**서버가 인터넷에 접속되지 않는 경우:** 인터넷이 되는 PC에서 이미지를 만든 뒤 파일로 옮깁니다. 그 PC에 podman 또는 docker가 있어야 합니다.
```bash
podman build -t nipa-qa:latest -f deploy/Containerfile .
podman save -o nipa-qa.tar nipa-qa:latest
# nipa-qa.tar를 서버로 복사한 뒤 서버에서 실행
podman load -i nipa-qa.tar
```

## 4. 실행

먼저 직접 실행해서 잘 뜨는지 확인합니다.
```bash
podman run -d --name nipa-qa -p 16850:8501 \
  -v /opt/bi/nipa_ai_eval:/app \
  -e AIPS_BASE=http://192.168.200.115:17010/api \
  -e AIPS_REF_BASE=http://192.168.200.29:8777 \
  localhost/nipa-qa:latest
podman logs -f nipa-qa        # "URL: http://0.0.0.0:8501" 이 보이면 정상
```

브라우저에서 **http://192.168.200.115:16850** 으로 접속합니다.

| 작업 | 명령 |
|---|---|
| 중지 | `podman stop nipa-qa` |
| 다시 시작 (파일 교체 후) | `podman restart nipa-qa` |
| 삭제 (다시 만들 때) | `podman rm -f nipa-qa` |

## 5. 상시 실행 (재부팅·로그아웃 후에도 유지)

```bash
podman rm -f nipa-qa                                  # 4장에서 직접 띄운 컨테이너 정리
mkdir -p ~/.config/containers/systemd
cp /opt/bi/nipa_ai_eval/deploy/nipa-qa.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start nipa-qa
systemctl --user status nipa-qa
loginctl enable-linger $USER                          # 로그아웃해도 계속 실행
```

## 6. 설정값 (환경변수)

| 환경변수 | 기본값 | 서버에서 |
|---|---|---|
| `AIPS_BASE` | `http://192.168.200.115:17010/api` | `http://192.168.200.115:17010/api` (같은 서버) |
| `AIPS_REF_BASE` | `http://192.168.200.29:8777` | 그대로. 개발자 PC라 서버에서 닿지 않으면 AI1 참조 열만 비고, 시험에는 지장 없음 |

PC에서 쓰던 `.streamlit/config.toml`의 `address = "192.168.0.4"`는 컨테이너에서 쓰지 않습니다. Containerfile의 실행 명령이 `0.0.0.0:8501`로 덮어씁니다.

## 7. 이관 후 확인

- [ ] 서버 안에서 접속 대상에 닿는지
  - `curl -s http://192.168.200.115:17010/` (AIPS)
  - `curl -s http://192.168.200.116:17801/api/stats` (법령 DB)
  - `curl -s http://192.168.200.115:18800/api/models` (모델명)
  - `curl -s http://192.168.200.128:8003/version` (vLLM)
- [ ] 화면의 "성과지표 현황"에 지난 결과(AI1 7회, AI2 1회)가 보이는지
- [ ] 지난 결과 폴더를 하나 열어 점수가 PC와 같은지. 실행 폴더의 `meta.json`에 Windows 경로(`spec_data\...`)가 남아 있지만, 채점은 실행 폴더 안의 `data/` 사본을 먼저 씁니다.
- [ ] 다른 PC에서 http://192.168.200.115:16850 이 열리는지. 안 열리면 서버 방화벽에서 16850을 엽니다(`sudo ufw allow 16850/tcp`).
- [ ] 규격서 접속 주소(`:8501`)를 `http://192.168.200.115:16850`으로 고치도록 의뢰서 작성자에게 전달했는지

## 주의

- 시험을 돌리는 중에 컨테이너를 다시 시작하면 실행이 끊깁니다. 끊긴 실행은 화면의 "이어서 실행"으로 남은 건부터 다시 하면 됩니다.
- 계정과 비밀번호는 이 문서에 적지 않습니다. 설치정보 원본을 참고하세요.
