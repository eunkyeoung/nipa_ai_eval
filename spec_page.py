"""
성과지표 화면 (AI 1 ~ AI 7) — qa_app.py 사이드바의 '화면' 전환에서 불러옴
"""
import glob
import io
import json
import os
import sys
import time

import pandas as pd
import streamlit as st

import spec_eval as S

DATA = "spec_data"
FILES = {"AI1": ["testset_ai1", "answer_ai1"], "AI2": ["testset_ai2", "answer_ai2"], "AI3": ["testset_ai3"],
         "AI4": ["testset_ai4", "answer_ai4"], "AI5": ["testset_ai5", "answer_ai5", "law_article_db"],
         "AI6": ["testset_ai6", "answer_ai6", "intent_map_ai6", "result_ai6"], "AI7": ["testset_ai7", "answer_ai7", "result_ai7"], "DA2": []}
LABEL = {"testset": "시험셋(질의)", "answer": "정답셋", "law_article_db": "법령·조례 조문 DB",
         "result": "실행 결과(시스템 응답)", "intent": "의도 대응표"}
OPTIONAL = ("law_", "result_", "intent_")   # 없어도 실행 가능 (조문 DB 없으면 ① 판정 보류 · 결과 파일은 수동 채점용 · 대응표 없으면 기본 대응표)
RAW_VERDICT = {"possible": "승인 가능", "impossible": "부적합", "conditional": "조건부 가능",   # AIPS 원 판정값 → 수행계획서 4종
               "needs_review": "검토 필요", "unknown": "검토 필요"}
HIDE_TABLES = {"AI1": ("보조 지표(적용 법령·근거 조문)별 일치·불일치 건수 및 일치율",)}   # 2026-11-11 의뢰기업 요청: 결과 화면에서 숨김 (result.xlsx에는 남음)
HIDE_CASE_COLS = ["적용룰셋"]          # 질의별 결과 화면에서 숨길 컬럼 (질의별 상세 결과·result.xlsx에는 남음)


EMPTY = "–"                              # 빈칸 표시


def show_table(df, where=None, height=None):
    """모든 표 공통 — 한 줄 표(st.dataframe): 칸 너비는 머리글 경계를 끌어서 조절, 긴 글자는 칸을 넓히거나 전체 화면에서 확인.
    정렬·검색·CSV 내려받기·전체 화면 도구는 표에 마우스를 올리면 나옴"""
    d = df.copy()
    for c in d.columns:
        if d[c].dtype == object:
            d[c] = d[c].where(d[c].notna(), EMPTY).replace({"": EMPTY, "-": EMPTY, "nan": EMPTY, "None": EMPTY})
    kw = {"height": height} if height else {}
    (where or st).dataframe(d, hide_index=True, width="stretch", **kw)


def rows_height(n, cap=420):
    """한 줄 표의 높이 (머리글 38px + 행 수 × 35px), cap 넘으면 스크롤"""
    return min(cap, 38 + 35 * max(n, 1))


AI6_LIVE_COLS = ["사례ID", "명령유형", "질의", "해석 의도", "핵심 파라미터", "실행 대상 기능", "지도 상태",
                 "①", "②", "③", "판정", "오류원인", "불일치", "소요초"]


def ai6_live(df, total):
    """AI6 실행 중 화면 — 사례가 끝날 때마다(2초마다 다시 읽음) 규격서 2-6 출력 항목(해석 의도·핵심 파라미터·실행 대상 기능·
    지도 상태)과 판정(①②③ · 정상 수행/오류 · 오류 원인), 지금까지의 정상 수행·오류 건수를 보여 줌. df = progress.xlsx"""
    import ai6_eval as E6
    m = len(df)
    ok = int((df["판정"] == "정상 수행").sum())
    k = st.columns(4)
    k[0].metric("끝난 명령", f"{m} / {total}건")
    k[1].metric("정상 수행", f"{ok}건")
    k[2].metric("오류", f"{m - ok}건")
    k[3].metric("지금까지 정확도", "-" if not m else f"{E6.floor_pct(ok, m, S.SPEC['AI6'].get('dec', 2)):.2f} %",
                help="끝난 명령만으로 낸 중간 값이에요. 최종 정확도는 다 끝난 뒤 아래 결과 화면에서 전체 건수 기준으로 나와요.")
    by_type = []
    for t in E6.TYPES:
        sub = df[df["명령유형"] == t] if "명령유형" in df else df.iloc[0:0]
        by_type.append(f"{t} 정상 {int((sub['판정'] == '정상 수행').sum())} / 끝남 {len(sub)}")
    st.caption("명령 유형별 — " + " · ".join(by_type))
    cols = [c for c in AI6_LIVE_COLS if c in df]
    view = df[cols].iloc[::-1].rename(columns={"질의": "명령문"})            # 방금 끝난 명령이 맨 위
    view = view.astype(object).where(view.notna(), "")                      # 엑셀에서 빈칸만 있던 칸(오류원인·불일치)은 숫자형 NaN으로 읽힘
    show_table(view, height=rows_height(len(view), cap=420))
    st.caption("① 의도 · ② 핵심 파라미터 · ③ 지도 제어 — 셋 다 O 여야 정상 수행, 오류 원인은 ①→②→③ 중 처음 틀린 기준. "
               "명령별 응답 원문·규격 출력 JSON 은 끝난 뒤 아래 '질의별 상세 결과'에서 봅니다.")


SHORT = {"AI1": "AI 1 정확도", "AI2": "AI 2 F1", "AI3": "AI 3 일관성", "AI4": "AI 4 조건부", "AI5": "AI 5 환각",
         "AI6": "AI 6 지도 제어", "AI7": "AI 7 3D 시뮬레이션", "DA2": "DA 2 온톨로지"}
NOT_PROVIDED = [   # 시험의뢰서가 요구하지만 AIPS가 질의 응답(SSE)에 실어 보내지 않는 값 → 대신 쓰는 방법
    "**AI 엔진 모델 버전** — 응답에 모델명이 없음 → 시험 시작 때 **공간정보 에이전트·MCP 콘솔(192.168.200.115:18800)**에서 지금 쓰는 모델명을 "
    "조회해 기록 (현재 gpt-oss-120b). 조회 실패 시 시험의뢰서 시험환경 값(gpt-oss-120b)",
    "**룰셋 버전** — 룰셋 자체 버전 값이 없음 → 응답 안의 **룰엔진 버전(engine_version, 현재 3.3.0)**으로 표시",
    "**법령·조례 DB 버전** — 응답에 없음 → 시험 시작 때 **법령 수집·적재 관리자(192.168.200.116:17801)**에서 마지막 수집일·법령·조문 수를 "
    "조회해 기록 (현재 수집 기준 2026-10-18)",
    "**6단계 처리 로그** — 시스템이 단계별 로그 파일을 따로 내보내지 않음 → 응답에 실려 오는 진행 메시지·도구 호출 기록·단계 결과로 "
    "6단계를 재구성. 오케스트레이터(115:16001) 실행 기록은 10-27 이후 기록이 없어 대체 불가",
    "**적용 룰셋 ID** — 별도 '룰셋 ID' 값은 없음 → 판정에 쓰인 룰 ID(R-001 등, 룰엔진 룰 목록과 같은 번호) + "
    "용도지역 매트릭스(MATRIX:용도지역코드:시설코드)로 표시",
    "위 값은 모두 **시스템 응답 값이 아니라 개발·운영 사이트 조회값**이므로, 시험의뢰서의 '동결 버전 확인'을 확실히 하려면 "
    "AIPS 응답(orc.start)에 모델명·룰셋 버전·법령 DB 기준일을 넣어 달라고 개발팀에 요청 필요",
]
NOT_PROVIDED_AI6 = [   # [AI 6] 규격 시험방법 (2) '동결 버전 확인' 중 공간오케·에이전트가 알려 주지 않는 값 → 대신 쓰는 방법
    "**AI 모델** — 공간오케 /ready 의 설정 모델, LLM 서버 /v1/models 목록(LLM 키를 넣었을 때), vLLM 버전은 LLM 서버 /version 으로 확인 "
    "(위 'LLM 모델' 두 행과 'vLLM 버전' 행, 조회값)",
    "**공간분석 API 버전** — 사전 점검 때 /v3/api-docs 의 info.version 을 읽어 시험 조건(v0.1)과 대조 — 다르면 사전 점검 실패로 실행하지 않음 (조회값)",
    "**Sub 오케스트레이터·에이전트 버전, 지도 데이터 기준일** — 시스템이 알려 주지 않음(/health 는 상태만) → 위 '동결 버전 확인' 표에 "
    "**의뢰기업 선언값**으로 적고, 실행마다 '연결 에이전트' 목록이 같은지로 바뀌었는지만 확인",
]
CRITERIA = {
    "AI1": "AI 판단 결과(판단 유형)가 정답 판단 유형과 정확히 일치하는지 평가. 개별 테스트 사례에 대해 판단 유형이 정답과 "
           "일치한 경우 일치 건수로 계수(판단 유형 2종: 허가 가능 / 허가 불가능, 조작적 정의서에 따라 조건부 가능 = 허가 가능).\n\n"
           "적용 법령·근거 조문(법령ID.조.항, 호·목 단위 제외)의 일치 여부는 보조 지표로 별도 산출하며 정확도 산정에는 포함하지 않음 "
           "(정답 조문·법령 중 하나라도 AI 응답에 있으면 일치).",
    "AI2": "동일 시나리오의 표현 변형 질의 전체를 개별 테스트 샘플로 하여, AI 판단 결과(판단 유형)와 시나리오의 정답 판단 유형을 비교. "
           "판단 유형 2종 각각에 대해 TP·FP·FN을 산출하여 2 × 2 혼동 행렬을 작성.\n\n"
           "Precision(%) = TP / (TP + FP) × 100, Recall(%) = TP / (TP + FN) × 100, "
           "F1 Score(%) = 2 × Precision × Recall / (Precision + Recall).\n\n"
           "보조 지표: 표현 변형 재현 일치율(%) = 원문 질의 결과와 동일한 판단 유형을 도출한 변형 질의 수 / 전체 변형 질의 수 × 100 "
           "(F1 산정에는 포함하지 않음).",
    "AI3": "6단계 판단 프로세스: ① 질의 파싱 ② 대상 필지(PNU) 조회 ③ 토지특성(지목, 면적) 조회 ④ 적용 법령·조례 검색 "
           "⑤ 룰셋 적용 ⑥ 최종 판단 도출.\n\n"
           "각 사례의 1회차 수행 로그를 기준 로그로 하여 2–5회차 로그를 각각 비교. 비교 대상 필드: 단계 실행 순서(①–⑥ 실행 순서·여부), "
           "조회 데이터소스, 조회 키·조회 결과 건수, 적용 룰셋 ID 집합(⑤), 검색 법령 집합(④), 최종 판단 유형(⑥). "
           "집합 형태의 필드는 순서를 무시한 집합 일치로 판정. 비교 제외: 타임스탬프, 처리 소요 시간, 요청 ID, 로그 시퀀스 번호.\n\n"
           "보조 지표: 사례 단위 일관성(%) = 5 회차가 모두 일치한 사례 수 / 전체 사례 수 × 100 (일관성 산정에는 포함하지 않음).",
    "AI4": "판정 결과: 정답 라벨과 정확 일치. 단서 조건 근거 조문: 정규화 표기(법령ID.조.항, 호·목 단위 제외) 기준 정답 조문 전부 포함(순서 무시, 정답 외 관련 조문의 추가 제시는 허용). "
           "둘 다 적합해야 적합 건수로 계수.\n\n"
           "보조 지표: 보완사항 — 정답 보완사항의 핵심 요건 포함 여부(정확 일치 / 부분 일치 / 불일치 3단 표기), "
           "필요 행정절차 — 정답 행정절차 포함 여부(정확 일치 / 부분 일치 / 불일치 3단 표기, 순서 무시). 정확도 산정에는 포함하지 않음.",
    "AI5": "응답별로 환각 유형 ①–④를 순서대로 자동 판정: ① 인용 조문이 법령·조례 조문 DB에 존재하는지 ② 근거 조문 항목이 존재하고 "
           "값이 비어 있지 않은지 ③ 인용 조문과 정답 조문셋의 교집합이 존재하는지 ④ 응답의 적용 법령이 정답 적용 법령과 일치하는지. "
           "하나의 응답에서 ①–④ 중 1개 이상 검출되면 환각 응답 1건으로 계수하며, 복수 유형이 동시에 검출되어도 중복 계수하지 않음.",
    "AI6": "시험 데이터셋의 자연어 지도 제어 명령을 문장 그대로, 사례마다 새 대화(thread_id)로 지도제어·공간분석 Sub 오케스트레이터에 "
           "1회 입력. 사례마다 아래 3개 검증 기준을 모두 만족해야 정상 수행 1건.\n\n"
           "① 의도 추출: 시스템이 실제로 부른 기능과 지도 동작을 의도 대응표로 바꾼 의도가 정답 의도와 집합 완전 일치 — "
           "빠지거나 남으면 오류(부분 일치 = 오류). 레이어 찾기·토지이용계획 조회 같은 보조 호출은 세지 않고, "
           "공간오케가 보낸 의도 코드는 참고로만 기록\n\n"
           "② 파라미터 추출: 위치는 대상 필지 PNU(필지 선택·분석 호출에 쓴 PNU, 검색 후보는 제외)로 대조, 분석대상(분석 종류·레이어)은 "
           "집합 완전 일치 — 레이어는 공간오케가 실제로 고른 레이어로 대조하고, 그 레이어 ID가 그 레이어의 자료 테이블인지도 확인"
           "(예: '지구단위계획구역'인데 도로 테이블을 고르면 오류), 명령에 든 반경은 단위만 맞춰 값이 같아야 함"
           "('300m' = '0.3km', 300.4m ≠ 300m). 표기만 다른 경우는 일치('263번지' = '263', 가운뎃점·공백 무시). "
           "정답에 없는 반경을 쓰면 오류\n\n"
           "③ GIS 지도 제어: 응답의 지도 제어 명령(대상 필지 선택·이동, 확대/축소 — 이동+확대 한 번 명령 포함)과 표출 도형(대상 필지 도형·분석 결과 도형)이 "
           "정답 시나리오와 일치 — 정답의 도형이 모두 와야 함(남는 도형은 기록만)\n\n"
           "되묻기(후보 선택 요청)는 오류. 무응답·응답 대기 상한 초과·연결 실패·시스템 오류 이벤트, 그리고 위치 확정 호출이 "
           "시스템 사유로 실패해(연결 실패 등 — '못 찾음'은 제외) 분석을 시작하지 못한 사례는 규격('시스템 오류로 응답하지 못한 사례')대로 "
           "'응답 오류'로 따로 집계(그 밖의 도구 실패는 답변이 오면 응답한 것으로 보고 ①②③으로 판정). "
           "오류 원인은 규격대로 ① → ② → ③ 중 처음 어긋난 기준 — 되묻기 사유·위치 미확정 같은 내용은 '세부 원인'으로 따로 남김. "
           "\n\n"
           "호출 순서·중심 좌표·줌 레벨·산출 면적·거리는 로그에만 기록하고 판정에 쓰지 않음(명령에 든 반경만 ②에서 판정). "
           "결과는 소수점 셋째자리 이하 버림.",
    "AI7": "사례마다 ① 의도 추출 ② 핵심 파라미터(위치·층수·높이·용도·기준일·시간대·조망점 등) ③ 3D 시뮬레이션 실행(3D 지도 상태·생성 객체·"
           "실행 조건이 정답 시나리오와 일치, 실행 상태 '완료')을 순서대로 판정. 셋 다 일치하면 정상 수행 1건, 부분 일치는 오류. "
           "오류 원인은 최초 불일치 기준으로 분류. 실행 대기 상한 안에 '완료'가 아니면 미실행(실행 오류).\n\n결과는 소수점 둘째자리 이하 버림.",
    "DA2": "세부 시험항목 1-1 ~ 1-4(DA 2-1 ~ 2-4)의 구성지표 4개를 가중합산해 온톨로지(ODP) 설계 완성도를 산출하고 96 % 이상 여부를 판정. "
           "기능 정확성(X) = 1 − 부정확한 기능의 수 / 고려 대상 기능의 수(4). 검증 결과는 validation_result_da2.csv로 저장. "
           "결과는 소수점 셋째자리 이하 버림.",
}
CMD_NOTE = ("실행 결과 파일(result_ai6 / result_ai7)을 올리면 성과지표 기준으로 채점해요. "
            "자동 실행은 지도·3D 명령의 응답 구조를 확인한 뒤 연결할 예정이에요.")


