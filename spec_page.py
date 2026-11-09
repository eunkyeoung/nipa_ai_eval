"""
성과지표 화면 (AI 1 ~ AI 7) — qa_app.py 사이드바의 '화면' 전환에서 불러옴
"""
import glob
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
         "AI6": ["testset_ai6", "answer_ai6", "result_ai6"], "AI7": ["testset_ai7", "answer_ai7", "result_ai7"]}
LABEL = {"testset": "시험셋(질의)", "answer": "정답셋", "law_article_db": "법령·조례 조문 DB",
         "result": "실행 결과(시스템 응답)"}
RAW_VERDICT = {"possible": "승인 가능", "impossible": "부적합", "conditional": "조건부 가능",   # AIPS 원 판정값 → 수행계획서 4종
               "needs_review": "검토 필요", "unknown": "검토 필요"}
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


SHORT = {"AI1": "AI 1 정확도", "AI2": "AI 2 F1", "AI3": "AI 3 일관성", "AI4": "AI 4 조건부", "AI5": "AI 5 환각",
         "AI6": "AI 6 지도 제어", "AI7": "AI 7 3D 시뮬레이션"}
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
CRITERIA = {
    "AI1": "판단 유형 2종(허가 가능 / 허가 불가능)으로 정답과 비교. 조건부 가능은 '허가 가능'으로 보고 단서 조건은 부가 정보로 출력.\n\n"
           "보조 지표: 적용 법령·근거 조문(법령ID.조.항 정규화, 호·목 제외) 일치·불일치 건수.",
    "AI2": "시나리오별 원문 1 + 변형 4 질의의 판단 유형을 정답과 비교 → 2×2 혼동행렬.\n\n"
           "Precision = TP ÷ (TP + FP), Recall = TP ÷ (TP + FN), F1 = 2 × P × R ÷ (P + R).\n\n"
           "보조: 표현 변형 재현 일치율 = 원문과 같은 판단을 낸 변형 질의 수 ÷ 전체 변형 질의 수.",
    "AI3": "6단계 판단 프로세스: ① 질의 파싱 ② 대상 필지(PNU) 조회 ③ 토지특성(지목, 면적) 조회 ④ 적용 법령·조례 검색 "
           "⑤ 룰셋 적용 ⑥ 최종 판단 도출.\n\n"
           "1회차 로그를 기준으로 2–5회차를 비교. 비교 필드: 단계 실행 순서(①–⑥ 실행 순서·여부), 조회 데이터소스, "
           "조회 키·조회 결과 건수, 적용 룰셋 ID 집합(⑤), 검색 법령 집합(④), 최종 판단 유형(⑥). "
           "집합 필드는 순서 무시. 타임스탬프·처리 소요 시간·요청 ID·로그 시퀀스 번호는 제외.\n\n"
           "보조: 사례 단위 일관성 = 5회차가 모두 일치한 사례 수 ÷ 전체 사례 수.",
    "AI4": "판정 결과(2종)가 같고 **단서 조건 근거 조문 집합**(법령ID.조.항 정규화, 순서 무시)이 정답과 같아야 정답.\n\n"
           "보조: 보완사항(정확·부분·불일치), 필요 행정절차(집합 일치).",
    "AI5": "응답마다 ① 인용 조문 미존재(조문 DB에 없음) ② 근거 조문 부재 ③ 근거 오인용(정답 조문셋과 교집합 없음) "
           "④ 법령·조례 근거 불일치(응답의 적용 법령 집합이 정답 적용 법령 집합과 다름)를 자동 판정. 하나라도 해당하면 환각 1건(중복 계수 안 함).\n\n"
           "⑤ 판단 사유와 결정 간 논리 불일치는 보조 지표(사전 정의 규칙 R1–R3).",
    "AI6": "사례마다 ① 의도 추출(정답 의도 집합과 정확 일치) ② 핵심 파라미터(레이어·위치·분석 조건, 표기 차이 허용 '263번지' = '263') "
           "③ GIS 지도 제어(실행 후 지도 상태·분석 결과 표출이 정답 시나리오와 일치)를 순서대로 판정. 셋 다 일치하면 정상 수행 1건, "
           "부분 일치는 오류. 오류 원인은 최초 불일치 기준으로 분류.\n\n좌표·줌 레벨·면적·거리 등 수치는 기록용으로 판정에 쓰지 않음. "
           "결과는 소수점 둘째자리 이하 버림.",
    "AI7": "사례마다 ① 의도 추출 ② 핵심 파라미터(위치·층수·높이·용도·기준일·시간대·조망점 등) ③ 3D 시뮬레이션 실행(3D 지도 상태·생성 객체·"
           "실행 조건이 정답 시나리오와 일치, 실행 상태 '완료')을 순서대로 판정. 셋 다 일치하면 정상 수행 1건, 부분 일치는 오류. "
           "오류 원인은 최초 불일치 기준으로 분류. 실행 대기 상한 안에 '완료'가 아니면 미실행(실행 오류).\n\n결과는 소수점 둘째자리 이하 버림.",
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
                  key=os.path.getmtime, reverse=True)


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
        snap = os.path.join(run_dir, "data", os.path.basename(p)) if p else None
        if snap and os.path.exists(snap):
            return snap
        return p if p and os.path.exists(p) else data_path(stem)
    ans, ts, law = pick("answer", f"answer_{item.lower()}"), pick("testset", f"testset_{item.lower()}"), pick("lawdb", "law_article_db")
    if item != "AI5":
        law = None
    if item == "AI3":
        ans = None
    lp = os.path.join(run_dir, S.SPEC[item]["log"])
    if not os.path.exists(lp):
        return None, meta, (ans, ts, law)
    fm = tuple(os.path.getmtime(p) if p and os.path.exists(p) else 0 for p in (ans, ts, law, S.__file__))   # 채점 코드가 바뀌어도 다시 계산
    try:
        r = _score(item, run_dir, os.path.getmtime(lp), ans, ts, law, fm)
    except Exception as e:                                            # noqa: BLE001
        st.error(f"채점 중 오류: {type(e).__name__}: {e}")
        r = None
    return r, meta, (ans, ts, law)


