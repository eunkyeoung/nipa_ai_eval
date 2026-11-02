"""
판단 여부 점검 화면 — qa_app.py 사이드바 '화면' 전환에서 불러옴
질의를 일괄로 보내 시스템이 허가 가능 / 허가 불가로 판단하지 못한 질의를 찾습니다.
"""
import glob
import json
import os
import sys

import pandas as pd
import streamlit as st

import verdict_check as V

def runs():
    return sorted([d for d in glob.glob(os.path.join(V.ROOT, "*")) if os.path.isdir(d)],
                  key=os.path.getmtime, reverse=True)


def sources():
    out = {}
    up = next((p for p in glob.glob(os.path.join(V.DATA, "queries.*"))), None)
    if up:
        out[f"올린 파일 ({os.path.basename(up)})"] = up
    if os.path.exists("gold.xlsx"):
        out["gold.xlsx (QA 정답셋)"] = "gold.xlsx"
    for p in sorted(glob.glob(os.path.join("spec_data", "testset_ai*.*"))):
        out[f"규격서 {os.path.basename(p)}"] = p
    return out


def sidebar(side, launch, running):
    import qa_runner
    with side.popover("📂 질의 파일 올리기", width="stretch"):
        st.caption("CSV·XLSX: '질의문' 컬럼 (없으면 주소·시설물 컬럼으로 질의문 생성), ID 컬럼(사례ID·case_id 등)은 있으면 사용\n\n"
                   "TXT: 한 줄에 질의 하나")
        up = st.file_uploader("질의 파일", type=["csv", "xlsx", "txt"], label_visibility="collapsed", key="chk_up")
        if up is not None:
            os.makedirs(V.DATA, exist_ok=True)
            for p in glob.glob(os.path.join(V.DATA, "queries.*")):
                os.remove(p)
            ext = os.path.splitext(up.name)[1].lower()
            with open(os.path.join(V.DATA, "queries" + ext), "wb") as f:
                f.write(up.getvalue())
            st.success(f"{up.name} → {V.DATA}/queries{ext} 저장")
        st.download_button("⬇ 양식 (queries.csv)",
                           pd.DataFrame([["Q-0001", "경상북도 영천시 과전동 91에 단독주택 지을 수 있어?"]],
                                        columns=["ID", "질의문"]).to_csv(index=False).encode("utf-8-sig"),
                           file_name="queries.csv", width="stretch")
    src = sources()
    if not src:
        side.caption("질의 파일을 먼저 올려 주세요.")
        return
    name = side.selectbox("질의 목록", list(src), key="chk_src")
    path = src[name]
    try:
        qs = V.read_queries(path, qa_runner.QUESTION_TMPL)
    except Exception as e:                                           # noqa: BLE001
        side.error(f"읽지 못했어요: {e}")
        return
    ids = side.multiselect("실행할 ID (비우면 전체)", [q[0] for q in qs], key="chk_ids")
    n = len(ids) or len(qs)
    side.caption(f"질의 {n}회 · 예상 약 {n * 50 // 60}분")
    if side.button("⏳ 실행 중…" if running else "▶ 판단 여부 점검 실행", type="primary", width="stretch",
                   disabled=running or not qs, help="결과는 check/<시각>/ 에 저장돼요. 백그라운드에서 실행됩니다."):
        cmd = [sys.executable, "-u", "verdict_check.py", "--input", path, "--status-file", qa_runner.STATUS_FILE,
               "--base", qa_runner.BASE, "--timeout", str(qa_runner.TIMEOUT), "--question-tmpl", qa_runner.QUESTION_TMPL]
        if ids:
            cmd += ["--cases", ",".join(ids)]
        launch(cmd, n)