def data_path(stem):
    for ext in (".csv", ".xlsx"):
        p = os.path.join(DATA, stem + ext)
        if os.path.exists(p):
            return p
    return None


def runs(item):
    return sorted([d for d in glob.glob(os.path.join("spec", item, "*")) if os.path.isdir(d)],
                  key=os.path.basename, reverse=True)          # 폴더 이름(실행 시각) 순 — 수정 시각은 재채점하면 바뀌므로 쓰지 않음


def read_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                                # noqa: BLE001
        return {}


def fmt_pct(v, item=None):
    dec = S.SPEC[item].get("dec", 2) if item else 2
    return "-" if v is None else f"{v:.{dec}f} %"


def trunc_txt(item):
    return "소수점 둘째자리 이하 버림" if S.SPEC[item].get("dec", 2) == 1 else "소수점 셋째자리 이하 버림"


def target_txt(item):
    sp = S.SPEC[item]
    return f"{sp['target']:.0f} % {'이상' if sp['cmp'] == '>=' else '이하'}"


@st.cache_data(show_spinner=False)
def _score(item, run_dir, mt, ans, ts, law, files_mt):
    return S.score(item, run_dir, ans, ts, law)


def score_run(item, run_dir):
    meta = read_json(os.path.join(run_dir, "meta.json"))

    def pick(key, stem):                      # 실행 폴더의 사본(data/) → meta 경로 → 현재 spec_data 순
        p = meta.get(key)
        snap = os.path.join(run_dir, "data", os.path.basename(p.replace("\\", "/"))) if p else None   # PC(윈도)에서 실행한 결과의 '\' 경로도 서버(리눅스)에서 찾도록
        if snap and os.path.exists(snap):
            return snap
        return p if p and os.path.exists(p) else data_path(stem)
    ans, ts, law = pick("answer", f"answer_{item.lower()}"), pick("testset", f"testset_{item.lower()}"), pick("lawdb", "law_article_db")
    if item != "AI5":
        law = None
    if item == "AI3":
        ans = None
    lp = os.path.join(run_dir, S.log_name(run_dir, item))
    if not os.path.exists(lp):
        return None, meta, (ans, ts, law)
    code = [S.__file__] + ([os.path.join(os.path.dirname(S.__file__), "ai6_eval.py")] if item == "AI6" else [])
    fm = tuple(os.path.getmtime(p) if p and os.path.exists(p) else 0 for p in (ans, ts, law, *code))   # 채점 코드가 바뀌어도 다시 계산
    try:
        r = _score(item, run_dir, os.path.getmtime(lp), ans, ts, law, fm)
    except Exception as e:                                            # noqa: BLE001
        st.error(f"채점 중 오류: {type(e).__name__}: {e}")
        r = None
    return r, meta, (ans, ts, law)


# ---------------------------------------------------------------- 사이드바
@st.cache_data(show_spinner=False)
def _sha256(path, mtime):
    """파일 SHA-256 (수정 시각이 바뀌면 다시 계산)"""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# [DA 2] 입력 양식 — 제작지시서 0.2v(2026-11-06)의 CSV 필수 열 중 입력(정답) 열. 예시 1행
DA2_TEMPLATES = {
    "da2_cq_testset.csv": (
        ["cq_id", "odp_type", "municipality", "question", "inference_required", "query_file", "expected_bindings"],
        [["CQ-001", "Exception (예외·단서 적용)", "공통", "대지가 도로에 2m 미만으로 접한 경우에도 건축이 허용되는 단서 조항은 무엇인가?",
          "false", "sparql/CQ-001.rq", '["건축법.44.1", "건축법시행령.28.1", "건축법시행령.28.2"]']]),
    "da2_schema_checklist.csv": (
        ["check_id", "item_group", "description", "method", "validator_file", "expected"],
        [["SCH-001", "핵심 클래스", "법령(aip:Law)마다 법령명(aip:lawName)이 있다", "SPARQL ASK", "checks/SCH-001.rq", "true"]]),
    "aips_da2_mapping_testset.csv": (
        ["mapping_id", "source_field", "target_element", "expected_mapping"],
        [["MAP-001", "(원천 테이블).(필드) 예: 법령수집DB.법령.법령명", "https://onto.go.kr/aip/lawName",
          '{"class": "aip:Law", "property": "aip:lawName", "relation": "", "datatype": "xsd:string"}']]),
}


