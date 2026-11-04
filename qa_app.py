"""
AIPS 사전진단 QA 화면 (Streamlit)
---------------------------------
실행:  pip install streamlit
       streamlit run qa_app.py          → 브라우저에서 http://localhost:8501 자동으로 열림

qa_runner.py / qa_eval.py 와 같은 폴더에 두고 실행합니다.
QA 대시보드 결과는 dashboard/ (raw/, pred.xlsx, qa_result.xlsx, f1_result.xlsx, consistency/),
성과지표 시험 결과는 spec/AIn/<시각>/ 에 저장됩니다.
"""
import glob
import io
import json
import os
import re
import subprocess
import sys
import time

import pandas as pd
import streamlit as st

import importlib

import qa_eval
import qa_f1
import qa_runner
import spec_eval
import spec_page
import verdict_check
import check_page

# qa_eval / qa_runner / qa_f1 파일이 바뀌면 화면을 껐다 켜지 않아도 새로고침 때 다시 읽음
# (qa_f1이 qa_eval 함수를 가져다 쓰므로 qa_eval → qa_runner → qa_f1 순서)
for _m in (qa_eval, qa_runner, qa_f1, spec_eval, spec_page, verdict_check, check_page):
    _mt = os.path.getmtime(_m.__file__)
    # 표시가 없으면(이 로직 이전에 불러온 모듈) 또는 파일이 바뀌었으면 다시 읽기
    if getattr(_m, "_loaded_mtime", None) != _mt:
        importlib.reload(_m)
        _m._loaded_mtime = _mt

# 화면을 URL로 구분 (메뉴는 숨김)
#   /           → 성과지표 시험 (시험원용 기본 화면)
#   /dashboard  → QA 대시보드 (내부용)
#   /check      → 판단 여부 점검 (내부용)
def _p_spec(): pass
def _p_dash(): pass
def _p_check(): pass
_page = st.navigation([st.Page(_p_spec, title="성과지표", url_path="spec", default=True),
                       st.Page(_p_dash, title="성능테스트 대시보드", url_path="dashboard"),
                       st.Page(_p_check, title="판단 여부 점검", url_path="check")], position="hidden")
VIEW = _page.title

st.set_page_config(page_title="AIPS 사전진단 성능테스트", page_icon="🧪",
                   layout="wide" if st.session_state.get("wide", True) else "centered")
os.chdir(os.path.dirname(os.path.abspath(__file__)))          # 스크립트 폴더 기준으로 파일 저장

# 브라우저 자동 번역(Chrome 번역 등) 끄기 — 번역기가 화면 요소를 바꿔 치우면
# "NotFoundError: 'removeChild'…" 오류가 나므로 페이지를 '번역하지 않음'으로 표시
import streamlit.components.v1 as _components
_components.html("""<script>
const d = window.parent.document;
d.documentElement.setAttribute("translate", "no");
d.documentElement.setAttribute("lang", "ko");
d.documentElement.classList.add("notranslate");
if (!d.querySelector('meta[name="google"]')) {
  const m = d.createElement("meta"); m.name = "google"; m.content = "notranslate"; d.head.appendChild(m);
}
</script>""", height=0)
_moved = qa_runner.migrate_dashboard()                         # 예전 위치의 대시보드 결과 → dashboard/

# 오른쪽 상단 Streamlit 기본 메뉴(Deploy 버튼, ⋮ 메뉴) 숨김 → 설정은 사이드바 하단 ⚙️ 설정에서
st.markdown("""
<style>
[data-testid="stToolbarActions"], [data-testid="stAppDeployButton"], .stAppDeployButton,
[data-testid="stMainMenu"], #MainMenu { display: none !important; }
/* 사이드바를 숨겼을 때 다시 펼치는 버튼은 왼쪽 위에 항상 표시 (제목 고정 영역보다 위) */
[data-testid="stExpandSidebarButton"] {
    position: fixed !important; top: 0.6rem; left: 0.6rem; z-index: 1001;
    background: #ffffff; border-radius: 8px; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12);
}
/* 상단 빈 여백 줄이기 (본문·사이드바) */
[data-testid="stHeader"] { height: 0; min-height: 0; background: transparent; }
[data-testid="stMainBlockContainer"], .block-container { padding-top: 0.75rem !important; }
[data-testid="stSidebarHeader"] { height: 2.25rem; padding-top: 0.5rem; padding-bottom: 0; margin-bottom: 0; }
[data-testid="stSidebarUserContent"] { padding-top: 0 !important; }
/* 스타일 코드가 만드는 빈 줄 숨김 */
[data-testid="stElementContainer"]:has(style) { display: none; }
[data-testid="stElementContainer"]:has(iframe[height="0"]) { display: none; }
/* 본문 제목 상단 고정 — 제목을 감싼 영역 자체를 sticky로 */
[data-testid="stLayoutWrapper"]:has(> div.st-key-page_header),
div.st-key-page_header:not([data-testid="stLayoutWrapper"] > *) {
    position: sticky; top: 0; z-index: 990;
    background: #ffffff; padding: 0.4rem 0 0.6rem 0;
    border-bottom: 1px solid rgba(100, 116, 139, 0.15);
}
div.st-key-page_header { min-height: 2.6rem; }
div.st-key-page_header [data-testid="stMarkdownContainer"],
div.st-key-page_header [data-testid="stMarkdown"] { margin-bottom: 0 !important; }
/* 탭 메뉴도 제목 아래에 고정 */
[data-testid="stTabs"] [role="tablist"] {
    position: sticky; top: var(--aips-header-h, 58px); z-index: 980;
    background: #ffffff; padding-top: 0.25rem;
}
/* 사이드바: 스크롤 없이 한 화면에 — 간격 축소, 스크롤바 숨김 */
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0.45rem; }
[data-testid="stSidebar"] h1 { font-size: 1.35rem; line-height: 1.4; padding: 0.2rem 0 0.2rem 0; }
[data-testid="stSidebar"] [data-testid="stHeading"] { margin-bottom: 0.5rem; }
[data-testid="stSidebar"] hr { margin: 0.4rem 0; }
[data-testid="stSidebarContent"] { scrollbar-width: none; }
[data-testid="stSidebarContent"]::-webkit-scrollbar { display: none; }
.aips-title { font-size: 1.9rem; font-weight: 700; line-height: 1.35; margin: 0; color: rgb(49, 51, 63); }
</style>
""", unsafe_allow_html=True)


