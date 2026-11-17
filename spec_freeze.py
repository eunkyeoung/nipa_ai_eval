"""[AI 1]~[AI 5] 사전 점검·동결 (11-17 사용자: AI6·AI7과 같게 맞춤)

규격서 [AI 1]~[AI 5] 시험방법 (2): 'AI 사전진단 엔진 모델 버전, 룰셋 버전, 법령·조례 DB 버전이 사전조건의 동결 버전과 일치하는지 확인'.
사전조건: '시험 기간 중 엔진 모델 버전(vLLM 0.24.0), 룰셋 버전, 법령·조례 DB 버전을 고정하며, 변경 시 전체 테스트셋을 재수행'.

  - 사전 점검(precheck): AIPS 연결 · 모델(에이전트·MCP 콘솔) · vLLM · 법령 DB(법령 수집·적재 관리자) 조회 +
    확인 질의 1건으로 룰엔진 버전(engine_version)·시스템 빌드를 읽음 (AIPS가 질의 없이 버전을 알려 주는 주소가 없음)
  - 동결(freeze): 시험셋·정답셋(·조문 DB) SHA-256, AIPS 주소, 대기 상한, 반복 횟수, 위 버전을 spec_data/freeze_aiN.json 에 기록
  - 실행기(spec_runner.py)는 동결 기록과 다르면 실행하지 않음. 동결 기록이 없으면 이전처럼 실행(지난 공식 결과는 그대로)

운영자 명령:  python spec_runner.py AI1 --freeze --testset … --answer … [--lawdb …] [--base …] [--timeout 600]
"""
import json
import os
import re
import time

import qa_runner
import spec_eval as S

DATA = "spec_data"
SPEC_MODEL = "gpt-oss-120b"            # 규격서 시험환경 'LLM 추론 환경 (gpt-oss-120b)'
SPEC_VLLM = "0.24.0"                   # 규격서 사전조건 '엔진 모델 버전(vLLM 0.24.0)'
ITEMS = ("AI1", "AI2", "AI3", "AI4", "AI5")


def freeze_path(item, data_dir=DATA):
    return os.path.join(data_dir, f"freeze_{item.lower()}.json")


def read_freeze(item, data_dir=DATA):
    p = freeze_path(item, data_dir)
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def law_db_day(text):
    """법령 DB 조회값 '수집 기준 2026-10-18 (법령 …)' → '2026-10-18' (비교는 수집 기준일로)"""
    m = re.search(r"\d{4}-\d{2}-\d{2}", str(text or ""))
    return m.group(0) if m else ""