def sidebar(side, launch, running):
    os.makedirs(DATA, exist_ok=True)
    # 시험 항목 버튼 7개를 사이드바 한 줄에 (버튼 폭을 균등하게 줄임)
    side.markdown("""<style>
div.st-key-spec_item, div.st-key-spec_item [data-testid="stButtonGroup"] { width: 100% !important; }
div.st-key-spec_item [data-testid="stButtonGroup"] > div { flex-wrap: nowrap !important; width: 100% !important; min-width: 100% !important; max-width: none !important; }
div.st-key-spec_item [data-testid="stButtonGroup"] button {
    flex: 1 1 0 !important; min-width: 0 !important; padding: 0.25rem 0 !important; }
div.st-key-spec_item [data-testid="stButtonGroup"] button p { font-size: 0.8rem; }
</style>""", unsafe_allow_html=True)
    pick = side.segmented_control("시험 항목", list(S.SPEC), format_func=lambda k: k,
                                  default=st.session_state.get("spec_item_last", "AI1"), key="spec_item")
    item = pick or st.session_state.get("spec_item_last", "AI1")          # 다시 눌러 선택이 풀려도 유지
    st.session_state["spec_item_last"] = item
    sp = S.SPEC[item]
    side.caption(f"{sp['no']} · {sp['metric']} · 목표 {target_txt(item)}")

    if sp.get("da2"):                                   # [DA 2] 온톨로지 검증 — 검증 자산 확인만 (실행은 자산이 준비되면 연결)
        with side.popover("📂 시험 데이터 (검증 자산)", width="stretch"):
            for label, path in sp["assets"]:
                if os.path.exists(path):
                    kb = os.path.getsize(path) / 1024
                    st.markdown(f"**{label}** `{os.path.basename(path)}` — ✅ {kb:,.0f} KB  \nSHA-256 `{_sha256(path, os.path.getmtime(path))}`")
                else:
                    st.markdown(f"**{label}** `{os.path.basename(path)}` — ❌ 없음")
                ext = os.path.splitext(path)[1].lower()
                up = st.file_uploader(os.path.basename(path), type=[ext.lstrip(".")], key=f"up_da2_{os.path.basename(path)}",
                                      label_visibility="collapsed")
                if up is not None:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(up.getvalue())
                    st.cache_data.clear()
                    st.success(f"{os.path.basename(path)} 저장")
            st.caption("규격서 [DA 2] 사전조건의 검증 자산입니다. " + sp.get("source", ""))
            st.caption("작성 규칙(제작지시서 0.2v) — CSV는 UTF-8, 헤더 1행. 정답 바인딩·매핑은 JSON 문자열. "
                       "조문은 `법령명.조.항`(항 없으면 `법령명.조`, 별표는 `법령명.별표N`), 호·목은 쓰지 않음. "
                       "CQ·스키마 zip에는 CSV 1개와 질의 파일(.rq / Shape .ttl)을 함께 넣음")
            st.divider()
            st.markdown("**양식**")
            for fn, (cols, rows) in DA2_TEMPLATES.items():
                st.download_button(f"⬇ {fn} 양식", pd.DataFrame(rows, columns=cols).to_csv(index=False).encode("utf-8-sig"),
                                   file_name=fn, mime="text/csv", width="stretch", key=f"tpl_da2_{fn}")
        # 실행: 구성지표 선택(비우면 전체 2-1 ~ 2-4) → tools/da2_run.py
        labels = {f"{k} {n}": i + 1 for i, (k, n, *_r) in enumerate(sp["parts"])}
        sel = side.multiselect("실행할 구성지표 (비우면 전체)", list(labels), key="da2_parts")
        parts = sorted(labels[x] for x in sel) or [1, 2, 3, 4]
        need = {1: (0, 3, 4), 2: (1, 3, 4, 5), 3: (2, 3, 4), 4: (3, 4)}         # 구성지표별 필요한 검증 자산(assets 순번)
        miss = sorted({os.path.basename(sp["assets"][j][1]) for p in parts for j in need[p] if not os.path.exists(sp["assets"][j][1])})
        cq_ids = []
        if 1 in parts and os.path.exists(sp["assets"][0][1]):
            try:
                import zipfile
                with zipfile.ZipFile(sp["assets"][0][1]) as z:
                    cq_ids = pd.read_csv(io.BytesIO(z.read("da2_cq_testset.csv")), encoding="utf-8-sig")["cq_id"].tolist()
            except Exception:                                          # noqa: BLE001
                cq_ids = []
        ids = side.multiselect("실행할 CQ ID (비우면 전체)", cq_ids, key="da2_ids") if cq_ids else []
        if sel or ids:
            side.caption("부분 실행 — 결과는 따로 저장되고 종합 점수(가중합)는 계산하지 않아요.")
        run = side.button("⏳ 실행 중…" if running else f"▶ {SHORT[item]} 시험 실행", type="primary", width="stretch",
                          disabled=running or bool(miss), help="결과는 spec/DA2/<시각>/ 에 저장돼요. SHACL 검증 포함 약 7분.")
        if miss:
            side.caption("❌ 검증 자산 없음: " + ", ".join(miss))
        if run:
            import qa_runner
            cmd = [sys.executable, "-u", os.path.join("tools", "da2_run.py"), "--parts", ",".join(map(str, parts)),
                   "--status-file", qa_runner.STATUS_FILE]
            if ids:
                cmd += ["--ids", ",".join(ids)]
            st.session_state["spec_run_dir"] = None
            launch(cmd, len(parts))
        return item

    need = FILES[item]
    with side.popover("📂 시험 데이터", width="stretch"):
        st.caption(f"폴더 `{DATA}/` 의 CSV(UTF-8) 또는 XLSX를 사용해요. 올리면 같은 이름으로 저장돼요.")
        if item == "AI6":
            st.caption("작성 규칙 — 정답의도는 세미콜론(;)으로 구분 · 정답파라미터·정답시나리오는 `키=값; 키=값` (여러 값은 쉼표) · "
                       "참조PNU는 19자리(시스템에 보내지 않고 위치 대조에만 씀) · 의도 대응표가 없으면 기본 대응표로 판정 (README 12)")
        else:
            st.caption("작성 규칙 — 여러 값은 세미콜론(;)으로 구분 · 근거조문은 `법령명.조.항` (예: `건축법.2.1; 건축법.44`) "
                       "또는 `건축법 제44조 제1항`처럼 '제'를 붙여 표기 (자세한 내용: README 9-2)")
        for stem in need:
            if item == "AI6" and stem.startswith("result_"):         # AI6 는 자동 실행 — 결과 파일을 올리지 않음
                continue
            p = data_path(stem)
            kind = LABEL.get(stem.split("_")[0], LABEL.get(stem, stem))
            n = len(S.read_table(p)) if p else 0
            st.markdown(f"**{kind}** `{stem}` — " + (f"✅ {n}건" if p else ("기본 대응표 사용" if stem.startswith("intent_") else "❌ 없음"))
                        + (f" · SHA-256 `{S.sha256(p)}`" if p and item == "AI6" else ""))   # 규격서에 적힌 64자리와 그대로 대조
            up = st.file_uploader(stem, type=["csv", "xlsx"], key=f"up_{stem}", label_visibility="collapsed")
            if up is not None and st.session_state.get(f"up_done_{stem}") != up.file_id:   # 올린 파일은 한 번만 저장 (다시 그릴 때 덮어쓰지 않게)
                ext = os.path.splitext(up.name)[1].lower()
                for e in (".csv", ".xlsx"):
                    if os.path.exists(os.path.join(DATA, stem + e)):
                        os.remove(os.path.join(DATA, stem + e))
                with open(os.path.join(DATA, stem + ext), "wb") as f:
                    f.write(up.getvalue())
                st.session_state[f"up_done_{stem}"] = up.file_id
                st.cache_data.clear()
                st.success(f"{stem}{ext} 저장")
        if item == "AI6":
            st.divider()
            st.markdown("**엑셀 한 파일에서 가져오기** — 시트 이름에 `testset`·`answer`(또는 질문지·정답지)가 들어간 통합 문서. "
                        "`대응표` 시트가 있으면 의도 대응표도 가져와요. 기존 파일은 덮어써요.")
            wb = st.file_uploader("ai6_workbook", type=["xlsx"], key="up_ai6_wb", label_visibility="collapsed")
            if wb is not None and st.session_state.get("ai6_wb_done") != (wb.name, wb.size):
                try:
                    msg = import_ai6_workbook(wb.getvalue())
                    st.session_state["ai6_wb_done"] = (wb.name, wb.size)   # 다시 그릴 때 같은 파일을 또 쓰지 않도록
                    st.cache_data.clear()
                    st.success(msg)
                except Exception as e:                            # noqa: BLE001
                    st.error(f"가져오지 못했어요: {e}")
        if item == "AI4":                       # 규격서 [AI 4] 시험방법 (2): 판정 결과별·예외 유형별 개수 확인
            t4, a4 = S.read_table(data_path("testset_ai4")), S.read_table(data_path("answer_ai4"))
            if t4 is not None and "예외유형" in t4:
                ec = t4["예외유형"].value_counts()
                st.markdown("**예외 유형별 개수** — " + " / ".join(f"{k} {int(ec.get(k, 0))}건" for k in ("예외 조항", "단서 규정", "협의 대상", "심의 대상")))
            if a4 is not None and "판정결과" in a4:
                vc = a4["판정결과"].map(S.type2).value_counts()
                st.markdown("**판정 결과별 개수** — " + " / ".join(f"{k} {int(vc.get(k, 0))}건" for k in S.TYPE2))
        st.divider()
        st.markdown("**양식**")
        for stem in need:
            if item == "AI6" and stem.startswith("result_"):         # AI6 는 자동 실행 — 결과 파일 양식은 쓰지 않음
                continue
            cols, rows = S.TEMPLATES[item][stem + ".csv"]
            st.download_button(f"⬇ {stem}.csv 양식", pd.DataFrame(rows, columns=cols).to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"{stem}.csv", mime="text/csv", width="stretch", key=f"tpl_{stem}")
        if item == "AI1" and os.path.exists("gold.xlsx"):
            if st.button("gold.xlsx → AI1 시험셋·정답셋 만들기", width="stretch",
                         help="기존 정답셋(주소·시설물·정답판정·근거)으로 testset_ai1.csv / answer_ai1.csv 생성 (기존 파일 덮어씀)"):
                import qa_eval
                g = qa_eval.load_gold("gold.xlsx")
                import qa_runner
                n = S.gold_to_ai1(g, qa_runner.find_col(g.columns, "주소"), qa_runner.find_col(g.columns, "시설물"), DATA)
                st.cache_data.clear()
                st.success(f"{n}건 생성")

    missing = [s for s in need if not data_path(s) and not s.startswith(OPTIONAL)]
    for s_ in need:
        p = data_path(s_)
        opt = s_.startswith(OPTIONAL)
        if item == "AI6" and s_.startswith("result_") and not p:
            continue                                                  # AI6 결과 파일은 수동 채점용 — 없으면 표시하지 않음
        side.caption(("✅ " if p else ("⚠️ " if opt else "❌ ")) + f"{s_}" +
                     (f" · {len(S.read_table(p))}건" if p else (" 없음 (① 판정 보류)" if s_.startswith("law_")
                                                              else " 없음 (기본 대응표로 판정)" if s_.startswith("intent_")
                                                              else (" 없음 (채점 전에 올려 주세요)" if opt else " 없음"))))
    if item == "AI6":
        return sidebar_ai6(side, launch, running, missing)
    if S.SPEC[item].get("cmd"):
        side.caption(CMD_NOTE)
        rp = data_path(f"result_{item.lower()}")
        if side.button("⏳ 실행 중…" if running else f"▶ {SHORT[item]} 결과 채점", type="primary", width="stretch",
                       disabled=running or bool(missing) or not rp,
                       help="📂 시험 데이터에 올린 실행 결과 파일을 가져와 spec/" + item + "/<시각>/ 에 로그·결과를 저장하고 채점해요."):
            try:
                rd = S.import_results(item, rp, data_path(f"testset_{item.lower()}"), data_path(f"answer_{item.lower()}"))
                import spec_runner
                spec_runner.run_score(item, rd, data_path(f"answer_{item.lower()}"), data_path(f"testset_{item.lower()}"))
                st.session_state["spec_run_dir"] = rd
                st.cache_data.clear()
                st.rerun()
            except Exception as e:                                    # noqa: BLE001
                side.error(f"채점하지 못했어요: {e}")
        return item
    repeat = 1
    if item == "AI3":
        repeat = side.number_input("반복 횟수", 2, 10, 5, key="spec_repeat")
    ts_p = data_path(f"testset_{item.lower()}")
    ids = []
    if ts_p:
        tsdf = S.read_table(ts_p)
        key = "질의ID" if item == "AI2" else "사례ID"
        if key in tsdf:
            ids = side.multiselect("실행할 ID (비우면 전체)", tsdf[key].tolist(), key=f"spec_ids_{item}")
            n = (len(ids) or len(tsdf)) * repeat
            side.caption(f"질의 {n}회 · 예상 약 {n * 50 // 60}분")
    run = side.button("⏳ 실행 중…" if running else f"▶ {SHORT[item]} 시험 실행", type="primary", width="stretch",
                      disabled=running or bool(missing),
                      help="결과는 spec/" + item + "/<시각>/ 에 저장돼요. 백그라운드에서 실행됩니다.")
    if missing:
        side.caption("❌ 표시된 파일을 먼저 올려 주세요 (📂 시험 데이터).")
    if run:
        import qa_runner
        cmd = [sys.executable, "-u", "spec_runner.py", item, "--testset", ts_p, "--status-file", qa_runner.STATUS_FILE,
               "--base", qa_runner.BASE, "--timeout", str(qa_runner.TIMEOUT)]
        a = data_path(f"answer_{item.lower()}")
        if a:
            cmd += ["--answer", a]
        if item == "AI5" and data_path("law_article_db"):
            cmd += ["--lawdb", data_path("law_article_db")]
        if item == "AI3":
            cmd += ["--repeat", str(repeat)]
        if ids:
            cmd += ["--cases", ",".join(ids)]
        st.session_state["spec_run_dir"] = None
        launch(cmd, (len(ids) or len(S.read_table(ts_p))) * repeat)
    return item


# ---------------------------------------------------------------- [AI 6] 지도 제어 — 자동 실행
AI6_COLS = {"testset_ai6": ["사례ID", "명령유형", "명령문"],
            "answer_ai6": ["사례ID", "명령유형", "정답의도", "정답파라미터", "정답시나리오", "참조PNU"],
            "intent_map_ai6": ["정답의도", "근거", "값", "비고"]}
AI6_OPT_COLS = {"answer_ai6": ["검증자", "교차검증자", "검증일"]}   # 선택 칸 — 있으면 그대로 옮김
AI6_SHEET_KEYS = {"testset_ai6": ("testset", "질문지", "시험셋"), "answer_ai6": ("answer", "정답지", "정답셋"),
                  "intent_map_ai6": ("intent_map", "대응표")}