# ---------------- 공통 ----------------
def load_gold(src):
    gold = qa_eval.load_gold(src)
    addr = qa_runner.find_col(gold.columns, "주소")
    fac = qa_runner.find_col(gold.columns, "시설물")
    cid = qa_runner.find_col(gold.columns, "case_id")
    if cid is None:
        gold["case_id"] = range(1, len(gold) + 1)
        cid = "case_id"
    gold = gold.rename(columns={cid: "case_id"})
    gold = gold[gold["case_id"].notna()]
    return gold, addr, fac


def question(row, addr, fac):
    if "질의문" in row and pd.notna(row["질의문"]):
        return str(row["질의문"])
    return qa_runner.QUESTION_TMPL.format(주소=str(row[addr]).strip(), 시설물=str(row[fac]).strip())


def to_xlsx(sheets: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name, index=name == "판정_혼동행렬")
    return buf.getvalue()


def ok(v):
    return "✅" if v else "❌"


@st.cache_data(show_spinner=False)
def read_raw(path, mtime):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


STATUS = qa_runner.STATUS_FILE


def read_status():
    try:
        with open(STATUS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                           # noqa: BLE001
        return None


def is_running(stt):
    """러너가 3초마다 상태를 갱신 → 20초 넘게 갱신이 없으면 비정상 종료로 판단"""
    return bool(stt) and stt.get("state") == "running" and time.time() - stt.get("updated", 0) < 20


def consistency_runs():
    return sorted([d for d in glob.glob(os.path.join(qa_runner.CONSIST, "*")) if os.path.isdir(d)], key=os.path.getmtime, reverse=True)


# ---------------- 결과 화면 ----------------
# 영역 구분: 테두리 없이 연한 배경색으로 묶음 (kpi = 연한 파랑 / box = 연한 회색 / warn = 연한 주황)
st.markdown("""
<style>
div[class*="st-key-kpi_"], div[class*="st-key-box_"], div[class*="st-key-warn_"] {
    border-radius: 14px; padding: 18px 22px 14px 22px;
}
div[class*="st-key-kpi_"]  { background: rgba(59, 130, 246, 0.08); }
div[class*="st-key-box_"]  { background: rgba(100, 116, 139, 0.07); }
div[class*="st-key-warn_"] { background: rgba(245, 158, 11, 0.10); }
div[class*="st-key-kpi_runend"] { background: rgba(34, 197, 94, 0.09); }
div[class*="st-key-"] h4 { margin-top: 0; padding-top: 0; font-size: 1.15rem; }
div[data-testid="stMetric"] { background: transparent; }
/* 회색 영역 안의 선택 상자는 배경과 같은 색이라 안 보임 → 연한 파랑 바탕 + 파란 테두리
   (Streamlit 1.66 구조: stSelectbox > div > div 가 입력 상자) */
div[class*="st-key-box_"] div[data-testid="stSelectbox"] > div > div,
div[class*="st-key-box_"] div[data-testid="stMultiSelect"] > div > div {
    background: #eff6ff !important; border: 1.5px solid #60a5fa !important; border-radius: 8px;
}
div[class*="st-key-box_"] div[data-testid="stSelectbox"] > div > div:hover,
div[class*="st-key-box_"] div[data-testid="stSelectbox"] > div > div:focus-within,
div[class*="st-key-box_"] div[data-testid="stMultiSelect"] > div > div:hover,
div[class*="st-key-box_"] div[data-testid="stMultiSelect"] > div > div:focus-within {
    border-color: #2563eb !important; box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.15);
}
div[class*="st-key-box_"] div[data-testid="stSelectbox"] input { color: #1e3a8a !important; font-weight: 600; }
/* 펼친 목록: 마우스를 올린 항목·선택된 항목을 파랑으로 */
[role="listbox"] [role="option"]:hover,
[role="listbox"] [role="option"][aria-selected="true"] { background: #dbeafe !important; }
</style>
""", unsafe_allow_html=True)


def area(key):
    """배경색 영역. key 접두어: kpi_ / box_ / warn_"""
    return st.container(key=key)


SPEC_VIEW = VIEW == "성과지표"
CHECK_VIEW = VIEW == "판단 여부 점검"
with st.container(key="page_header"):
    st.markdown('<div class="aips-title">' + {"성과지표": "성과지표 (AI 1 ~ AI 7)",
                                              "판단 여부 점검": "판단 여부 점검"}.get(VIEW, "AIPS 사전진단 성능테스트")
                + '</div>', unsafe_allow_html=True)


@st.fragment(run_every=2)
def progress_panel():
    """실행 상황을 2초마다 다시 읽어 표시 (이 영역만 갱신)"""
    stt = read_status()
    if not stt:
        return
    running = is_running(stt)
    fin = stt.get("finished") or stt.get("updated", 0)
    if not running:
        if st.session_state.get("was_running"):                 # 방금 끝남 → 결과 탭 갱신
            st.session_state["was_running"] = False
            if str(stt.get("mode", "")).startswith("판단 여부"):
                st.session_state["chk_run_dir"] = stt.get("run_dir")
            elif str(stt.get("mode", "")).startswith(("성과지표", "규격서")):   # '규격서'는 이름 변경 전 실행
                st.session_state["spec_run_dir"] = stt.get("run_dir")
            elif str(stt.get("mode", "")).startswith("일관성"):
                st.session_state["run_dir"] = stt.get("run_dir")
                st.session_state["tab_hint"] = "일관성"
            st.rerun(scope="app")
        if st.session_state.get("dismissed") == fin or time.time() - fin > 600:
            return
    else:
        st.session_state["was_running"] = True
    done, total = int(stt.get("done", 0)), max(int(stt.get("total", 0)), 1)
    state = stt.get("state")
    stale = state == "running" and not running
    with area("kpi_run" if running else ("warn_run" if state in ("error", "stopped") or stale else "kpi_runend")):
        h1, h2 = st.columns([4, 1], vertical_alignment="center")
        if running:
            el = int(time.time() - stt.get("started", time.time()))
            h1.markdown(f"**⏳ 실행 중** · {str(stt.get('mode', '')).replace('규격서', '성과지표')} · {done}/{total}건 · 경과 {el // 60}분 {el % 60}초")
            if os.path.exists(qa_runner.STOP_FILE):
                h2.button("⏹ 중지 요청됨", width="stretch", disabled=True)
            elif h2.button("⏹ 중지", width="stretch", help="지금 질의가 끝나면 멈춰요. 그때까지의 결과는 저장돼요."):
                open(qa_runner.STOP_FILE, "w").close()
                st.rerun(scope="fragment")
            st.progress(done / total)
            cur = stt.get("current") or {}
            if cur:
                st.caption(f"질의 중: [{cur.get('case_id')}#{cur.get('회차')}] {cur.get('질의')}")
            st.caption("백그라운드에서 실행 중이에요. 새로고침하거나 브라우저를 닫아도 계속 진행돼요.")
        else:
            icon = {"done": "✅ 완료", "stopped": "⏹ 중지됨", "error": "⚠️ 오류"}.get(state, "⚠️ 비정상 종료")
            h1.markdown(f"**{icon}** · {str(stt.get('mode', '')).replace('규격서', '성과지표')} · {done}/{total}건 · {stt.get('message', '')}")
            can_resume = (state in ("error", "stopped") or stale) and stt.get("argv") and done < total
            if can_resume:
                b1, b2 = h2.columns(2)
                if b1.button("↻ 이어서", width="stretch", help="이미 받은 결과는 그대로 두고 남은 질의만 실행"):
                    argv = [a for a in stt["argv"] if a != "--resume"] + ["--resume"]
                    launch([sys.executable, "-u", stt.get("script", "qa_runner.py")] + argv, total, resume=True)
                close = b2.button("닫기", width="stretch")
            else:
                close = h2.button("닫기", width="stretch")
            if close:
                st.session_state["dismissed"] = fin
                st.rerun(scope="app")
            if state in ("error",) or stale:
                try:
                    with open("run.log", encoding="utf-8", errors="replace") as f:
                        st.code("".join(f.readlines()[-15:]) or "(로그 없음)")
                except OSError:
                    pass
        try:
            out = stt.get("out")
            if out and os.path.exists(out) and done:
                df = pd.read_excel(out)
                if "질의" in df:
                    df = df[df["질의"].notna()]
                if "판단유형" in df:                              # 오류 칸: 종류 · 끊긴 위치 (통신 오류 + 응답 중단)
                    _ids = df["사례ID"] if "사례ID" in df else df.get("case_id", [""] * len(df))
                    _det = [spec_eval.error_detail({"오류": e if isinstance(e, str) else "", "판단유형": t,
                                                    "사례ID": i, "회차": n if n == n else 1}, stt.get("run_dir"))
                            for e, t, i, n in zip(df.get("오류", [""] * len(df)), df["판단유형"], _ids,
                                                  df.get("회차", [1] * len(df)))]
                    df["오류"] = [f"{k.split(' — ')[0]} · {w}" if k else "" for k, w in _det]
                cols = [c for c in ["case_id", "사례ID", "회차", "질의", "AI판정", "판단유형", "AI근거", "근거법령",
                                    "근거조문", "소요초"] if c in df]   # 적용룰셋은 결과 화면(사례별·상세)에서만 표시
                if "오류" in df and (df["오류"] != "").any():
                    cols.insert(cols.index("판단유형") + 1, "오류")      # 오류가 있을 때만 판단유형 옆에 표시
                _v = df[cols].tail(max(done, 1)).fillna("-")
                spec_page.show_table(_v, height=180)
        except Exception:                                       # noqa: BLE001 (러너가 쓰는 중이면 다음 갱신 때 표시)
            pass



# ---------------- 실행 (백그라운드 프로세스) ----------------
def launch(cmd, total, resume=False):
    """qa_runner.py를 별도 프로세스로 시작하고 화면을 새로 그림"""
    if os.path.exists(qa_runner.STOP_FILE):
        os.remove(qa_runner.STOP_FILE)
    qa_runner.write_status(STATUS, dict(state="running", done=0, total=total, current=None, started=time.time(),
                                        mode="시작 중", message="러너를 시작하는 중…"))
    log = open("run.log", "a" if resume else "w", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)   # Windows: 터미널 Ctrl+C와 분리
    subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                     env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    st.session_state["was_running"] = True
    st.session_state.pop("dismissed", None)
    time.sleep(1.5)
    st.rerun()



# ---------------- 사이드바: 실행(위) & 설정(아래) ----------------
side = st.sidebar.container()          # 위: 정답셋·실행
side_bottom = st.sidebar.container()   # 아래: ⚙️ 설정

with side_bottom:
    st.divider()
    with st.popover("⚙️ 설정", width="stretch"):
        st.markdown("**시스템 연결**")
        base = st.text_input("시스템 API 주소", qa_runner.BASE, key="api_base",
                             help="기본값 http://aips.iptime.org:17000/api — 테스트 서버로 바꿀 때만 수정")
        qa_runner.BASE = base.rstrip("/")
        qa_runner.TIMEOUT = st.number_input("질의 1건 최대 대기(초)", 30, 1200, qa_runner.TIMEOUT, 30, key="timeout")
        qa_runner.QUESTION_TMPL = st.text_input("질의문 형식", qa_runner.QUESTION_TMPL, key="q_tmpl",
                                                help="{주소}, {시설물} 자리에 정답셋 값이 들어갑니다. "
                                                     "정답셋에 '질의문' 컬럼이 있으면 그 문장을 우선 사용")
        st.markdown("**화면**")
        st.toggle("넓은 화면", value=True, key="wide")
        b1, b2 = st.columns(2)
        if b1.button("🔄 새로고침", width="stretch"):
            st.rerun()
        if b2.button("🧹 캐시 비우기", width="stretch", help="응답 원문 등 화면에 캐시된 데이터를 다시 읽음"):
            st.cache_data.clear()
            st.rerun()
        st.markdown("**정보**")
        st.caption(f"작업 폴더: {os.getcwd()}")
        st.caption(f"Streamlit {st.__version__} · 종료: 실행한 터미널에서 Ctrl + C")

side.title("🧪 AIPS 사전진단 성능테스트")
if CHECK_VIEW:
    check_page.sidebar(side, launch, is_running(read_status()))
    progress_panel()
    check_page.render(area)
    st.stop()
if SPEC_VIEW:
    _sitem = spec_page.sidebar(side, launch, is_running(read_status()))
    progress_panel()
    spec_page.render(_sitem, area)
    st.stop()

with side.popover("📂 정답셋 변경", width="stretch"):
    up = st.file_uploader("정답셋 엑셀 올리기 (올리면 폴더의 gold.xlsx로 저장)", type=["xlsx"])
gold_src = up if up is not None else ("gold.xlsx" if os.path.exists("gold.xlsx") else None)
if gold_src is None:
    side.error("gold.xlsx가 없습니다. 정답셋을 올려 주세요.")
    st.stop()
if up is not None:                                             # 올린 파일은 폴더에 gold.xlsx로 저장
    with open("gold.xlsx", "wb") as f:
        f.write(up.getvalue())
    gold_src = "gold.xlsx"

try:
    gold, addr_col, fac_col = load_gold(gold_src)
except Exception as e:                                          # noqa: BLE001
    side.error(f"정답셋을 읽지 못했습니다: {e}")
    st.stop()
side.caption(f"정답셋 {len(gold)}건 · 주소: `{addr_col}` · 시설물: `{fac_col}`")

mode = side.radio("측정 종류", ["정확도 (1회)", "일관성 (반복·변형)"], horizontal=True)
variants, repeat = None, 1
if not mode.startswith("정확도"):
    qmode = side.radio("질의 방식", ["동일 질의 반복", "변형 질의"], horizontal=True,
                       help="동일 질의 반복: 같은 문장을 N회 / 변형 질의: 같은 의도를 다른 표현으로 바꾼 문장들로 1회씩")
    if qmode.startswith("동일"):
        repeat = side.number_input("반복 횟수", 2, 10, 3)
    else:
        default_vs = qa_runner.load_variants("variants.txt") if os.path.exists("variants.txt") \
            else qa_runner.DEFAULT_VARIANTS
        with side.popover(f"✏️ 변형 문장 {len(st.session_state.get('variants_list', default_vs))}종 편집", width="stretch"):
            st.caption("한 줄에 하나씩, {주소}·{시설물} 자리에 정답셋 값이 들어가요. "
                       "저장하면 폴더의 variants.txt에 보관돼요. 정답셋에 '질의문_변형1…' 컬럼이 있으면 그 문장을 우선 사용해요.")
            txt = st.text_area("변형 질의 템플릿", "\n".join(default_vs), height=200, label_visibility="collapsed")
            c1, c2 = st.columns(2)
            if c1.button("💾 저장", width="stretch"):
                with open("variants.txt", "w", encoding="utf-8") as f:
                    f.write(txt.strip() + "\n")
                st.rerun()
            if c2.button("↺ 기본값", width="stretch"):
                if os.path.exists("variants.txt"):
                    os.remove("variants.txt")
                st.rerun()
            ex = gold.iloc[0]
            st.caption("예시: " + " / ".join(q for _, _, q in qa_runner.build_queries(
                ex, addr_col, fac_col, variants=[t for t in txt.splitlines() if t.strip()])[:2]) + " …")
        variants = [t.strip() for t in txt.splitlines() if t.strip()] or default_vs
        st.session_state["variants_list"] = variants
ids = side.multiselect("실행할 케이스 (비우면 전체)", gold["case_id"].tolist())
targets = gold[gold["case_id"].isin(ids)] if ids else gold
n_per = len(variants) if variants else repeat
est = len(targets) * n_per * 50
side.caption(f"질의 {len(targets) * n_per}회 · 예상 약 {est // 60}분 {est % 60}초")
_stt = read_status()
_running = is_running(_stt)
run = side.button("⏳ 실행 중…" if _running else "▶ 실행",
                  type="primary", width="stretch", disabled=_running,
                  help="실행은 백그라운드에서 진행돼요. 새로고침하거나 브라우저를 닫아도 계속됩니다.")
if mode.startswith("정확도") and ids:
    side.caption("선택한 케이스만 다시 질의하고, 나머지 케이스의 기존 결과는 유지합니다.")


# 화면(브라우저 연결)과 분리해서 qa_runner.py를 별도 프로세스로 실행 → 새로고침·탭 이동·연결 끊김에도 계속 진행
if run and not _running:
    consist = repeat > 1 or bool(variants)
    cmd = [sys.executable, "-u", "qa_runner.py", gold_src, "--status-file", STATUS,
           "--base", qa_runner.BASE, "--timeout", str(qa_runner.TIMEOUT), "--question-tmpl", qa_runner.QUESTION_TMPL]
    if addr_col:
        cmd += ["--addr-col", addr_col]
    if fac_col:
        cmd += ["--facility-col", fac_col]
    if ids:
        cmd += ["--cases", ",".join(map(str, ids))]
        if not consist:
            cmd.append("--keep")                                  # 선택 케이스 외 기존 결과 유지
    if variants:
        with open("_variants_run.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(variants) + "\n")
        cmd += ["--variant-file", "_variants_run.txt"]
    elif repeat > 1:
        cmd += ["--repeat", str(repeat)]
    launch(cmd, len(targets) * n_per)


# 지표 카드 ? 툴팁: 산정식 + 의미
HELP = {
    "판정 정확도": "**판정 정확도** = 판정이 정답과 같은 케이스 수 ÷ 전체 케이스 수",
    "오허가율": "**오허가율** = 정답 '불가'인데 AI가 '가능'이라고 한 수 ÷ 정답 '불가' 케이스 수\n\n"
              "지을 수 없는 땅을 된다고 안내한 비율 (가장 위험한 오류)",
    "오불허율": "**오불허율** = 정답 '가능'인데 AI가 '불가'라고 한 수 ÷ 정답 '가능' 케이스 수\n\n"
              "지을 수 있는 땅을 안 된다고 한 비율 (기회 손실 오류)",
    "핵심근거 적중률": "**핵심근거 적중률** = 핵심근거(필수근거, 없으면 근거)를 1개 이상 인용한 케이스 수 ÷ 전체 케이스 수\n\n"
                  "(법령명, 조) 단위로 비교",
    "조문 F1": "**조문 F1** = 케이스별 2 × 정밀도 × 재현율 ÷ (정밀도 + 재현율)의 평균\n\n"
             "정밀도 = 맞힌 조문 수 ÷ AI가 인용한 조문 수\n\n재현율 = 맞힌 조문 수 ÷ 정답 조문 수",
    "종합 정답률": "**종합 정답률** = 종합 정답 케이스 수 ÷ 전체 케이스 수\n\n"
              "종합 정답 = 판정 일치 + 핵심근거 적중 + 규모 일치\n\n"
              "(규모는 정답이 가능·조건부이고 정답셋에 수치가 있을 때만 확인)",
    "허가 정밀도": "**정밀도** = 맞힌 허가 수 ÷ **AI가 말한** 허가 수\n\n"
              "AI가 말한 것 중 얼마나 맞았나 → 과잉(필요 없는 허가 안내)을 봄",
    "허가 재현율": "**재현율** = 맞힌 허가 수 ÷ **정답** 허가 수\n\n"
              "정답 중 AI가 얼마나 찾아냈나 → 누락(필요한 허가를 빠뜨림)을 봄",
    "허가 F1": "**F1** = 2 × 정밀도 × 재현율 ÷ (정밀도 + 재현율)\n\n"
             "정밀도와 재현율을 함께 반영한 대표값 (전체 케이스의 허가를 합산해 계산)",
    "허가 완전일치율": "**완전일치율** = 필요 허가 목록이 정답과 정확히 같은 케이스 수 ÷ 대상 케이스 수\n\n"
                 "하나라도 빠지거나 더 말하면 불일치",
    "건폐율 정확도": "**건폐율 정확도** = AI 건폐율이 정답과 같은 케이스 수 ÷ 정답 건폐율이 있는 케이스 수\n\n(소수점 0.01 이내면 같음)",
    "용적률 정확도": "**용적률 정확도** = AI 용적률이 정답과 같은 케이스 수 ÷ 정답 용적률이 있는 케이스 수\n\n(소수점 0.01 이내면 같음)",
    "층수 정확도": "**층수 정확도** = AI 층수가 정답과 같은 케이스 수 ÷ 정답_층수가 있는 케이스 수\n\n"
              "('4층 이하'·'4층', '제한 없음'·'제한없음'은 같게 봄)",
    "적용 용도지역 일치율": "**적용 용도지역 일치율** = AI 적용 용도지역이 정답의 '(○○ 기준)'과 같은 케이스 수 ÷ 정답 용도지역이 있는 케이스 수",
    "규모 정확도": "**규모 정확도** = 건폐율·용적률·층수(정답에 있는 것)가 모두 일치한 케이스 수 ÷ 정답 규모가 있는 케이스 수",
}


def help_with_n(label, full):
    """산정식 툴팁 + 대상 건수"""
    n = re.search(r"대상\s*(\d+)건", full)
    base = HELP.get(label, full)
    return base + (f"\n\n대상 {n.group(1)}건" if n else "")


progress_panel()
if st.session_state.get("tab_hint") == "일관성":
    st.info("방금 실행한 일관성 결과는 **[AI 3] 일관성 결과** 탭에서 볼 수 있어요.")
tab_acc, tab_f1, tab_con, tab_gold = st.tabs(["📊 [AI 1] 정확도 결과", "🎯 [AI 2] 판단 F1 스코어",
                                              "🔁 [AI 3] 일관성 결과", "📋 정답셋"])

# ----- [AI 1] 정확도 -----
with tab_acc:
    if not os.path.exists(qa_runner.PRED):
        st.info("아직 결과가 없어요. 왼쪽에서 측정 종류를 '정확도 (1회)'로 두고 실행하세요.")
    else:
        pred = pd.read_excel(qa_runner.PRED)
        col_a, col_b = st.columns([3, 1], vertical_alignment="center")
        col_a.caption(f"dashboard/pred.xlsx 기준 · 마지막 저장 {time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(qa_runner.PRED)))}")
        if col_b.button("🔄 raw에서 다시 추출", help="재질의 없이 dashboard/raw/ 원문으로 pred.xlsx 재생성", width="stretch"):
            qa_runner.reextract(qa_runner.RAW_DIR, qa_runner.PRED)
            st.rerun()
        summary, confusion, res = qa_eval.evaluate(gold, pred)
        m = dict(zip(summary["지표"], summary["값"]))

        with area("kpi_acc"):
            c = st.columns(6)
            c[0].metric("판정 정확도", f"{m['판정 정확도']:.0%}", help=HELP["판정 정확도"])
            c[1].metric("오허가율", f"{m['오허가율 (정답 불가→AI 가능)']:.0%}", help=HELP["오허가율"])
            c[2].metric("오불허율", f"{m['오불허율 (정답 가능→AI 불가)']:.0%}", help=HELP["오불허율"])
            c[3].metric("핵심근거 적중률", f"{m['핵심근거 적중률']:.0%}", help=HELP["핵심근거 적중률"])
            c[4].metric("조문 F1", f"{m['조문 F1 (평균)']:.2f}", help=HELP["조문 F1"])
            _tot = m.get("종합 정답률 (판정+핵심근거+규모)", m.get("종합 정답률 (판정+핵심근거)", 0))
            c[5].metric("종합 정답률", f"{_tot:.0%}", help=HELP["종합 정답률"])
            sk = [(k, v) for k, v in m.items() if any(w in k for w in ("건폐율 정확도", "용적률 정확도", "층수 정확도",
                                                                        "적용 용도지역", "규모 정확도"))]
            pk = [(k, v) for k, v in m.items() if k.startswith("필요 행위허가")]
            if pk:
                st.caption("필요 행위허가 — 정답셋에 '정답_필요허가'가 적힌 케이스만 대상 (1차 사업 허가 10종)")
                c3 = st.columns(6)
                for i, (k, v) in enumerate(pk[:4]):
                    label = k.replace("필요 행위허가 ", "허가 ").split(" (")[0]
                    c3[i].metric(label, f"{v:.0%}", help=help_with_n(label, k))
            if sk:
                st.caption("규모 — 정답이 가능·조건부이고 정답셋에 건폐율·용적률·층수가 적힌 케이스만 대상 (불가는 규모가 정해지지 않아 제외)")
                c2 = st.columns(6)
                for i, (k, v) in enumerate(sk[:6]):
                    label = k.split(" (")[0] if "규모 정확도" not in k else "규모 정확도"
                    c2[i].metric(label, f"{v:.0%}", help=help_with_n(label, k))

        left, right = st.columns([1, 2])
        with left:
            with area("box_acc_cm"):
                st.markdown("#### 판정 혼동행렬")
                st.caption("행: 정답 · 열: AI")
                st.dataframe(confusion, width="stretch")
        with right:
            with area("box_acc_sum"):
                st.markdown("#### 지표 전체")
                st.dataframe(summary, width="stretch", hide_index=True)

        _pt = qa_eval.permit_table(res)
        if not _pt.empty:
            with area("box_acc_permit"):
                st.markdown("#### 행위허가 종류별 결과")
                st.caption("정답에 필요한 허가를 AI가 언급했는지 — FN(누락): 필요한데 언급 안 함 · FP(과잉): 필요 없는데 언급함")
                _v = _pt.copy()
                for col in ["정밀도", "재현율", "F1"]:
                    _v[col] = _v[col].map(lambda x: "" if pd.isna(x) else f"{x:.0%}")
                st.dataframe(_v, width="stretch", hide_index=True)

        with area("box_acc_cases"):
            h1, h2 = st.columns([3, 1], vertical_alignment="center")
            h1.markdown("#### 케이스별 결과")
            only_wrong = h2.toggle("틀린 케이스만 보기")
            view = res.copy()
            view = view.merge(gold[["case_id", addr_col, fac_col]], on="case_id", how="left")
            if only_wrong:
                view = view[~view["종합정답"]]
            cols = ["case_id", addr_col, fac_col, "정답판정", "AI판정", "판정일치", "핵심근거_적중", "종합정답"]
            if "규모_일치" in view:
                cols += ["규모_일치"]
            if "허가_완전일치" in view:
                cols += ["허가_완전일치"]
            cols += ["조문_F1", "누락근거", "과잉근거"] + [c for c in ["정답_규모", "AI_규모"] if c in view]
            if "허가_완전일치" in view:
                view = view.merge(pred[["case_id", "AI필요허가"]], on="case_id", how="left") \
                    if "AI필요허가" in pred and "AI필요허가" not in view else view
                cols += [c for c in ["정답_필요허가(분류)", "AI필요허가", "허가_누락", "허가_과잉"] if c in view]
            show = view[cols].copy()
            for col in ["판정일치", "핵심근거_적중", "종합정답"]:
                show[col] = show[col].map(ok)
            for col in ["규모_일치", "허가_완전일치"]:
                if col in show:
                    show[col] = show[col].map(lambda v: "" if pd.isna(v) else ok(bool(v)))
            st.dataframe(show, width="stretch", hide_index=True)
            st.download_button("⬇ qa_result.xlsx 다운로드",
                               to_xlsx({"요약": summary, "판정_혼동행렬": confusion, "케이스별": res}),
                               "qa_result.xlsx")

        with area("box_acc_detail"):
            st.markdown("#### 케이스 상세")
            pick = st.selectbox("케이스 선택", res["case_id"].tolist(),
                                format_func=lambda x: f"{x} · {gold.loc[gold['case_id'] == x, addr_col].iloc[0]}")
            g = gold[gold["case_id"] == pick].iloc[0]
            p = pred[pred["case_id"] == pick]
            p = p.iloc[0] if len(p) else pd.Series(dtype=object)
            r = res[res["case_id"] == pick].iloc[0]
            a, b = st.columns(2)
            with a:
                with area("kpi_acc_gold"):
                    st.markdown(f"**정답 · {r['정답판정']}**")
                    st.text(str(g.get("근거", "")))
                    if "필수근거" in g and pd.notna(g.get("필수근거")):
                        st.caption(f"필수근거: {g['필수근거']}")
            with b:
                with area("kpi_acc_ai"):
                    st.markdown(f"**AI · {r['AI판정']}**  ({ok(r['판정일치'])} 판정, {ok(r['핵심근거_적중'])} 핵심근거)")
                    st.write(p.get("AI요약", ""))
                    if pd.notna(p.get("AI설명", None)):
                        st.caption(p.get("AI설명"))
                    st.markdown("**AI근거** (채점 기준)")
                    st.text(str(p.get("AI근거", "")).replace("] [", "]\n["))
            if pd.notna(p.get("AI규모요약", None)) and str(p.get("AI규모요약")).strip():
                with area("box_acc_scale"):
                    st.markdown("**규모 근거** — 적용 용도지역의 건폐율·용적률·층수")
                    s1, s2 = st.columns(2)
                    gs = r.get("정답_규모", "")
                    s1.markdown(f"정답: {gs if isinstance(gs, str) and gs else '(정답셋에 없음)'}")
                    mark = "" if pd.isna(r.get("규모_일치", None)) else f"  {ok(bool(r['규모_일치']))}"
                    s2.markdown(f"AI: {r.get('AI_규모') or p.get('AI규모요약')}{mark}")
                    st.caption("근거: " + str(p.get("AI규모근거", "")).replace("] [", "] · ["))
            if str(p.get("AI필요허가", "") or "").strip() not in ("", "nan") or \
                    str(g.get("정답_필요허가", "") or "").strip() not in ("", "nan"):
                with area("box_acc_permit_detail"):
                    st.markdown("**행위허가** — 필요한 허가 종류")
                    q1, q2 = st.columns(2)
                    gp = g.get("정답_필요허가")
                    q1.markdown("정답: " + (str(gp) if isinstance(gp, str) and gp.strip() else "(정답셋에 없음)"))
                    ap = p.get("AI필요허가")
                    mk = "" if pd.isna(r.get("허가_완전일치", float("nan"))) else f"  {ok(bool(r['허가_완전일치']))}"
                    q2.markdown("AI: " + (str(ap) if isinstance(ap, str) and ap.strip() else "(언급 없음)") + mk)
                    if isinstance(p.get("AI허가근거"), str) and p.get("AI허가근거"):
                        st.caption("언급 위치: " + p.get("AI허가근거"))
            if r["누락근거"]:
                st.warning(f"누락근거: {r['누락근거']}")
            if r["과잉근거"]:
                st.info(f"과잉근거: {r['과잉근거']}")
            with st.expander("AI 불가근거 / 허용근거 / 전체인용법령"):
                for col in ["AI용도지역판정", "AI불가근거", "AI허용근거", "AI전체인용법령"]:
                    st.markdown(f"**{col}**")
                    st.text(str(p.get(col, "")).replace("] [", "]\n["))
            raw_path = os.path.join(qa_runner.RAW_DIR, f"{pick}_1.json")
            if os.path.exists(raw_path):
                ev = read_raw(raw_path, os.path.getmtime(raw_path))
                with st.expander("시스템 처리 과정 (단계별 결과)"):
                    seen = {}
                    for e in ev:
                        d = e["data"]
                        if e["event"] == "orc.response" and d.get("event") == "stage":
                            sd = d.get("data") or {}
                            if sd.get("status") == "done":
                                seen[sd.get("id")] = sd
                    for sid_, sd in seen.items():
                        st.markdown(f"**{sd.get('title')}** — verdict: `{sd.get('verdict')}`")
                        if sd.get("text"):
                            st.caption(sd["text"])
                        det = sd.get("detail") or {}
                        for line in det.get("basis") or []:
                            st.write("• " + str(line))
                with st.expander("응답 원문 JSON"):
                    st.json(ev, expanded=False)

# ----- [AI 2] 판단 F1 -----
with tab_f1:
    if not os.path.exists(qa_runner.PRED):
        st.info("[AI 1] 결과(pred.xlsx)가 있어야 계산할 수 있어요. 왼쪽에서 '정확도 (1회)'로 먼저 실행하세요.")
    else:
        st.caption("[AI 1]과 같은 질문셋·정답셋·AI 결과(pred.xlsx)로 4종 판단 유형별 지표를 계산합니다.")
        with area("box_f1_opt"):
            o1, o2, o3 = st.columns([1, 1, 2], vertical_alignment="bottom")
            target = o1.number_input("목표 F1(%)", 0.0, 100.0, 95.0, 0.5)
            na_opt = o2.selectbox("무응답(되묻기·오류) 처리", ["별도 열(미탐)", "검토필요로 간주"],
                                  help="별도 열: 4종 어디에도 넣지 않고 실제 유형의 미탐(FN)으로만 계산")
            use_w = o3.toggle("유형별 중요도 가중치 적용")
            weights = None
            if use_w:
                wc = st.columns(4)
                weights = {t: wc[i].number_input(f"{t} 중요도", 0.0, 10.0, 2.0 if t == "불가" else 1.0, 0.5,
                                                 key=f"w_{t}") for i, t in enumerate(qa_f1.TYPES)}
        r = qa_f1.run(gold, pd.read_excel(qa_runner.PRED), weights, target,
                      "separate" if na_opt.startswith("별도") else "검토필요", addr_col, fac_col)
        sm = dict(zip(r["summary"]["항목"], r["summary"]["값"]))
        final = r["final"]

        with area("kpi_f1"):
            k = st.columns(4)
            k[0].metric("종합 F1 Score", "-" if final is None else f"{final:.2f}%",
                        help="가중평균 F1 = Σ(유형별 F1 × 사례 수) / 전체 사례 수" + (" × 중요도" if weights else ""))
            k[1].metric("목표", f"{target:.1f}%")
            k[2].metric("달성 여부", sm.get("달성 여부", "-"))
            k[3].metric("평가 사례 수", sm.get("전체 사례 수", 0))
        if sm.get("사례 0건이라 제외된 유형", "-") != "-":
            with area("warn_f1"):
                st.markdown(f"⚠️ 정답셋에 **{sm['사례 0건이라 제외된 유형']}** 유형 사례가 없어 해당 유형 지표는 산출되지 않았어요. "
                            "4종 모두 평가하려면 정답셋에 각 유형 사례를 넣어 주세요.")

        a, b = st.columns([1, 1])
        with a:
            with area("box_f1_cm"):
                st.markdown("#### (5) 4×4 혼동행렬")
                st.caption("행: 실제(정답) · 열: 판단(AI)")
                st.dataframe(r["matrix"], width="stretch")
                st.caption(f"산출 도구: {sm.get('혼동행렬 산출 도구')}")
        with b:
            with area("box_f1_sum"):
                st.markdown("#### (7) 종합 F1 산정")
                st.dataframe(r["summary"].astype(str), width="stretch", hide_index=True)

        with area("box_f1_metrics"):
            st.markdown("#### (6) 유형별 지표")
            st.caption("4종 × Precision·Recall·Specificity·F1 = 16개 값 (분모 0이면 산출 불가)")
            mt = r["metrics"].copy()
            for c in ["Precision(%)", "Recall(%)", "Specificity(%)", "F1(%)", "오탐률(%)"]:
                mt[c] = mt[c].map(lambda v: "산출 불가" if pd.isna(v) else f"{v:.2f}")
            st.dataframe(mt, width="stretch", hide_index=True)

        with area("box_f1_err"):
            st.markdown(f"#### (8) 오탐·미탐 사례 — {len(r['errors'])}건")
            if r["errors"].empty:
                st.success("오탐·미탐 사례가 없어요.")
            else:
                st.dataframe(r["errors"].drop(columns=["확인 원인", "개선사항", "후속 계획"]), width="stretch", hide_index=True)
                st.markdown("**오탐 패턴 (특이도 보조 분석)** — AI가 잘못 판단한 유형이 실제로 어떤 유형에서 왔는지")
                st.dataframe(r["patterns"], width="stretch", hide_index=True)
                st.caption("추정 원인은 정답·AI 결과로 자동 분류한 값이에요. 다운로드한 엑셀의 '확인 원인·개선사항·후속 계획' 열에 검토 결과를 적어 주세요.")
            buf = io.BytesIO()
            qa_f1.save(r, buf)
            st.download_button("⬇ f1_result.xlsx 다운로드", buf.getvalue(), "f1_result.xlsx")

# ----- [AI 3] 일관성 -----
with tab_con:
    runs = consistency_runs()
    if not runs:
        st.info("아직 일관성 결과가 없어요. 왼쪽에서 측정 종류를 '일관성 (반복·변형)'으로 바꿔 실행하세요.")
    else:
        default = runs.index(st.session_state["run_dir"]) if st.session_state.get("run_dir") in runs else 0
        rd = st.selectbox("실행 폴더", runs, index=default,
                          format_func=lambda d: os.path.basename(d) + ("  · 변형 질의" if d.endswith("_변형") else
                                                                     "  · 동일 질의 반복" if d.endswith("_반복") else ""))
        rep_path = os.path.join(rd, "consistency_report.xlsx")
        _s = read_status()
        if is_running(_s) and os.path.normpath(str(_s.get("run_dir"))) == os.path.normpath(rd):
            st.info("이 폴더는 지금 실행 중이에요. 끝나면 리포트가 만들어지고 여기에 표시돼요.")
        elif not os.path.exists(rep_path) and os.path.exists(os.path.join(rd, "pred_repeat.xlsx")):
            qa_runner.consistency_report(pd.read_excel(os.path.join(rd, "pred_repeat.xlsx")), rep_path)
        if os.path.exists(rep_path):
            sheets = pd.read_excel(rep_path, sheet_name=None)
            sm, cs = sheets["요약"], sheets["케이스별"]
            m = dict(zip(sm["지표"], sm["값"]))
            pick = lambda *ks: next((m[k] for k in ks if k in m), 0)
            is_var = str(m.get("질의 방식", "")).startswith("변형")
            with area("kpi_con"):
                c = st.columns(5)
                c[0].metric("질의 방식", "변형" if is_var else "반복",
                            help="변형: 같은 의도·다른 표현으로 1회씩 / 반복: 같은 문장 N회")
                c[1].metric("케이스 수", int(pick("케이스 수")))
                c[2].metric("케이스당 질의 수", int(pick("케이스당 질의 수", "반복 횟수")))
                c[3].metric("판정 일관성", f"{pick('판정 일관성 (모든 질의 판정 동일 비율)', '판정 일관성 (모든 회차 판정 동일 비율)'):.0%}",
                            help="한 케이스의 모든 질의가 같은 판정을 낸 비율")
                c[4].metric("근거 유사도 평균", f"{pick('근거 유사도 평균 (질의 간 Jaccard)', '근거 유사도 평균 (회차 간 Jaccard)'):.2f}",
                            help="질의 쌍마다 AI근거 (법령명, 조) 집합의 Jaccard 유사도 평균. 1.0이면 매번 같은 근거")
            if "변형별" in sheets:
                with area("box_con_var"):
                    st.markdown("#### 변형(표현)별 결과")
                    st.caption("어떤 표현에서 판정이 흔들리는지 — 최빈판정 일치율: 그 케이스에서 가장 많이 나온 판정과 같은 비율")
                    bv = sheets["변형별"].copy()
                    for col in ["최빈판정_일치율", "정답_일치율"]:
                        if col in bv:
                            bv[col] = bv[col].map(lambda v: f"{v:.0%}")
                    st.dataframe(bv, width="stretch", hide_index=True)
            with area("box_con_cases"):
                h1, h2 = st.columns([3, 1], vertical_alignment="center")
                h1.markdown("#### 케이스별 " + ("변형 비교" if is_var else "회차 비교"))
                only_bad = h2.toggle("판정이 흔들린 케이스만 보기")
                v = cs[~cs["판정일관"]] if only_bad else cs
                v = v.copy()
                for col in ["판정일관", "근거완전일치"]:
                    v[col] = v[col].map(ok)
                st.dataframe(v, width="stretch", hide_index=True)
                with open(rep_path, "rb") as f:
                    st.download_button("⬇ consistency_report.xlsx 다운로드", f.read(),
                                       f"consistency_report_{os.path.basename(rd)}.xlsx")
            all_q = sheets.get("전체질의", sheets.get("전체회차"))
            if all_q is not None and "질의" in all_q:
                with st.expander("실제로 보낸 질의문 전체"):
                    cols = [c for c in ["case_id", "회차", "변형", "질의", "AI판정", "AI근거", "소요초"] if c in all_q]
                    st.dataframe(all_q[cols], width="stretch", hide_index=True)

# ----- 정답셋 -----
with tab_gold:
    with area("kpi_gold"):
        st.markdown(f"주소 컬럼: `{addr_col}` · 시설물 컬럼: `{fac_col}`")
        st.caption(f"질의문 예시: {question(gold.iloc[0], addr_col, fac_col)}")
    with area("box_gold"):
        st.markdown(f"#### 정답셋 — {len(gold)}건")
        st.dataframe(gold, width="stretch", hide_index=True)