# ---------------------------------------------------------------- 사이드바
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

    need = FILES[item]
    with side.popover("📂 시험 데이터", width="stretch"):
        st.caption(f"폴더 `{DATA}/` 의 CSV(UTF-8) 또는 XLSX를 사용해요. 올리면 같은 이름으로 저장돼요.")
        st.caption("작성 규칙 — 여러 값은 세미콜론(;)으로 구분 · 근거조문은 `법령명.조.항` (예: `건축법.2.1; 건축법.44`) "
                   "또는 `건축법 제44조 제1항`처럼 '제'를 붙여 표기 (자세한 내용: README 9-2)")
        for stem in need:
            p = data_path(stem)
            kind = LABEL.get(stem.split("_")[0], LABEL.get(stem, stem))
            n = len(S.read_table(p)) if p else 0
            st.markdown(f"**{kind}** `{stem}` — " + (f"✅ {n}건" if p else "❌ 없음"))
            up = st.file_uploader(stem, type=["csv", "xlsx"], key=f"up_{stem}", label_visibility="collapsed")
            if up is not None:
                ext = os.path.splitext(up.name)[1].lower()
                for e in (".csv", ".xlsx"):
                    if os.path.exists(os.path.join(DATA, stem + e)):
                        os.remove(os.path.join(DATA, stem + e))
                with open(os.path.join(DATA, stem + ext), "wb") as f:
                    f.write(up.getvalue())
                st.cache_data.clear()
                st.success(f"{stem}{ext} 저장")
        st.divider()
        st.markdown("**양식**")
        for stem in need:
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

    missing = [s for s in need if not data_path(s) and not s.startswith(("law_", "result_"))]
    for s_ in need:
        p = data_path(s_)
        opt = s_.startswith(("law_", "result_"))
        side.caption(("✅ " if p else ("⚠️ " if opt else "❌ ")) + f"{s_}" +
                     (f" · {len(S.read_table(p))}건" if p else (" 없음 (① 판정 보류)" if s_.startswith("law_")
                                                              else (" 없음 (채점 전에 올려 주세요)" if opt else " 없음"))))
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