def import_ai6_workbook(data):
    """질문지·정답지(·대응표) 시트가 든 엑셀 한 파일 → spec_data/testset_ai6.csv · answer_ai6.csv (· intent_map_ai6.csv)
    양식 밖의 칸(검토용 메모 등)은 버리고 양식 칸만 UTF-8(BOM) CSV 로 저장"""
    import csv
    import tempfile

    import ai6_eval as E6
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
        tf.write(data)
        tmp = tf.name
    try:
        names = E6.sheet_names(tmp)
        plan, notes = {}, []                                       # 1단계: 모든 시트를 읽고 확인만 (아직 저장 안 함)
        for stem, keys in AI6_SHEET_KEYS.items():
            sheet = next((n for n in names if any(k.lower() in n.lower() for k in keys)), None)
            if sheet is None:
                if stem == "intent_map_ai6":
                    continue
                raise ValueError(f"'{'/'.join(keys)}' 시트를 찾지 못했어요 (시트: {', '.join(names)})")
            rows = E6.read_rows(tmp, sheet=sheet)
            cols = AI6_COLS[stem]
            lack = [c for c in cols if not rows or c not in rows[0]]
            if lack and stem == "intent_map_ai6":                  # 대응표는 선택 — 형식이 다르면(설명용 표 등) 건너뛰고 기본 대응표
                notes.append(f"'{sheet}' 시트는 대응표 양식(정답의도·근거·값·비고)이 아니라 건너뜀")
                continue
            if lack:
                raise ValueError(f"'{sheet}' 시트에 칸이 없어요: {', '.join(lack)}")
            plan[stem] = (sheet, cols + [c for c in AI6_OPT_COLS.get(stem, []) if rows and c in rows[0]], rows)
        if "intent_map_ai6" in plan:                               # 대응표 형식(근거 종류 등)까지 확인
            import io as _io
            buf = _io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            w.writerow(AI6_COLS["intent_map_ai6"])
            for r in plan["intent_map_ai6"][2]:
                w.writerow([r.get(c, "") for c in AI6_COLS["intent_map_ai6"]])
            with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig") as tf2:
                tf2.write(buf.getvalue())
            try:
                E6.load_rules(tf2.name)
            finally:
                os.remove(tf2.name)
        done = []
        for stem, (sheet, cols, rows) in plan.items():             # 2단계: 전부 통과했을 때만 저장
            for e in (".csv", ".xlsx"):
                if os.path.exists(os.path.join(DATA, stem + e)):
                    os.remove(os.path.join(DATA, stem + e))
            with open(os.path.join(DATA, stem + ".csv"), "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, lineterminator="\n")
                w.writerow(cols)
                for r in rows:
                    w.writerow([r.get(c, "") for c in cols])
            done.append(f"{stem}.csv {len(rows)}건 ← '{sheet}'")
        return " · ".join(done + notes)
    finally:
        os.remove(tmp)


def sidebar_ai6(side, launch, running, missing):
    """AI6 사이드바: 데이터 점검(15/15/20 · 정답 짝 · PNU · 대응표 어휘) → 공간오케 주소·대기 상한 → 사전 점검 → 실행"""
    from collections import Counter

    import ai6_client as C6
    import ai6_eval as E6
    ts_p, ans_p, map_p = data_path("testset_ai6"), data_path("answer_ai6"), data_path("intent_map_ai6")
    ts_rows = E6.read_rows(ts_p) if ts_p else []
    ans_rows = E6.read_rows(ans_p) if ans_p else []
    try:
        rules, rule_err = (E6.load_rules(map_p) if map_p else E6.load_rules()), ""
    except ValueError as e:                                        # 대응표 형식 오류 → 실행 막음
        rules, rule_err = E6.load_rules(), str(e)
    probs = ([f"❌ {rule_err}"] if rule_err else []) + (E6.validate(ts_rows, ans_rows, rules) if ts_rows else [])
    cnt = Counter(E6.canon_type(r.get("명령유형")) for r in ts_rows)
    if ts_rows:
        side.caption("명령 유형 " + " / ".join(f"{t.split()[0]} {cnt.get(t, 0)}" for t in E6.TYPES) + " (규격 15 / 15 / 20)")
    if probs:
        with side.expander(f"⚠️ 시험 데이터 점검 {len(probs)}건", expanded=bool(rule_err)):
            st.markdown("\n".join(f"- {p}" for p in probs[:60]) + ("\n- …" if len(probs) > 60 else ""))
    import spec_runner_ai6 as R6
    fz_path = os.path.join(DATA, R6.FREEZE_FILE)
    fz = read_json(fz_path) if os.path.exists(fz_path) else {}
    with side.expander("공간오케 · 응답 대기 상한", expanded=False):
        base = st.text_input("공간오케 주소 (지도제어·공간분석 Sub 오케스트레이터)", C6.BASE, key="ai6_base",
                             help="POST {주소}/api/chat 으로 명령을 보내요. 기본값은 환경변수 AI6_BASE")
        dl0 = int(fz.get("deadline_s") or C6.DEADLINE)
        deadline = st.number_input("사례당 응답 대기 상한(초)", 30, 1200, min(1200, max(30, dl0)), 10, key="ai6_deadline",
                                   help="규격서 2-6 시험조건의 사례별 응답 대기 상한(180초) — 넘기면 연결을 끊고 '응답 오류'로 집계 (동결 대상)")
        if not 30 <= dl0 <= 1200:                                  # 범위 밖 기본값(AI6_DEADLINE)이면 화면이 멈추지 않게 맞춤
            st.caption(f"⚠️ 기본값 {dl0}초(AI6_DEADLINE)가 범위(30~1200초) 밖이라 {min(1200, max(30, dl0))}초로 맞췄어요.")
        probe = st.checkbox("사전 점검에 주소 검색 1건 포함 (V-World 경유)", value=True, key="ai6_probe",
                            help=f"공간오케에 시험셋에 없는 명령 1건('{C6.PROBE_MESSAGE}')을 보내 위치 검색이 되는지 확인해요")
        llm_key = st.text_input("LLM 키 (모델 확인용)", C6.llm_key_env(), type="password", key="ai6_llm_key",
                                help=f"LLM 서버 /v1/models 로 지금 띄운 모델이 시험 조건({C6.LLM_MODEL})·공간오케 설정과 같은지 확인해요. "
                                     f"비우면 모델 목록은 못 보고 vLLM 버전(시험 조건 {C6.VLLM_VERSION or '-'})과 공간오케 설정만 확인해요. "
                                     "키는 실행 기록에 남기지 않아요.")
    if side.button("사전 점검", width="stretch", disabled=running,
                   help="공간오케 /health·/ready, LLM, 공간분석 API 응답을 확인해요. 실행할 때도 다시 점검하고, 실패하면 실행하지 않아요."):
        with st.spinner("사전 점검 중…"):
            st.session_state["ai6_precheck"] = dict(at=time.strftime("%H:%M:%S"),
                                                    rows=C6.precheck(base, probe_message=C6.PROBE_MESSAGE if probe else None,
                                                                     llm_key=llm_key),
                                                    llm=C6.llm_info(base, llm_key=llm_key))   # 키는 저장하지 않음(모델 목록·vLLM 버전만)
    pc = st.session_state.get("ai6_precheck")
    if pc:
        bad = [r for r in pc["rows"] if r["결과"] != "정상"]
        side.caption(("✅ 사전 점검 정상" if not bad else f"❌ 사전 점검 실패 {len(bad)}건 — 실행하면 시작하지 않고 멈춰요") + f" · {pc['at']}")
        with side.expander("사전 점검 결과", expanded=bool(bad)):
            show_table(pd.DataFrame(pc["rows"]))
            st.markdown("**동결 버전 확인** (규격 시험방법 (2) — 선언값 = 시스템이 알려 주지 않아 의뢰기업 선언값으로 기록)")
            show_table(pd.DataFrame(C6.version_rows(pc["rows"], llm=pc.get("llm")))[["항목", "시험 조건", "확인값", "결과", "확인 방법"]])
    cur = R6.freeze_record(ts_p, ans_p, map_p, deadline, base) if ts_p and ans_p else None
    diff = R6.freeze_check(fz_path, cur) if cur else None
    if fz:                                                         # 규격: 의도 대응표는 시험 전에 확정, 시험 기간 중 변경 안 함
        side.caption(f"🔒 동결됨 · {fz.get('frozen_at', '')}" + (f" — ❌ 바뀐 항목: {', '.join(diff)} (실행 안 됨)" if diff else
                                                                 " — 시험셋·정답셋·의도 대응표·판정 기준·대기 상한 고정"))
        if side.button("동결 해제", width="stretch", disabled=running, help="시험 기간 중에는 풀지 마세요. 풀고 돌린 실행은 '동결 전 실행(예비)'으로 기록돼요."):
            os.remove(fz_path)
            st.rerun()
    elif side.button("🔒 시험 데이터·대응표 동결", width="stretch", disabled=running or not cur or bool(rule_err),
                     help="예비 실측을 마친 뒤 누르세요. 지금 시험셋·정답셋·의도 대응표·판정 기준·대기 상한의 해시를 저장하고, "
                          "그 뒤로 하나라도 바뀌면 실행을 거부해요."):
        with open(fz_path, "w", encoding="utf-8") as f:
            json.dump({**cur, "frozen_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "files": {"testset": ts_p, "answer": ans_p, "intent_map": map_p or "기본 대응표"}}, f, ensure_ascii=False, indent=1)
        st.rerun()
    ids = side.multiselect("실행할 ID (비우면 전체)", [r.get("사례ID") for r in ts_rows], key="spec_ids_AI6")
    n = len(ids) or len(ts_rows)
    side.caption(f"명령 {n}건 · 예상 약 {max(1, n * 20 // 60)}분 (2026-11-10 실측 명령당 4~19초 + 간격, 최대 {n * int(deadline) // 60}분)"
                 + (" · ID를 골라 돌리면 '예비 실행'으로 기록돼요(공식 결과 아님)" if ids else ""))
    data_block = bool(E6.blocking(probs)) and not ids                 # 데이터 문제가 있으면 공식(전체) 실행은 막고, ID를 고른 예비 실행만
    blocked = bool(missing) or not ts_rows or not ans_rows or bool(rule_err) or bool(diff) or data_block
    run = side.button("⏳ 실행 중…" if running else "AI 6 지도 제어 시험 실행", type="primary", width="stretch",
                      disabled=running or blocked,
                      help="결과는 spec/AI6/<시각>/ 에 저장돼요(명령 해석 로그 · 실행 결과 로그 · 결과표). 백그라운드에서 실행됩니다.")
    if missing:
        side.caption("❌ 표시된 파일을 먼저 올려 주세요 (📂 시험 데이터).")
    if not fz and ts_rows and not ids:
        side.caption("ℹ️ 동결하지 않고 돌린 실행은 '예비'로 기록돼요 — 예비 실측을 마친 뒤 🔒 동결하고 공식 실행하세요.")
    if rule_err:
        side.caption("❌ 의도 대응표 형식이 달라 실행할 수 없어요 — 위 '시험 데이터 점검'을 보세요.")
    elif data_block:
        side.caption("❌ 시험 데이터 점검 문제가 있어 전체(공식) 실행은 막혀요 — 고친 뒤 실행하거나, ID를 골라 예비로 돌려 보세요.")
    if run and not blocked and not running:                        # 비활성 버튼이 눌린 것처럼 와도 한 번 더 막음
        import qa_runner
        cmd = [sys.executable, "-u", S.SPEC["AI6"]["runner"], "--testset", ts_p, "--answer", ans_p,
               "--status-file", qa_runner.STATUS_FILE, "--base", base, "--deadline", str(deadline)]
        if map_p:
            cmd += ["--intent-map", map_p]
        if ids:
            cmd += ["--cases", ",".join(ids)]
        if probe:
            cmd += ["--probe", C6.PROBE_MESSAGE]
        st.session_state["spec_run_dir"] = None
        launch(cmd, n, env={"AI6_LLM_KEY": llm_key, "VLLM_API_KEY": ""})  # 키는 러너 환경변수로만 넘김(명령줄·기록에 안 남김), 비우면 확인 안 함
    if data_path("result_ai6"):
        side.caption("ℹ️ result_ai6(수동 결과 파일)는 쓰지 않아요 — AI6 는 위 버튼으로 공간오케에 직접 보내 판정합니다.")
    return "AI6"


def ai6_detail(run_dir, cid):
    """AI6 상세: 명령 해석 로그 + 실행 결과 로그 + 규격 출력 JSON + 응답 원문"""
    import ai6_eval as E6

    def row(name):                                                 # 같은 ID가 여러 줄이면(재시도) 마지막 줄
        p, found = os.path.join(run_dir, name), {}
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for ln in f:
                    try:
                        d = json.loads(ln)
                    except json.JSONDecodeError:
                        continue
                    if str(d.get("사례ID")) == str(cid):
                        found = d
        return found
    pr, gr = row(E6.PARSE_LOG), row(E6.GIS_LOG)
    if not gr:
        st.info("이 사례의 실행 결과 로그가 없어요.")
        return
    j = lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else ("-" if v in (None, "") else str(v))
    calls = "; ".join(f"{c.get('callee')}:{c.get('operation')} ({c.get('status')}/{c.get('resolve_status') or '-'})"
                      for c in gr.get("호출결과") or [])
    acts = ", ".join(f"{a.get('type')}" + (f"[{a.get('pnu')}]" if a.get("pnu") else "") for a in gr.get("지도제어명령") or []
                     if not str(a.get("type", "")).startswith("UI."))
    geoms = "; ".join(f"{g.get('operation')}: " + ", ".join(f"{k}×{v}" for k, v in (g.get("도형") or {}).items())
                      for g in gr.get("표출도형") or [])
    out = [("명령 유형", gr.get("명령유형")), ("명령문", gr.get("명령문")),
           ("판정", str(gr.get("판정") or "-") + (f" — {gr.get('오류원인')}" if gr.get("오류원인") else "")),
           ("① 의도 / ② 파라미터 / ③ 지도 제어", " / ".join("O" if gr.get(k) else ("-" if gr.get(k) is None else "X")
                                                    for k in ("①의도", "②파라미터", "③지도제어"))),
           ("불일치 내용", " | ".join(gr.get("불일치내용") or []) or "-"),
           ("세부 원인", gr.get("세부원인") or "-"),
           ("참고 (판정 제외)", " | ".join(gr.get("참고") or []) or "-"),
           ("정답 의도", (gr.get("정답") or {}).get("정답의도")), ("해석 의도(대응표 적용)", "; ".join(pr.get("해석의도") or [])),
           ("의도 근거", "; ".join(f"{e.get('의도')} ← {e.get('근거')} {e.get('값')}" for e in pr.get("의도근거") or [])),
           ("정답 파라미터", (gr.get("정답") or {}).get("정답파라미터")), ("핵심 파라미터(시스템)", j(pr.get("핵심파라미터"))),
           ("정답 시나리오", (gr.get("정답") or {}).get("정답시나리오")),
           ("실행 대상 기능(호출)", calls or "-"), ("지도 제어 명령", acts or "-"), ("표출 도형", geoms or "-"),
           ("고른 레이어", ", ".join(f"{c.get('name') or '이름 모름'} ({c.get('lyr_id')})" for c in pr.get("고른레이어") or []) or "-"),
           ("되묻기", "예 — " + j(pr.get("되묻기내용")) if gr.get("되묻기") else "아니오"),
           ("응답 오류", gr.get("응답오류") or "-"), ("소요(초)", gr.get("소요초")), ("thread_id", gr.get("thread_id")),
           ("최종 응답", (gr.get("최종응답") or "-")[:500])]
    show_table(pd.DataFrame(out, columns=["항목", "값"]).astype(str))
    with st.expander("규격 출력 JSON (의도 · 핵심파라미터 · 실행대상기능 · 지도상태)"):
        st.json(pr.get("규격출력") or {})
    raw = os.path.join(run_dir, "raw", f"{cid}.json")
    if os.path.exists(raw):
        with st.expander("응답 원문 (공간오케 SSE 이벤트)"):
            d = read_json(raw)
            evs = (d.get("response") or {}).get("events") or []
            st.caption(f"요청 {j((d.get('response') or {}).get('request'))} · 종료 {(d.get('response') or {}).get('ended_by')} · "
                       f"이벤트 {len(evs)}개")
            st.json(evs, expanded=False)


# ---------------------------------------------------------------- 본문
def overview():
    rows = []
    for it in S.SPEC:
        rs = runs(it)
        if it == "AI6":                                            # 공식 실행이 있으면 그 결과 — ID 선택·다시 판정(예비) 폴더가 덮지 않게
            rs = [d for d in rs if read_json(os.path.join(d, "meta.json")).get("공식", True)] or rs
        pre = bool(rs) and read_json(os.path.join(rs[0], "meta.json")).get("공식", True) is False
        sm = read_json(os.path.join(rs[0], "summary.json")) if rs else {}
        rows.append({"시험항목": f"{S.SPEC[it]['no']} {SHORT[it]}", "지표": S.SPEC[it]["metric"],
                     "진단 결과": fmt_pct(sm.get("value"), it) if sm else "미실행",
                     "목표": target_txt(it),
                     "달성 여부": ("✅ 달성" if sm.get("passed") else "❌ 미달성") if sm.get("value") is not None else "-",
                     "대상": str(sm["n"]) if sm.get("n") is not None else "-",
                     "실행": (os.path.basename(rs[0]) + (" (예비)" if pre else "")) if rs else "-"})
    return pd.DataFrame(rows)


def _read_csv(p):
    try:
        return pd.read_csv(p, encoding="utf-8-sig", dtype=str).fillna("")
    except Exception:                                                # noqa: BLE001
        return None


def _rate(ok, n):
    return f"{int(ok / n * 10000) / 100:.2f} %" if n else "-"


_OOPS_KO = {   # OOPS! 함정 코드 → 뜻(한국어)
    "P04": "다른 요소와 연결되지 않은 요소가 있음", "P08": "라벨·설명(rdfs:label·comment) 주석이 없는 요소가 있음",
    "P10": "클래스 간 서로소(disjoint) 선언이 없음", "P11": "속성에 domain 또는 range 선언이 없음",
    "P13": "역관계(owl:inverseOf)가 선언되지 않은 속성이 있음", "P34": "타입 선언 없이 클래스로 쓰이는 요소가 있음",
    "P41": "라이선스 선언이 없음", "P22": "명명 규칙이 섞여 있음", "P02": "같은 라벨을 가진 서로 다른 요소가 있음",
}


def _da2_cause(i, r):
    """DA2 구성지표별 행 → 문제 원인(문제가 없으면 빈 문자열)"""
    g = lambda c: str(r.get(c, "") if r.get(c, "") is not None else "").strip()
    if g("status") == "error":
        return "실행 오류: " + (g("error_message") or "-")
    if i == 0:                                                              # 2-1 CQ
        if g("match") == "true":
            return ""
        out = []
        if g("missing_bindings"):
            out.append(f"정답 중 못 찾은 조문: {g('missing_bindings')}")
        if g("extra_bindings"):
            out.append(f"정답에 없는 조문이 나옴: {g('extra_bindings')}")
        return " / ".join(out) or "결과 불일치"
    if i == 1:                                                              # 2-2 스키마
        return "" if g("conforms") == "true" else ("부적합: " + (g("message") or g("actual") or "-"))
    if i == 2:                                                              # 2-3 매핑
        if g("exact_match") == "true":
            return ""
        if g("lookup_path"):                                                # 확인 경로(원천 → 대상 IRI → 파일 → 결과)
            return g("lookup_path")
        try:
            ex, ac = json.loads(g("expected_mapping") or "{}"), json.loads(g("actual_mapping") or "{}")
        except Exception:
            return "매핑 불일치"
        if "노드 없음" in str(ac.get("class", "")):
            return f"온톨로지에 대상 노드가 없음 ({g('target_element') or g('source_value')}) — 원천 값 '{g('source_value')}'에 해당하는 노드 누락"
        nm = {"class": "클래스", "property": "속성", "relation": "관계", "datatype": "자료형"}
        diff = [f"{nm[k]} 다름(정답 {ex.get(k) or '-'} / 실제 {ac.get(k) or '-'})" for k in nm if str(ex.get(k, "")) != str(ac.get(k, ""))]
        return " / ".join(diff) or "매핑 불일치"
    if g("detected") == "true" or (g("count") not in ("", "0") and g("check_id").startswith(("CON", "CYC"))):   # 2-4
        code = g("check_id").replace("OOPS-", "")
        what = _OOPS_KO.get(code, g("item"))
        lv = f"[{g('level')}] " if g("level") else ""
        tgt = g("elements")
        try:
            n = len(json.loads(tgt)) if tgt else 0
        except Exception:
            n = 0
        return f"{lv}{what}" + (f" — 대상 {n}개(아래 펼침 표: IRI·파일 위치·비고)" if n else "")
    return ""


def render_da2(item, sp, area):
    """[DA 2] 온톨로지(ODP) 설계 완성도 — 구성지표 2-1 ~ 2-4 결과 (tools/da2_run.py 산출물)"""
    rs = runs(item)
    if not rs:
        with area("box_spec_cases"):
            st.markdown("#### 구성지표 (세부 시험항목 1-1 ~ 1-4)")
            show_table(pd.DataFrame([{"세부 시험항목": f"[{k}]", "구성지표": n, "가중치": f"{w} %",
                                      "목표": (f"{sp['part_target'][k]:.0f} % 이상" if sp["part_target"].get(k) else "미정(○○)"),
                                      "판정 방식": how, "산출식": f, "결과": "미실행"} for k, n, w, how, f in sp["parts"]]))
            st.markdown(f"- 산정식: {sp['formula']}\n- 목표: 온톨로지(ODP) 설계 완성도 {target_txt(item)}\n- 시험 데이터: {sp['data']}")
            st.info("왼쪽 '📂 시험 데이터 (검증 자산)'에서 준비 상태를 확인하고 실행하세요.")
        return
    pick = st.session_state.pop("spec_run_dir", None)
    sk = f"spec_dir_{item}"
    if pick:
        hit = next((d for d in rs if os.path.normpath(d) == os.path.normpath(pick)), None)
        if hit:
            st.session_state[sk] = hit
    if st.session_state.get(sk) not in rs:
        st.session_state[sk] = rs[0]
    run_dir = st.selectbox("결과 폴더", rs, format_func=lambda d: d.replace("\\", "/"), key=sk)
    sm = read_json(os.path.join(run_dir, "summary.json"))
    meta = read_json(os.path.join(run_dir, "meta.json"))
    if not sm:
        st.warning("이 폴더에는 아직 결과가 없어요 (실행 중이거나 중단됨).")
        return
    pr = sm.get("parts", {})
    with area("kpi_spec_main"):
        st.markdown(f"#### {sp['no']} {sp['name']}")
        c = st.columns(4)
        c[0].metric("온톨로지(ODP) 설계 완성도", fmt_pct(sm.get("value"), item) if sm.get("value") is not None else "-")
        c[1].metric("목표", target_txt(item))
        c[2].metric("달성 여부", ("달성" if sm.get("passed") else "미달성") if sm.get("value") is not None else "-")
        c[3].metric("실행 범위", "전체" if sm.get("scope") == "full" else "부분 실행")
        if sm.get("scope") != "full":
            st.caption("부분 실행(구성지표·CQ 일부 선택)이라 종합 점수(가중합)는 계산하지 않았어요.")
        rows = []
        for k, n, w, how, f in sp["parts"]:
            p = pr.get(k, {})
            ok, tot = p.get("ok"), p.get("n")
            rows.append({"세부 시험항목": f"[{k}]", "구성지표": n, "가중치": f"{w} %",
                         "결과": fmt_pct(p.get("value")) if p.get("value") is not None else ("오류" if p else "미실행"),
                         "목표": (f"{sp['part_target'][k]:.0f} % 이상" if sp["part_target"].get(k) else "미정(○○)"),
                         "달성 여부": ("-" if not sp["part_target"].get(k) or p.get("value") is None
                                    else ("✅ 달성" if p["value"] >= sp["part_target"][k] else "❌ 미달성")),
                         "일치(적합)/전체": f"{ok} / {tot}" if tot else "-", "오류": str(p.get("error", "-")) if p else "-",
                         "산출식": f})
        show_table(pd.DataFrame(rows))
        st.caption(f"산정식: {sp['formula']} · 종합 96 % 이상, 구성지표별 목표는 2-1 ~ 2-4 각 96 % 이상 · 결과는 소수점 셋째자리 이하 버림")

    tabs = st.tabs([f"{k} {n}" for k, n, *_r in sp["parts"]] + ["검증 자산·도구"])
    for i, (k, n, *_r) in enumerate(sp["parts"]):
        with tabs[i]:
            d = os.path.join(run_dir, f"da2-{i + 1}")
            p = os.path.join(d, "validation_result_da2.csv")
            df = _read_csv(p) if os.path.exists(p) else None
            if df is None:
                st.info("이 실행에서는 수행하지 않았어요.")
                continue
            st.caption(f"로그 파일: `{p.replace(os.sep, '/')}`")
            st.download_button("⬇ [로그] validation_result_da2.csv", open(p, "rb").read(), file_name=f"validation_result_da2_{k.replace(' ', '')}.csv",
                               mime="text/csv", key=f"dl_da2_{i}")
            df = df.copy()
            df["원인"] = [_da2_cause(i, r) for _, r in df.iterrows()]
            bad = df["원인"] != ""
            only = st.toggle(f"문제 사례만 보기 (문제 {int(bad.sum())}건)", key=f"da2_only_{i}")
            full = df
            if only:
                df = df[bad]
                if df.empty:
                    st.success("문제 사례가 없어요.")
            if i == 0:                                                      # 2-1 CQ: ODP 유형별 일치·불일치
                g = full.groupby("odp_type")["match"].agg(lambda s: (s == "true").sum()).rename("일치").to_frame()
                g["전체"] = full.groupby("odp_type").size()
                g["불일치"] = g["전체"] - g["일치"]
                g["일치율"] = [_rate(a, b) for a, b in zip(g["일치"], g["전체"])]
                show_table(g.reset_index().rename(columns={"odp_type": "ODP 유형"}))
                show_table(df.rename(columns={"cq_id": "CQ ID", "odp_type": "ODP 유형", "municipality": "지자체", "question": "질문",
                                              "expected_bindings": "정답", "actual_bindings": "실제 결과", "missing_bindings": "누락",
                                              "extra_bindings": "추가", "match": "일치"})[
                    ["CQ ID", "ODP 유형", "지자체", "질문", "원인", "정답", "실제 결과", "누락", "추가", "일치", "status", "error_message"]])
                with st.expander("CQ별 SPARQL 질의"):
                    import zipfile
                    zp = os.path.join(run_dir, "data", "aips_da2_cq_testset.zip")
                    if os.path.exists(zp):
                        with zipfile.ZipFile(zp) as z:
                            for _, r in df.iterrows():
                                st.markdown(f"**{r['cq_id']}** {r['question']}")
                                st.code(z.read(r["query_file"]).decode("utf-8"), language="sparql")
            elif i == 1:                                                    # 2-2 스키마: 항목군별 적합·부적합
                g = full.groupby("item_group")["conforms"].agg(lambda s: (s == "true").sum()).rename("적합").to_frame()
                g["전체"] = full.groupby("item_group").size()
                g["부적합"] = g["전체"] - g["적합"]
                g["적합률"] = [_rate(a, b) for a, b in zip(g["적합"], g["전체"])]
                show_table(g.reset_index().rename(columns={"item_group": "항목군"}))
                show_table(df.rename(columns={"check_id": "평가항목", "item_group": "항목군", "description": "점검 내용", "method": "방법",
                                              "actual": "결과", "conforms": "적합", "focus_node": "위반 노드", "message": "내용"})[
                    ["평가항목", "항목군", "점검 내용", "원인", "방법", "결과", "적합", "위반 노드", "내용", "status", "error_message"]])
            elif i == 2:                                                    # 2-3 매핑: 검증 단위별 일치·불일치
                units = [("class_match", "클래스 매핑"), ("property_match", "속성 매핑"), ("relation_match", "관계 매핑"),
                         ("datatype_match", "자료형"), ("exact_match", "정확 매핑(4개 모두)")]
                show_table(pd.DataFrame([{"검증 단위": nm, "일치": int((full[c] == "true").sum()), "불일치": int((full[c] == "false").sum()),
                                          "일치율": _rate(int((full[c] == "true").sum()), len(full))} for c, nm in units]))
                if "value_match" in full:
                    vm = full[full["value_match"] != ""]
                    st.caption(f"(보조) 값 일치 {int((vm['value_match'] == 'true').sum())} / {len(vm)} — 판정에는 넣지 않음. "
                               "원천 최신본과 온톨로지 판 차이 등을 드러내기 위한 참고 값")
                show_table(df.rename(columns={"mapping_id": "매핑 ID", "source_field": "원천 필드", "source_value": "원천 값",
                                              "target_element": "대상 요소", "expected_mapping": "정답 매핑", "actual_mapping": "실제 매핑",
                                              "exact_match": "정확 매핑", "value_match": "(보조) 값 일치", "actual_value": "온톨로지 값",
                                              "lookup_path": "확인 경로", "source_key": "원천 레코드"}).reindex(
                    columns=["매핑 ID", "원천 필드", "원천 값", "원인", "원천 레코드", "대상 요소", "정답 매핑", "실제 매핑", "class_match",
                             "property_match", "relation_match", "datatype_match", "정확 매핑", "(보조) 값 일치", "온톨로지 값"], fill_value=""))
            else:                                                           # 2-4 일관성·무결성
                s4 = read_json(os.path.join(d, "summary_da2_4.json"))
                if s4:
                    show_table(pd.DataFrame([
                        {"구분": "일관성 (OWL Reasoner)", "내용": f"consistent={s4.get('consistent')}, unsatisfiable class {s4.get('unsatisfiable')}개, "
                                                         f"계층 순환 {sum((s4.get('cycles') or {}).values())}건", "오류 건수": str(s4.get("error_count")),
                         "점수": str(s4.get("consistency_score"))},
                        {"구분": "무결성 (OOPS! 함정)", "내용": " · ".join(f"{k} {v}개" for k, v in (s4.get("pitfall_types") or {}).items()),
                         "오류 건수": str(sum((s4.get("pitfall_types") or {}).values())), "점수": str(s4.get("pitfall_score"))},
                        {"구분": "논리적 일관성·무결성", "내용": "(일관성 점수 + 함정 점수) / 2", "오류 건수": "", "점수": str(s4.get("value"))}]))
                show_table(df.rename(columns={"check_id": "항목", "category": "구분", "item": "내용", "level": "중요도", "detected": "검출",
                                              "count": "건수", "affected": "영향 요소 수(계수 안 함)", "elements": "대상",
                                              "location": "대상 위치(IRI @ 파일:줄)"}).reindex(
                    columns=["항목", "구분", "내용", "중요도", "원인", "검출", "건수", "영향 요소 수(계수 안 함)",
                             "대상", "status", "error_message"], fill_value=""))
                for _, r in df[df["check_id"].astype(str).str.startswith("OOPS")].iterrows():
                    try:
                        locs = json.loads(r.get("location") or "[]")
                    except Exception:
                        locs = []
                    if locs:
                        aff = str(r.get("affected") or "").strip()
                        cnt = f"대상 요소 {aff}개" + (f"({len(locs)}줄 — 역관계 후보 쌍은 한 줄)" if aff and aff != str(len(locs)) else "")
                        with st.expander(f"{r['check_id']} {cnt} — 전체 IRI와 스키마 파일 위치 (감점은 함정 유형 1개당 1번)"):
                            show_table(pd.DataFrame([
                                {"대상(IRI)": x["iri"], "위치(파일:줄)": x["loc"], "종류": x.get("kind", ""), "라벨": x.get("label", ""),
                                 "비고(원인·고치는 법)": x.get("note", "")} if isinstance(x, dict)
                                else {"대상(IRI)": x.split(" @ ")[0], "위치(파일:줄)": x.split(" @ ")[-1]} for x in locs]))
                op = os.path.join(d, "oops_response.xml")
                if os.path.exists(op):
                    st.download_button("⬇ OOPS! 응답 원문", open(op, "rb").read(), file_name="oops_response.xml", key="dl_oops")
    with tabs[-1]:
        show_table(pd.DataFrame([{"검증 자산": k, "파일": v.get("path"), "크기(KB)": f"{v.get('bytes', 0) / 1024:,.0f}", "SHA-256": v.get("sha256")}
                                 for k, v in (meta.get("assets") or {}).items()]))
        show_table(pd.DataFrame([{"도구": k, "버전": v} for k, v in (meta.get("tools") or {}).items()]))
        st.caption(f"시작 {meta.get('started', '-')} · 구성지표 {meta.get('parts')} · CQ 선택 {meta.get('ids') or '전체'}")


def render(item, area, help_fn=None, live=None):                  # live = 지금 실행 중인 결과 폴더
    sp = S.SPEC[item]
    with area("box_spec_overview"):
        st.markdown("#### 성과지표 현황 (AI 1 ~ AI 7, DA 2)")
        ov = overview()
        st.dataframe(ov.style.map(lambda v: "background-color: #eff6ff; color: #1d4ed8; font-weight: 700"   # 진단 결과 열 = 파랑
                                  if v != "미실행" else "background-color: #eff6ff; color: #94a3b8", subset=["진단 결과"]),
                     hide_index=True, width="stretch")

    if sp.get("da2"):
        render_da2(item, sp, area)
        return

    rs = runs(item)
    if not rs:
        st.info(f"아직 {SHORT[item]} 시험 결과가 없어요. 왼쪽에서 시험 데이터를 올리고 실행하세요. "
                f"(데이터: {sp['data']})")
        with st.expander("성과지표 시험 기준", expanded=True):
            st.markdown(f"**{sp['no']} {sp['name']}**\n\n- 지표: {sp['metric']} (목표 {target_txt(item)})\n"
                        f"- 산정식: {sp['formula']}\n- 시험 데이터: {sp['data']}\n\n{CRITERIA[item]}")
        return
    pick = st.session_state.pop("spec_run_dir", None)              # 방금 끝난 실행 → 그 폴더를 보여줌
    sk = f"spec_dir_{item}"
    if pick:
        pick = os.path.normpath(pick)
        hit = next((d for d in rs if os.path.normpath(d) == pick), None)
        if hit:
            st.session_state[sk] = hit
    if st.session_state.get(sk) not in rs:
        st.session_state[sk] = rs[0]
    hc = st.columns([3, 1], vertical_alignment="bottom")
    c1, c2 = hc[0], hc[1]
    run_dir = c1.selectbox("결과 폴더", rs, format_func=lambda d: d.replace("\\", "/"), key=sk)
    r, meta, (ans, ts, law) = score_run(item, run_dir)
    auto6 = item == "AI6" and S.log_name(run_dir, item) == sp["log"]       # AI6 자동 실행 폴더 (결과 파일 가져오기가 아님)
    busy = bool(live) and os.path.abspath(str(live)) == os.path.abspath(run_dir)   # 이 폴더가 아직 실행 중 → 다시 저장·다시 판정 막음
    if r is None:
        st.warning("이 폴더에는 아직 로그가 없어요 (실행 중이거나 중단됨).")
        return
    if not auto6 and c2.button("💾 결과 파일 다시 저장", width="stretch", disabled=busy, help="현재 정답셋으로 다시 채점해 result.xlsx / summary.json 저장"):
        import spec_runner
        spec_runner.run_score(item, run_dir, ans, ts, law)
        st.toast("저장했어요")
    if auto6 and c2.button("🔁 다시 판정", width="stretch", disabled=busy,
                              help="공간오케에 다시 묻지 않고, 저장된 응답 원문을 지금 spec_data 의 정답셋·의도 대응표로 다시 판정해 "
                                   "새 폴더 '<이 폴더>_재판정_<시각>'에 '예비'로 남겨요. 이 실행 폴더(공식 결과)는 바뀌지 않아요."):
        import spec_runner_ai6
        new, _ = spec_runner_ai6.rescore(run_dir, data_path("answer_ai6"), None, data_path("intent_map_ai6"))
        st.session_state["spec_run_dir"] = new
        st.cache_data.clear()
        st.toast(f"다시 판정 결과를 새 폴더에 남겼어요: {os.path.basename(new)}")
        st.rerun()

    # ---- 핵심 지표
    log = S.read_log(run_dir, item)
    builds = sorted({str(x) for x in log.get("build", pd.Series(dtype=str)).dropna()})
    contracts = sorted({str(x) for x in log.get("contract_version", pd.Series(dtype=str)).dropna()})
    key_col = "질의ID" if "질의ID" in log.columns else "사례ID"
    err_rows = []                                                           # 통신 오류 + 응답 중단 (종류·끊긴 위치)
    for _, rr in log.iterrows():
        kind, where = S.error_detail(rr, run_dir)
        if kind:
            err_rows.append({key_col: rr[key_col], "회차": rr.get("회차"), "질의문": rr.get("질의문"),
                             "오류 종류": kind, "발생 위치": where, "소요(초)": rr.get("소요초")})
    errs = len(err_rows)
    with area("kpi_spec_main"):
        st.markdown(f"#### {sp['no']} {sp['name']}")
        if busy:
            st.info("실행 중인 폴더예요 — 아래 지표는 지금까지 받은 결과로 낸 중간 값이에요(AI 6 은 아직 보내지 않은 사례를 응답 오류로 셈). "
                    "끝나면 다시 계산돼요.")
        k = st.columns([1.3, 1, 1, 1, 1])
        k[0].metric(sp["metric"], fmt_pct(r["value"], item), help=f"**{sp['formula']}**\n\n{trunc_txt(item)}")
        k[1].metric("목표", target_txt(item))
        k[2].metric("달성 여부", "달성" if r["passed"] else "미달성")
        k[3].metric("수행 건수", r["n"], help="AI3은 질의 수 × 반복 횟수")
        if sp.get("cmd"):
            k[4].metric("오류 건수", int((~r["cases"]["정상수행"]).sum()) if r["n"] else 0, help="의도·파라미터·실행 중 하나라도 불일치")
        elif errs:                                                    # 응답 오류는 발생했을 때만 표시
            k[4].metric("응답 오류", errs, help="통신 오류·시간 초과·응답 중단(판정 없이 끝난 응답) — 모두 판단불가로 집계, "
                                                "'이어서 실행'을 누르면 이 건만 다시 질의")
        aux = r.get("aux") or {}
        if aux:
            a = st.columns(max(len(aux), 2))
            for i, (kk, vv) in enumerate(aux.items()):
                a[i].metric(kk + " (보조)", fmt_pct(vv) if "%" in kk else vv)
        st.caption(f"산정식: {sp['formula']}  ·  결과는 {trunc_txt(item)}")

    # ---- 시험 환경 (재현성)
    with area("box_spec_env"):
        st.markdown("#### 시험 환경 · 데이터 무결성")
        cur = [(p, S.sha256(p), meta.get(k)) for p, k in ((ans, "answer_sha256"), (ts, "testset_sha256"), (law, "lawdb_sha256")) if p]
        env = [("시험 일시", meta.get("started", "-") + (f" (이어서 {meta['resumed']})" if meta.get("resumed") else "")),
               ("대상 시스템", meta.get("base", "-")),
               ("시스템 빌드 / 계약 버전", f"{', '.join(builds) or '-'} / {', '.join(contracts) or '-'}"),
               ("로그 파일", os.path.join(run_dir, sp["log"]).replace("\\", "/")),
               ("시험셋 SHA-256", f"{meta.get('testset_sha256') or '-'}  ({meta.get('testset') or '-'})"),
               ("정답셋 SHA-256", "해당 없음 — AI3은 정답셋 없이 1회차 로그를 기준으로 비교" if item == "AI3"
                else f"{meta.get('answer_sha256') or '-'}  ({meta.get('answer') or '-'})")]
        if item == "AI5":
            env.append(("조문 DB SHA-256", f"{meta.get('lawdb_sha256') or '미제공'}  ({meta.get('lawdb') or '-'})"))
        if item == "AI3":
            env.append(("반복 횟수", meta.get("repeat", "-")))
        if auto6:
            import ai6_eval as E6
            rd, rsc = meta.get("ready") or {}, meta.get("rescored")
            rsc = rsc[-1] if isinstance(rsc, list) and rsc else rsc
            env = [env[0],
                   ("대상 시스템 (공간오케)", meta.get("endpoint") or meta.get("base") or "-"),
                   ("LLM 모델 (공간오케 /ready)", rd.get("LLM 모델") or "-"),
                   ("LLM 모델 (LLM 서버 확인)", (meta.get("llm") or {}).get("내용") or "기록 없음 (사전 점검 생략 또는 이전 실행)"),
                   ("vLLM 버전 (LLM 서버 /version)", (meta.get("llm") or {}).get("vLLM") or "기록 없음 (사전 점검 생략 또는 이전 실행)"),
                   ("연결 에이전트", rd.get("연결 에이전트") or "-"),
                   ("사례당 응답 대기 상한", f"{meta.get('deadline_s', '-')}초 (넘기면 연결을 끊고 응답 오류)"),
                   ("실행 사례", f"선택 {len(meta['cases'])}건" if meta.get("cases") else "시험셋 전체"),
                   ("로그 파일", " · ".join(os.path.join(run_dir, n).replace("\\", "/") for n in (E6.PARSE_LOG, E6.GIS_LOG))),
                   env[-2], env[-1],
                   ("의도 대응표 SHA-256", f"{meta.get('intent_map_sha256') or '-'}  ({meta.get('intent_map') or '기본 대응표 (ai6_eval.DEFAULT_RULES)'})")]
            if meta.get("완료전") and busy:
                kind = "공식 조건으로 실행 중 — 모두 마치면 공식으로 기록 (지금 값은 중간 값)"
            elif meta.get("공식", True):
                kind = "공식"
            else:
                kind = "예비 — " + ", ".join(meta.get("예비사유") or []) + " (공식 결과로 쓰지 않음)"
                if meta.get("완료전"):                                 # 멈춘 공식 조건 실행 — 이어서 끝까지 돌리면 공식이 되나
                    import spec_runner_ai6 as R6
                    lost = R6.resume_outlook(run_dir)
                    kind += (f" · 이어서 돌려도 공식 아님 — 응답을 기록 못 한 사례 {len(lost)}건({', '.join(lost[:3])})은 다시 보내지 않으므로 전체 재수행"
                             if lost else " · 이어서 끝까지 돌리면 공식 (진행 창의 '↻ 이어서')")
            env.insert(1, ("실행 구분", kind))
            env.append(("판정 기준 지문", f"{str(meta.get('criteria_sha256') or '-')[:16]}… (ai6_eval 판정 규칙·옵션)"))
            env.append(("동결", ("예 — 동결된 시험셋·정답셋·대응표·판정 기준·대기 상한으로 실행" if meta.get("frozen")
                                else "아니오 (동결 전 실행)")
                        + (" · 이 폴더는 다시 판정 — 그때의 정답셋·대응표·판정 기준으로 매김(아래 '다시 판정' 행)" if rsc else "")))
            if rsc:
                env.append(("다시 판정", f"{rsc.get('at')} — 정답셋 {str(rsc.get('answer_sha256') or '-')[:16]}… · "
                                       f"대응표 {str(rsc.get('intent_map_sha256') or '기본')[:16]}…"
                                       + (f" (원본 실행 폴더 {meta['원본']})" if meta.get("원본") else "")))
        elif sp.get("cmd"):
            env = [x for x in env if x[0] not in ("대상 시스템", "시스템 빌드 / 계약 버전")]
            env.insert(1, ("실행 결과 파일 SHA-256", f"{meta.get('result_sha256') or '-'}  ({meta.get('result_file') or '-'})"))
        _env = pd.DataFrame(env, columns=["항목", "값"])
        show_table(_env)
        if auto6 and meta.get("precheck"):
            rp = (meta.get("resume_precheck") or [{}])[-1]                  # 이어서 실행했으면 그때 다시 한 점검이 기준
            pre = rp.get("rows") or meta["precheck"]
            import ai6_client as C6
            st.markdown("**동결 버전 확인** (규격 시험방법 (2) — 실행 때 사전 점검 · 조회값 기준, 선언값 = 시스템이 알려 주지 않아 의뢰기업 선언값으로 기록)")
            show_table(pd.DataFrame(C6.version_rows(pre, rp.get("ready"), rp.get("llm")) if rp.get("rows")
                                    else C6.version_rows(pre, meta.get("ready"), meta.get("llm"))))
            bad = [p for p in pre if p.get("결과") != "정상"]
            with st.expander("실행 전 사전 점검 결과" + (f" (이어서 실행 {rp.get('at')})" if rp.get("rows") else "")
                             + (f" — ❌ 실패 {len(bad)}건" if bad else " — 정상"), expanded=bool(bad)):
                show_table(pd.DataFrame(pre))
        if auto6 and meta.get("data_check"):
            with st.expander(f"⚠️ 실행 당시 시험 데이터 점검 {len(meta['data_check'])}건"):
                st.markdown("\n".join(f"- {p}" for p in meta["data_check"][:60]))
        changed = [p for p, h, h0 in cur if h and h0 and h != h0]
        if changed:
            st.warning("실행 이후 바뀐 파일로 채점 중이에요: " + ", ".join(changed) + " → 해시가 실행 당시와 달라요.")
        if not sp.get("cmd"):
            with st.expander("시스템 미제공 항목 처리"):
                st.markdown("\n".join(f"- {x}" for x in NOT_PROVIDED))
        elif auto6:
            with st.expander("시스템 미제공 항목 처리 (동결 버전 확인)"):
                st.markdown("\n".join(f"- {x}" for x in NOT_PROVIDED_AI6))

    # ---- 결과 표
    def pct_view(df):                          # 화면 표시용: '(%)' 컬럼 → '60.00 %' (헤더의 '(%)'는 뺌, 값 없음은 '-')
        dec = sp.get("dec", 2)
        df = df.copy()
        for c in [c for c in df.columns if isinstance(c, str) and "(%)" in c]:
            df[c] = df[c].map(lambda v: "-" if v is None or (isinstance(v, float) and pd.isna(v)) or v == ""
                              else f"{float(v):.{dec}f} %" if isinstance(v, (int, float)) else str(v))
            df = df.rename(columns={c: c.replace("(%)", "").strip()})
        return df

    with area("box_spec_tables"):
        st.markdown("#### 결과")
        if isinstance(r.get("confusion"), pd.DataFrame) and not r["confusion"].empty:
            cc = st.columns([1, 1.25], gap="medium")                 # 왼쪽 혼동행렬 · 오른쪽 판단 유형별 결과
            cc[0].markdown("**2 × 2 혼동 행렬** (행 = 정답, 열 = AI 판단 결과)")
            conf = r["confusion"]
            cs = r.get("cases")
            gcol = next((c for c in ("정답_판단유형", "정답_판정결과") if isinstance(cs, pd.DataFrame) and c in cs), None)
            if err_rows and gcol and key_col in cs:                       # 판단불가 열 = 응답 오류 → 정답 유형별 오류원인
                kind_of = {str(e[key_col]): e["오류 종류"].split(" — ")[0] for e in err_rows}
                ec = cs[cs[key_col].astype(str).isin(kind_of)]
                cause = {g: " · ".join(f"{k} {v}" for k, v in                   # 칸이 좁으면 · 에서 줄바꿈
                                       ec[ec[gcol] == g][key_col].astype(str).map(kind_of).value_counts().items())
                         for g in conf.index}
                conf = conf.copy()
                conf["오류원인"] = [cause.get(g) or "-" for g in conf.index]
            ct = conf.reset_index().rename(columns={"index": "정답_판단유형"})   # 행마다 글자 양만큼만 높이 (일반 표)
            show_table(ct, cc[0])
            if err_rows:
                cc[0].caption("판단불가 = 응답 오류 (시스템 판정을 받지 못해 오답으로 집계)")
            first = list(r["tables"].items())[0]
            ftab = pct_view(first[1])
            if err_rows:                                                 # 응답 오류 = 혼동행렬의 '판단불가' 열
                gcol = next((c for c in ("정답_판단유형", "정답_판정결과") if isinstance(cs, pd.DataFrame) and c in cs), None)
                tcol = next((c for c in ("판단유형", "판정결과") if c in ftab), None)
                if gcol and tcol and key_col in cs:
                    ek = {str(e[key_col]) for e in err_rows}
                    per = cs[cs[key_col].astype(str).isin(ek)][gcol].value_counts()
                    ftab = ftab.copy()
                    ecnt = [int(per.sum()) if t == "계" else int(per.get(t, 0)) for t in ftab[tcol]]
                    if "불일치" in ftab:                         # 불일치 = 오판 + 응답 오류 → 일치 + 오판 + 응답 오류 = 정답건수
                        i = list(ftab.columns).index("불일치")
                        ftab.insert(i, "불일치(오판)", [int(n) - e for n, e in zip(ftab["불일치"], ecnt)])
                        ftab["불일치(응답 오류)"] = ecnt                  # 맨 끝 (일치율 다음)
                        ftab = ftab.drop(columns=["불일치"])
                    else:
                        ftab["응답 오류"] = ecnt
            cc[1].markdown(f"**{first[0]}**")
            show_table(ftab, cc[1])
            if "불일치(오판)" in ftab:
                cc[1].caption("정답건수 = 일치 + 불일치(오판: 판정이 정답과 다름) + 불일치(응답 오류: 판정을 받지 못함)")
            rest = list(r["tables"].items())[1:]
        else:
            rest = list(r["tables"].items())
        rest = [(name, pct_view(df) if isinstance(df, pd.DataFrame) else df) for name, df in rest
                if name not in HIDE_TABLES.get(item, ())]          # 화면에서만 숨김 (result.xlsx에는 남음)
        for name, df in rest:
            st.markdown(f":orange[⚠️ **{name}**] — 시스템 판정을 받지 못해 판단불가(오답)로 집계된 건"
                        if name.startswith("응답 오류") else f"**{name}**")
            if "O 일치" in name and not df.empty:
                st.dataframe(df.style.map(lambda v: "background-color: rgba(239,68,68,.18)" if v == "X" else "",
                                          subset=[c for c in df.columns if c != "사례ID"]), hide_index=True, width="stretch")
            elif name.startswith("응답 오류"):                        # 건수가 적은 표 → 행마다 글자 양만큼만 높이
                show_table(df)
            else:
                show_table(df)
            if name.startswith("응답 오류"):
                st.caption("왼쪽 사이드바의 '이어서 실행'을 누르면 이 건만 다시 질의합니다. 연결 끊김·응답 중단은 시스템(AIPS) 쪽 문제라 "
                           "같은 시간대 서버 재배포·과부하 여부를 개발팀에 확인하세요.")
        with st.expander("성과지표 시험 기준", expanded=True):
            st.markdown(f"- 지표: {sp['metric']} (목표 {target_txt(item)})\n- 산정식: {sp['formula']}\n"
                        f"- 시험 데이터: {sp['data']}\n\n{CRITERIA[item]}")

    # ---- 사례별
    with area("box_spec_cases"):
        st.markdown("#### 질의별 결과")
        cases = r["cases"]
        flag = {"AI1": "판단_일치", "AI2": "일치", "AI3": "일치", "AI4": "적합", "AI5": "환각",
                "AI6": "정상수행", "AI7": "정상수행"}[item]
        only = st.toggle("문제 사례만 보기", key=f"spec_only_{item}")
        view = cases
        if only and flag in cases:
            view = cases[cases[flag] == (item == "AI5")] if item == "AI5" else cases[~cases[flag].astype(bool)]
        view = view.drop(columns=[c for c in HIDE_CASE_COLS if c in view])      # 화면에서만 숨김 (result.xlsx엔 유지)
        if "참조_오류" in view:                  # 룰엔진(8777) 참조 정보 조회 실패 여부 — AIPS 응답 오류와는 무관
            view = view.copy()
            view["참조_오류"] = view["참조_오류"].map(lambda v: v if isinstance(v, str) and v.strip() else "없음 (조회 정상)")
            view = view.rename(columns={"참조_오류": "참조_조회오류"})
        if err_rows and key_col in view:                                          # 응답 오류 칸: 판단유형 바로 옆
            emap = {str(e[key_col]): f"{e['오류 종류'].split(' — ')[0]} · {e['발생 위치']}" for e in err_rows}
            view = view.copy()
            pos = list(view.columns).index("판단유형") + 1 if "판단유형" in view else len(view.columns)
            view.insert(pos, "응답 오류", view[key_col].astype(str).map(emap).fillna(""))
        if auto6:                                                   # ①②③ → O / X / -(미실행), 되묻기 → 예
            view = view.drop(columns=["정상수행"], errors="ignore").copy()
            for c in ("①의도", "②파라미터", "③지도제어"):
                if c in view:
                    view[c] = view[c].map(lambda v: "O" if v is True else "X" if v is False else "-")
            if "되묻기" in view:
                view["되묻기"] = view["되묻기"].map(lambda v: "예" if v is True else "")
            st.caption("① 의도 · ② 핵심 파라미터 · ③ 지도 제어 — 셋 다 O 여야 정상 수행. 오류 원인은 ①→②→③ 순으로 처음 틀린 단계 "
                       "(응답이 없거나 대기 상한을 넘기면 응답 오류, 되묻기는 오류).")
        show_table(view, height=rows_height(len(view), cap=560))
        logs = [S.log_name(run_dir, item)]
        if auto6:
            import ai6_eval as E6
            logs = [E6.PARSE_LOG, E6.GIS_LOG]
        dl = st.columns(1 + len(logs))
        rx = os.path.join(run_dir, "result.xlsx")
        if os.path.exists(rx):
            with open(rx, "rb") as f:
                dl[0].download_button("⬇ 결과 엑셀 (result.xlsx)", f.read(), file_name=f"{item}_result.xlsx", width="stretch")
        for i, name in enumerate(logs, 1):
            lp = os.path.join(run_dir, name)
            if os.path.exists(lp):
                with open(lp, "rb") as f:
                    dl[i].download_button(f"⬇ 로그 ({name})", f.read(), file_name=name, width="stretch", key=f"dl_{item}_{name}")

    # ---- 상세
    with area("box_spec_detail"):
        st.markdown("#### 질의별 상세 결과 · 판단 프로세스 로그" if item == "AI3" else "#### 질의별 상세 결과")
        key = "질의ID" if item == "AI2" else "사례ID"
        if log.empty or key not in log.columns:                    # 로그 파일은 있는데 읽을 줄이 없음(빈 로그 · 깨진 줄만)
            st.info("판정 로그에 읽을 수 있는 줄이 없어 상세 결과를 보여 줄 수 없어요.")
            return
        ids = list(dict.fromkeys(log[key].astype(str)))
        qcol = next((c for c in ("질의문", "명령문") if c in log.columns), None)
        qtext = dict(zip(log[key].astype(str), log[qcol].astype(str))) if qcol else {}
        cid = st.selectbox("상세 결과를 볼 질의를 선택하세요 ▼", ids, key=f"spec_case_{item}",
                           format_func=lambda i: f"{i}  ·  {qtext[i][:70]}" if qtext.get(i) else i,
                           help="목록을 펼쳐 질의를 고르면 아래에 시스템 출력 항목(판단 유형·근거 법령·근거 조문 등)이 나옵니다.")
        recs = log[log[key].astype(str) == cid].sort_values("회차")
        rec = recs.iloc[0]
        if auto6:
            ai6_detail(run_dir, cid)
            return
        if sp.get("cmd"):
            c = r["cases"].set_index("사례ID").loc[cid] if cid in set(r["cases"]["사례ID"]) else {}
            out = [("명령 유형", rec.get("명령유형")), ("명령문", rec.get("명령문")),
                   ("해석 의도", rec.get("해석의도")), ("해석 파라미터", rec.get("해석파라미터")),
                   ("실행 대상 기능", rec.get("실행대상기능")), ("실행 결과(지도·3D 상태)", rec.get("실행결과")),
                   ("실행 상태", rec.get("실행상태")), ("판정", "정상 수행" if c.get("정상수행") else f"오류 — {c.get('오류원인', '')}"),
                   ("불일치 내용", c.get("불일치 내용") or "-"), ("캡처 파일", rec.get("캡처파일") or "-")]
            _o = pd.DataFrame(out, columns=["항목", "값"]).astype(str)
            show_table(_o)
            cap = str(rec.get("캡처파일") or "")
            for cp in (cap, os.path.join(run_dir, cap), os.path.join(DATA, "captures", cap)):
                if cap and os.path.isfile(cp):
                    st.image(cp, caption=f"화면 캡처 · {cap}")
                    break
            return
        raw_v = str(rec.get("원판정") or "")

        def none_why(what):                     # 값이 없는 이유: 불가 판정이면 시스템이 이 단계를 진행하지 않음
            if S.error_text(rec):
                return f"없음 — 응답 오류로 {what} 받지 못함"
            if rec.get("판단유형") == "허가 불가능":
                return f"없음 — 허가 불가 판정이라 시스템이 {what} 제시하지 않음"
            return f"없음 — 시스템이 {what} 제시하지 않음"
        out = [("판단 유형", rec.get("판단유형")),
               ("시스템 원 판정", f"{RAW_VERDICT[raw_v]} ({raw_v})" if raw_v in RAW_VERDICT else raw_v or "-"),
               ("근거 법령", "; ".join(rec.get("적용법령") or []) or "-"),
               ("근거 조문 (법령ID.조.항)", "; ".join(rec.get("근거조문") or []) or "-"),
               ("적용 룰셋", "; ".join(rec.get("적용룰셋") or []) or "미제공"),
               ("단서 조건 근거 조문", "; ".join(rec.get("단서조건근거조문") or []) or "-"),
               ("보완사항", rec.get("보완사항") or none_why("보완 제안을")),
               ("필요 행정절차", "; ".join(rec.get("필요행정절차") or []) or none_why("인허가 절차 안내를")),
               ("설명문", rec.get("설명문") or "-"), ("추적 ID", rec.get("trace_id") or "-"),
               ("소요(초)", rec.get("소요초")), ("오류", " · ".join(x for x in S.error_detail(rec, run_dir) if x) or "없음 (정상 응답)")]
        if item == "AI5" and "환각사유" in r["cases"]:          # 환각 판정과 사유 (정답 조문셋과 비교한 결과)
            c5 = r["cases"].set_index("사례ID")
            if cid in c5.index:
                c5 = c5.loc[cid]
                out[:0] = [("환각 판정", "환각" if c5["환각"] else "정상 응답"),
                           ("환각 사유", c5["환각사유"] if c5["환각"] else "-"),
                           ("정답 조문셋", c5["정답조문"] or "-"), ("정답 적용 법령", c5["정답_적용법령"] or "-")]
        if item == "AI4" and "부적합사유" in r["cases"]:        # 적합 판정과 부적합 사유 (정답과 비교한 결과)
            c4 = r["cases"].set_index("사례ID")
            if cid in c4.index:
                c4 = c4.loc[cid]
                out[:0] = [("적합 판정", "적합" if c4["적합"] else "부적합"),
                           ("부적합 사유", c4["부적합사유"] if not c4["적합"] else "-"),
                           ("예외 유형", c4.get("예외유형") or "-"),
                           ("정답 판정 결과", c4["정답_판정결과"]),
                           ("정답 단서 조건 근거 조문", c4["정답_근거조문"] or "-"),
                           ("정답 보완사항", c4.get("정답_보완사항") or "-"),
                           ("정답 필요 행정절차", c4.get("정답_필요행정절차") or "-")]
        _o = pd.DataFrame(out, columns=["출력 항목", "값"]).astype(str)
        show_table(_o)
        steps = rec.get("단계로그") or []
        if item == "AI3" and len(recs) > 1:
            st.markdown("**6단계 로그 회차별 비교** (기준 = 1회차, 다르면 붉게)")
            comp = []
            for s_i, name in enumerate(S.STEPS):
                row = {"단계": name}
                for _, rr in recs.iterrows():
                    sl = rr.get("단계로그") or []
                    s = sl[s_i] if s_i < len(sl) else {}
                    v = s.get("중간판정값") if s_i + 1 == 6 else (                      # 규격서 v1.51 비교 필드: ⑥ 최종 판단, ⑤ 룰셋, ④ 법령
                        ";".join(s.get("적용룰셋") or []) if s_i + 1 == 5 else
                        (";".join(s.get("검색법령") or []) if s_i + 1 == 4 else f"{s.get('데이터소스', '')}|{s.get('조회키', '')}|{s.get('결과건수', '')}"))
                    row[f"{int(rr['회차'])}회"] = str(v)[:120] if s else "(오류)"
                comp.append(row)
            cdf = pd.DataFrame(comp)
            base_col = cdf.columns[1]
            st.dataframe(cdf.style.apply(lambda row: ["" if c in ("단계", base_col) or row[c] == row[base_col]
                                                      else "background-color: rgba(239,68,68,.18)" for c in row.index], axis=1),
                         hide_index=True, width="stretch",
                         column_config={c: st.column_config.TextColumn(c, width="medium") for c in cdf.columns})
        if steps and item == "AI3":                                # 6단계 로그는 기록은 모든 항목, 화면 표시는 AI3만
            st.markdown("**6단계 단계별 처리 로그** (사례 ID · 회차 · 단계 번호 · 단계명 · 조회 데이터소스 · 조회 키 · 적용 룰셋 ID · 검색 법령 · 최종 판단 유형)")
            st.caption("값은 모두 AIPS가 응답으로 보낸 처리 기록(질의 해석 · 도구 호출 · 단계별 검토 · 최종 진단)에서 단계별로 가져온 것입니다. "
                       "**근거 읽는 법** — '응답 원문 14번 항목'은 아래 '응답 원문 (JSON)'을 펼쳤을 때 왼쪽에 보이는 14번이고, "
                       "괄호 안은 그 항목의 내용, '›' 뒤는 그 안에서 값을 가져온 부분입니다. "
                       "[c1] 같은 표시는 그 항목 안 도구 호출 번호(원문의 call_id)입니다.")
            n_last = len(S.STEPS)                                  # 규격서 v1.51 저장 항목 이름·순서대로 표시
            sdf = pd.DataFrame([{"사례 ID": cid, "회차 번호": int(rec["회차"]), "단계 번호": s_["단계번호"], "단계명": s_["단계명"],
                                 "실행": "예" if s_.get("실행") else "아니오",
                                 "조회 데이터소스": s_.get("데이터소스", ""), "조회 키": s_.get("조회키", ""),
                                 "조회 결과 건수": s_.get("결과건수", ""),
                                 "적용 룰셋 ID": "; ".join(s_.get("적용룰셋") or []),
                                 "검색 법령": "; ".join(s_.get("검색법령") or []),
                                 "최종 판단 유형": s_.get("중간판정값", "") if s_["단계번호"] == n_last else "",
                                 "근거": s_.get("근거", "")} for s_ in steps])
            show_table(sdf.astype(str))
        rp = os.path.join(run_dir, "raw", f"{cid}_{int(rec['회차'])}.json")
        if os.path.exists(rp):
            with st.expander("응답 원문 (JSON)"):
                raw = read_json(rp)
                raw = raw if isinstance(raw, list) else []
                st.markdown("**응답 흐름 요약** — AIPS 응답은 조각 여러 개가 차례로 옵니다. 번호는 받은 순서(0부터)이고, 아래 원문의 번호와 같습니다.")
                show_table(pd.DataFrame(S.event_rows(raw)), height=rows_height(len(raw), cap=360))
                st.markdown("**원문** — 번호 옆 이름이 조각의 종류입니다. ▶를 누르면 내용이 펼쳐집니다.")
                st.json(S.labeled_raw(raw), expanded=1)          # 번호·종류 목록만 펼치고 내용은 접음