def render(area):
    rs = runs()
    if not rs:
        st.info("아직 점검 결과가 없어요. 왼쪽에서 질의 목록을 고르고 실행하세요.")
        with st.expander("판단 구분 기준", expanded=True):
            st.markdown(CRITERIA)
        return
    pick = st.session_state.pop("chk_run_dir", None)
    if pick:
        hit = next((d for d in rs if os.path.normpath(d) == os.path.normpath(pick)), None)
        if hit:
            st.session_state["chk_dir"] = hit
    if st.session_state.get("chk_dir") not in rs:
        st.session_state["chk_dir"] = rs[0]
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    run_dir = c1.selectbox("결과 폴더", rs, format_func=lambda d: d.replace("\\", "/"), key="chk_dir")
    if not os.path.exists(os.path.join(run_dir, "queries.json")):
        st.warning("이 폴더에는 아직 결과가 없어요.")
        return
    df, summ, by = V.build_report(run_dir, save=False)
    if c2.button("🔄 결과 엑셀 다시 만들기", width="stretch", help="재질의 없이 raw/ 원문으로 check_result.xlsx 재생성"):
        try:
            V.build_report(run_dir)
            st.toast("다시 만들었어요")
        except OSError as e:
            st.error(f"저장하지 못했어요 (엑셀로 열려 있으면 닫아 주세요): {e}")
    try:
        with open(os.path.join(run_dir, "input.json"), encoding="utf-8") as f:
            inp = json.load(f)
    except Exception:                                                # noqa: BLE001
        inp = {}
    n = len(df)
    dec = int((df["판단여부"] == "판단함").sum()) if n else 0
    with area("kpi_chk"):
        st.markdown("#### 판단 여부 요약")
        k = st.columns(5)
        k[0].metric("전체 질의", n)
        k[1].metric("판단함", dec, help="허가 가능(possible·conditional) 또는 허가 불가(impossible)로 답한 질의")
        k[2].metric("판단 못함", n - dec, help="검토 필요·되묻기·응답 없음·통신 오류 등")
        k[3].metric("판단률", f"{int(dec * 10000 / n) / 100:.2f} %" if n else "-", help="판단함 ÷ 전체 × 100 (소수점 셋째자리 이하 버림)")
        k[4].metric("허가 가능 / 불가", f"{int((df['판단결과'] == '허가 가능').sum())} / {int((df['판단결과'] == '허가 불가').sum())}")
        st.caption(f"질의 목록: {inp.get('input', '-')} · 대상 시스템: {inp.get('base', '-')} · 시작: {inp.get('started', '-')}")
    with area("warn_chk_by" if n - dec else "box_chk_by"):
        st.markdown("#### 판단 못함 — 유형별")
        if by.empty:
            st.success("모든 질의에서 허가 가능 / 허가 불가로 판단했어요.")
        else:
            st.dataframe(by, hide_index=True, width="stretch")
        with st.expander("판단 구분 기준"):
            st.markdown(CRITERIA)
    with area("box_chk_list"):
        st.markdown("#### 질의별 결과")
        only = st.toggle("판단 못한 질의만 보기", value=bool(n - dec), key="chk_only")
        view = df[df["판단여부"] == "판단 못함"] if only else df
        cols = ["ID", "질의문", "판단여부", "판단결과", "미판단유형", "시스템판정", "미판단사유", "시스템메시지", "소요초"]
        st.dataframe(view[[c for c in cols if c in view]], hide_index=True, width="stretch",
                     height=min(460, 38 + 35 * max(len(view), 1)))
        rx = os.path.join(run_dir, "check_result.xlsx")
        if os.path.exists(rx):
            with open(rx, "rb") as f:
                st.download_button("⬇ 결과 엑셀 (check_result.xlsx)", f.read(), file_name="check_result.xlsx")
    with area("box_chk_detail"):
        st.markdown("#### 질의 상세")
        cid = st.selectbox("질의", df["ID"].tolist(), format_func=lambda i: f"{i} · {df.set_index('ID').loc[i, '판단결과'] or df.set_index('ID').loc[i, '미판단유형']}",
                           key="chk_case")
        r = df.set_index("ID").loc[cid]
        st.dataframe(pd.DataFrame([(k, str(r[k])) for k in ["질의문", "판단여부", "판단결과", "미판단유형", "시스템판정",
                                                             "미판단사유", "시스템메시지", "근거"] if k in r],
                                  columns=["항목", "값"]), hide_index=True, width="stretch")
        rp = os.path.join(run_dir, "raw", f"{cid}.json")
        if os.path.exists(rp):
            with st.expander("응답 원문 (raw JSON)"):
                with open(rp, encoding="utf-8") as f:
                    st.json(json.load(f), expanded=False)


CRITERIA = """
| 구분 | 시스템 응답 | 표시 |
|---|---|---|
| **판단함** | `judgment.verdict` = possible · conditional | 허가 가능 |
| **판단함** | `judgment.verdict` = impossible | 허가 불가 |
| 판단 못함 | `judgment.verdict` = needs_review | 검토 필요 (미확정 조건·용도지역 `?`·결론 문장을 사유로 표시) |
| 판단 못함 | 진단 없이 되물음(reply) — 예: 주소를 찾지 못함 | 되묻기 (시스템 메시지 표시) |
| 판단 못함 | 진단도 되물음도 없음 | 응답 없음 |
| 판단 못함 | HTTP 오류·시간 초과 | 통신 오류·시간 초과 |
| 판단 못함 | 그 밖의 verdict 값 | 기타(값) |
"""