# ---------------------------------------------------------------- 본문
def overview():
    rows = []
    for it in S.SPEC:
        rs = runs(it)
        sm = read_json(os.path.join(rs[0], "summary.json")) if rs else {}
        rows.append({"시험항목": f"{S.SPEC[it]['no']} {SHORT[it]}", "지표": S.SPEC[it]["metric"],
                     "진단 결과": fmt_pct(sm.get("value"), it) if sm else "미실행",
                     "목표": target_txt(it),
                     "달성 여부": ("✅ 달성" if sm.get("passed") else "❌ 미달성") if sm.get("value") is not None else "-",
                     "대상": sm.get("n", "-"), "실행": os.path.basename(rs[0]) if rs else "-"})
    return pd.DataFrame(rows)


def render(item, area, help_fn=None):
    sp = S.SPEC[item]
    with area("box_spec_overview"):
        st.markdown("#### 성과지표 현황 (AI 1 ~ AI 7)")
        ov = overview()
        st.dataframe(ov.style.map(lambda v: "background-color: #eff6ff; color: #1d4ed8; font-weight: 700"   # 진단 결과 열 = 파랑
                                  if v != "미실행" else "background-color: #eff6ff; color: #94a3b8", subset=["진단 결과"]),
                     hide_index=True, width="stretch")

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
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    run_dir = c1.selectbox("결과 폴더", rs, format_func=lambda d: d.replace("\\", "/"), key=sk)
    r, meta, (ans, ts, law) = score_run(item, run_dir)
    if r is None:
        st.warning("이 폴더에는 아직 로그가 없어요 (실행 중이거나 중단됨).")
        return
    if c2.button("💾 결과 파일 다시 저장", width="stretch", help="현재 정답셋으로 다시 채점해 result.xlsx / summary.json 저장"):
        import spec_runner
        spec_runner.run_score(item, run_dir, ans, ts, law)
        st.toast("저장했어요")

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
        if sp.get("cmd"):
            env = [x for x in env if x[0] not in ("대상 시스템", "시스템 빌드 / 계약 버전")]
            env.insert(1, ("실행 결과 파일 SHA-256", f"{meta.get('result_sha256') or '-'}  ({meta.get('result_file') or '-'})"))
        _env = pd.DataFrame(env, columns=["항목", "값"])
        show_table(_env)
        changed = [p for p, h, h0 in cur if h and h0 and h != h0]
        if changed:
            st.warning("실행 이후 바뀐 파일로 채점 중이에요: " + ", ".join(changed) + " → 해시가 실행 당시와 달라요.")
        if not sp.get("cmd"):
            with st.expander("시스템 미제공 항목 처리"):
                st.markdown("\n".join(f"- {x}" for x in NOT_PROVIDED))

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
            cc[0].markdown("**혼동행렬** (행 = 정답, 열 = 예측)")
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
        rest = [(name, pct_view(df) if isinstance(df, pd.DataFrame) else df) for name, df in rest]
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
        show_table(view, height=rows_height(len(view), cap=560))
        d1, d2 = st.columns(2)
        rx = os.path.join(run_dir, "result.xlsx")
        if os.path.exists(rx):
            with open(rx, "rb") as f:
                d1.download_button("⬇ 결과 엑셀 (result.xlsx)", f.read(), file_name=f"{item}_result.xlsx", width="stretch")
        lp = os.path.join(run_dir, sp["log"])
        with open(lp, "rb") as f:
            d2.download_button(f"⬇ 로그 ({sp['log']})", f.read(), file_name=sp["log"], width="stretch")

    # ---- 상세
    with area("box_spec_detail"):
        st.markdown("#### 질의별 상세 결과 · 판단 프로세스 로그" if item == "AI3" else "#### 질의별 상세 결과")
        key = "질의ID" if item == "AI2" else "사례ID"
        ids = list(dict.fromkeys(log[key].astype(str)))
        qcol = next((c for c in ("질의문", "명령문") if c in log.columns), None)
        qtext = dict(zip(log[key].astype(str), log[qcol].astype(str))) if qcol else {}
        cid = st.selectbox("상세 결과를 볼 질의를 선택하세요 ▼", ids, key=f"spec_case_{item}",
                           format_func=lambda i: f"{i}  ·  {qtext[i][:70]}" if qtext.get(i) else i,
                           help="목록을 펼쳐 질의를 고르면 아래에 시스템 출력 항목(판단 유형·근거 법령·근거 조문 등)이 나옵니다.")
        recs = log[log[key].astype(str) == cid].sort_values("회차")
        rec = recs.iloc[0]
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