def probe(question, base=None, timeout=None):
    """확인 질의 1건 → {build, contract_version, 룰엔진버전, 소요초, 오류}. 실패하면 오류에 사유"""
    old = qa_runner.BASE, qa_runner.TIMEOUT
    qa_runner.BASE = (base or qa_runner.BASE).rstrip("/")
    qa_runner.TIMEOUT = timeout or qa_runner.TIMEOUT
    t0 = time.time()
    try:
        _sid, events = qa_runner.stream_query(question)
        start = next((d for e, d in events if e == "orc.start" and isinstance(d, dict)), {}) or {}
        ver = S.versions_in(json.dumps([{"event": e, "data": d} for e, d in events], ensure_ascii=False))
        eng = ", ".join(ver.get("룰엔진버전") or [])
        return dict(build=start.get("build") or "", contract_version=start.get("contract_version") or "",
                    룰엔진버전=eng, 소요초=round(time.time() - t0, 1),
                    오류="" if eng else "응답에 룰엔진 버전(engine_version)이 없음")
    except Exception as e:                                               # noqa: BLE001
        return dict(build="", contract_version="", 룰엔진버전="", 소요초=round(time.time() - t0, 1),
                    오류=f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        qa_runner.BASE, qa_runner.TIMEOUT = old


def current_versions(question=None, base=None, timeout=None):
    """지금 시스템 버전 — 모델·vLLM·법령 DB는 조회, 룰엔진·빌드는 확인 질의(question 이 있을 때만)"""
    model, model_src = S.current_model()
    law, law_src = S.current_law_db()
    infra = S.infra_versions()
    out = dict(모델명=model, 모델명출처=model_src, vLLM=(infra.get("LLM 서빙(vLLM)") or ["", ""])[0],
               법령DB=law, 법령DB출처=law_src)
    if question:
        out["확인질의"] = probe(question, base, timeout)
    return out


def freeze_record(item, testset, answer, lawdb, base, timeout, repeat, versions):
    pr = versions.get("확인질의") or {}
    return dict(item=item, testset_sha256=S.sha256(testset), answer_sha256=S.sha256(answer), lawdb_sha256=S.sha256(lawdb),
                base=str(base).rstrip("/"), timeout_s=int(timeout), repeat=int(repeat),
                모델명=versions.get("모델명", ""), vLLM=versions.get("vLLM", ""),
                법령DB=law_db_day(versions.get("법령DB")), 법령DB_조회값=versions.get("법령DB", ""),
                룰엔진버전=pr.get("룰엔진버전", ""), 시스템빌드=pr.get("build", ""),
                frozen_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                files=dict(testset=testset, answer=answer, lawdb=lawdb))


NAMES = {"testset_sha256": "시험셋", "answer_sha256": "정답셋", "lawdb_sha256": "조문 DB", "base": "AIPS 주소",
         "timeout_s": "질의 대기 상한", "repeat": "반복 횟수"}


def check_conditions(fz, testset, answer, lawdb, base, timeout, repeat):
    """동결 기록과 다른 시험 조건(파일·주소·대기·반복) 이름 목록. 동결 기록이 없으면 []"""
    if not fz:
        return []
    cur = dict(testset_sha256=S.sha256(testset), answer_sha256=S.sha256(answer), lawdb_sha256=S.sha256(lawdb),
               base=str(base).rstrip("/"), timeout_s=int(timeout), repeat=int(repeat))
    return [NAMES[k] for k in NAMES if k in fz and str(fz.get(k)) != str(cur.get(k))]


def check_versions(fz, versions):
    """동결 기록과 다른 버전 이름 목록 — 조회에 실패한 값은 비교하지 않음(확인 못 함은 사전 점검 표에 따로 표시)"""
    if not fz:
        return []
    bad = []
    if versions.get("모델명") and fz.get("모델명") and versions["모델명출처"].endswith("조회값") and versions["모델명"] != fz["모델명"]:
        bad.append("AI 사전진단 엔진 모델")
    if versions.get("vLLM") and fz.get("vLLM") and versions["vLLM"] != fz["vLLM"]:
        bad.append("추론 서버(vLLM)")
    if law_db_day(versions.get("법령DB")) and fz.get("법령DB") and law_db_day(versions["법령DB"]) != fz["법령DB"]:
        bad.append("법령·조례 DB")
    eng = (versions.get("확인질의") or {}).get("룰엔진버전")
    if eng and fz.get("룰엔진버전") and eng != fz["룰엔진버전"]:
        bad.append("룰셋(룰엔진)")
    return bad


def version_rows(fz, versions):
    """'동결 버전 확인' 표 — [{항목, 시험 조건, 확인값, 확인 방법, 결과}] (AI6·AI7 표와 같은 열)"""
    pr = versions.get("확인질의") or {}
    rows = []

    def add(name, want, got, how, ok):
        rows.append({"항목": name, "시험 조건": want or "-", "확인값": got or "-", "확인 방법": how,
                     "결과": "확인 못 함" if ok is None else ("일치" if ok is True else "불일치" if ok is False else ok)})
    m = versions.get("모델명") if str(versions.get("모델명출처", "")).endswith("조회값") else ""
    add("AI 사전진단 엔진 모델", fz.get("모델명") or SPEC_MODEL, m, "에이전트·MCP 콘솔(115:18800) 조회",
        (m == (fz.get("모델명") or SPEC_MODEL)) if m else None)
    v = versions.get("vLLM")
    add("추론 서버 (vLLM)", fz.get("vLLM") or SPEC_VLLM, v, "LLM 서버 /version 조회", (v == (fz.get("vLLM") or SPEC_VLLM)) if v else None)
    e = pr.get("룰엔진버전")
    add("룰셋(룰엔진) 버전", fz.get("룰엔진버전") or "동결 기록 없음", e,
        "확인 질의 1건의 응답(engine_version) — 룰셋 자체 버전 값은 시스템 미제공", (e == fz["룰엔진버전"]) if e and fz.get("룰엔진버전") else None)
    d = law_db_day(versions.get("법령DB"))
    add("법령·조례 DB 버전", fz.get("법령DB_조회값") or fz.get("법령DB") or "동결 기록 없음", versions.get("법령DB"),
        "법령 수집·적재 관리자(116:17801) 조회 — 수집 기준일로 비교", (d == fz["법령DB"]) if d and fz.get("법령DB") else None)
    b = pr.get("build")
    add("시스템 빌드 (참고)", fz.get("시스템빌드") or "-", b, "확인 질의 응답(orc.start build) — 규격서 동결 대상 아님, 참고로 표시",
        "참고")
    add("AI 엔진 결정성 옵션", "temperature = 0", "시스템 미제공", "temperature 를 알려 주는 API 없음 — 의뢰기업 선언값으로 기록", "선언값")
    return rows


def precheck_rows(versions):
    """사전 점검 표 — [{항목, 결과(정상/실패), 내용}]"""
    pr = versions.get("확인질의") or {}
    rows = [{"항목": "AI 사전진단 엔진 모델 조회", "결과": "정상" if str(versions.get("모델명출처", "")).endswith("조회값") else "실패",
             "내용": f"{versions.get('모델명')} ({versions.get('모델명출처')})"},
            {"항목": "추론 서버(vLLM) 조회", "결과": "정상" if versions.get("vLLM") else "실패", "내용": versions.get("vLLM") or "조회 실패"},
            {"항목": "법령·조례 DB 조회", "결과": "정상" if versions.get("법령DB") else "실패", "내용": versions.get("법령DB") or "조회 실패"}]
    if pr:
        rows.append({"항목": "AIPS 확인 질의 1건", "결과": "실패" if pr.get("오류") else "정상",
                     "내용": pr.get("오류") or f"룰엔진 {pr.get('룰엔진버전')} · 빌드 {pr.get('build')} · {pr.get('소요초')}초"})
    return rows
