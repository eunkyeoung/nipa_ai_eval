"""
AIPS 사전진단 시스템 일괄 질의 러너
-----------------------------------
정답셋 엑셀의 (주소, 시설물)을 하나씩 시스템 API에 질의하고,
응답에서 판정과 근거법령을 뽑아 dashboard/pred.xlsx로 저장합니다. 응답 원문은 dashboard/raw/ 폴더에 보관합니다.

입력  gold.xlsx : case_id | 주소 | 시설물 | ...  (질의문 컬럼이 있으면 그 문장을 그대로 사용)
출력  1회 실행(정확도)   : dashboard/raw/ , dashboard/pred.xlsx , dashboard/qa_result.xlsx
      N회 반복(일관성)  : dashboard/consistency/<YYYYMMDD_HHMM>_반복/raw/ , pred_repeat.xlsx , consistency_report.xlsx
      변형 질의(일관성) : dashboard/consistency/<YYYYMMDD_HHMM>_변형/…   (--variants, 같은 의도·다른 표현)
사용  python qa_runner.py gold.xlsx              (정확도)
      python qa_runner.py gold.xlsx --repeat 3   (일관성 - 동일 질의 반복)
      python qa_runner.py gold.xlsx --variants   (일관성 - 변형 질의)
필요  pip install pandas openpyxl requests
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import uuid

import pandas as pd
import requests

BASE = os.environ.get("AIPS_BASE", "http://192.168.200.115:17010/api")    # AIPS 사전진단 서비스 (2026-11-10 17000 → 17010). 컨테이너는 환경변수로 지정
DEVICE_ID = "qa-runner"                     # 시스템에서 QA 질의를 구분하기 위한 기기 ID
TIMEOUT = 300                               # 질의 1건 최대 대기(초)
QUESTION_TMPL = "{주소} {시설물} 건축 가능한가요?"
# 변형 질의(같은 의도·다른 표현) 기본 템플릿 — 1번은 기본 질의와 동일. 화면/파일로 바꿀 수 있음
DEFAULT_VARIANTS = [
    "{주소} {시설물} 건축 가능한가요?",
    "{주소} 필지에 {시설물} 신축이 가능한지 알려주세요.",
    "{주소}에 {시설물} 지으려고 하는데 허가 받을 수 있을까요?",
    "{시설물} 건축하려고 하는데 {주소} 땅이면 가능해?",
    "{주소} 토지에서 {시설물} 건축허가 가능 여부를 검토해 주세요.",
]


def build_queries(g, addr_col, fac_col, repeat=1, variants=None):
    """케이스 1건의 질의 목록 [(회차, 변형번호, 질의문)]
    - variants=None : 같은 질의를 repeat회 반복 (변형번호 0)
    - variants=[템플릿…] : 변형마다 1회. 정답셋에 '질의문_변형…' 컬럼이 있으면 그 문장을 우선 사용"""
    fill = lambda t: t.format(주소=str(g[addr_col]).strip(), 시설물=str(g[fac_col]).strip())
    if variants:
        col_vs = [str(g[c]).strip() for c in g.index
                  if str(c).startswith("질의문_변형") and pd.notna(g[c]) and str(g[c]).strip()]
        qs = col_vs or [fill(t) for t in variants]
        return [(i + 1, i + 1, q) for i, q in enumerate(qs)]
    base = str(g["질의문"]) if "질의문" in g.index and pd.notna(g["질의문"]) else fill(QUESTION_TMPL)
    return [(k + 1, 0, base) for k in range(repeat)]


def load_variants(path):
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]


def save_meta(run_dir, rows):
    """질의문·변형번호·정답판정을 raw와 함께 보관 → 재추출 때도 유지"""
    meta = {f"{r['case_id']}_{r['회차']}": {k: r.get(k) for k in ("질의", "변형", "정답판정", "소요초", "session_id")}
            for r in rows}
    with open(os.path.join(run_dir, "queries.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, default=str)
HEADERS = {"Content-Type": "application/json", "X-Device-Id": DEVICE_ID}


def stream_query(text: str):
    """질의 1건 실행 → SSE 이벤트 리스트 [(event, data_dict), ...]"""
    sid = f"qa-{uuid.uuid4().hex[:12]}"
    url = f"{BASE}/chat/sessions/{sid}/messages/stream"
    events, ev, buf = [], None, []
    with requests.post(url, headers=HEADERS, json={"user_request": text},
                       stream=True, timeout=TIMEOUT) as r:
        r.raise_for_status()
        # 서버가 charset을 안 보내므로 바이트로 받아 줄 단위로 UTF-8 디코딩 (한글 깨짐 방지)
        for bline in r.iter_lines(decode_unicode=False):
            if bline is None:
                continue
            line = bline.decode("utf-8", errors="replace").rstrip("\r")
            if line == "":                               # 이벤트 경계
                if ev:
                    raw = "\n".join(buf)
                    try:
                        events.append((ev, json.loads(raw)))
                    except json.JSONDecodeError:
                        events.append((ev, {"_raw": raw}))
                ev, buf = None, []
            elif line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                buf.append(line[5:])
    return sid, events


def extract(events):
    """이벤트에서 판정·근거 추출"""
    diag, stages, reply = None, [], ""
    for ev, d in events:
        if ev != "orc.response":
            continue
        if d.get("event") == "diagnosis":
            diag = d.get("data") or {}
        elif d.get("event") == "stage":
            stages.append(d.get("data") or {})
        elif d.get("event") == "reply":
            reply = str((d.get("data") or {}).get("reply", ""))
    if not diag:
        # 진단 없이 끝남: 주소를 못 찾았거나 되물은 경우(reply) / 그 외
        return dict(AI판정="되묻기" if reply else "응답없음", AI용도지역판정="", AI근거="",
                    AI전체인용법령="", AI요약=reply, AI적용용도지역="", AI건폐율=None, AI용적률=None,
                    AI층수="", AI규모근거="", AI규모요약="", AI필요허가="", AI허가근거="")

    j = diag.get("judgment") or {}
    verdict = j.get("verdict") or diag.get("status")
    done = [s for s in stages if s.get("status") == "done"]
    laws_of = lambda s: (s.get("detail") or {}).get("laws") or []

    # 불가 근거: 실패(fail)한 조건의 조문 + 불가 판정 단계의 법령
    deny = [c.get("source") for c in j.get("conditions") or [] if c.get("status") == "fail" and c.get("source")]
    for s in done:
        if s.get("verdict") == "impossible":
            deny += laws_of(s)
    # 용도지역·도시계획시설 등으로 불허된 경우 근거는 report.band.deny_reasons 에 들어 있음
    rep0 = diag.get("report") or {}
    deny += [str(x) for x in (rep0.get("band") or {}).get("deny_reasons") or []]
    # 허용 근거: 용도지역·토지이용계획 검토 단계(zone_use)에서 인용한 법령
    allow = []
    for s in done:
        if s.get("id") == "zone_use":
            allow += laws_of(s)
    all_laws = [x for s in done for x in laws_of(s)]
    all_laws += [c.get("source") for c in j.get("conditions") or [] if c.get("source")]

    uniq = lambda xs: list(dict.fromkeys(x for x in xs if x))
    br = lambda xs: " ".join(f"[{x}]" for x in uniq(xs))
    rep = diag.get("report") or {}
    card = rep.get("card") or {}
    return dict(
        AI판정=verdict,                                                        # possible / impossible / ...
        AI용도지역판정=(j.get("matrix_gate") or {}).get("final", ""),          # O / X … (접도 제외 판정)
        AI근거=br(deny) if verdict == "impossible" else br(allow),             # 채점에 쓰는 근거
        AI불가근거=br(deny),
        AI허용근거=br(allow),
        AI전체인용법령=br(all_laws),
        AI요약=card.get("headline") or (rep.get("band") or {}).get("line", ""),
        AI설명=card.get("summary", ""),
        **extract_scale(diag, done),
        **extract_permits(diag, done),
    )


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def visible_text(diag, stages, reply=""):
    """사용자에게 보이는 답변 문장만 모음 (단계별 결과 + 최종 보고서). 내부 지시문(parcel 등)은 제외"""
    parts = [reply or ""]
    for s_ in stages:
        det = s_.get("detail") or {}
        parts += [s_.get("text") or "", det.get("summary") or "", det.get("markdown") or ""]
        # checks(확인할 사항)는 LLM이 덧붙이는 일반 안내라 판단 근거가 아님 → 제외
        parts += [str(x) for x in (det.get("basis") or []) + (det.get("laws") or []) + (det.get("warnings") or [])]
    if diag:
        rep = diag.get("report") or {}
        parts += [json.dumps(rep.get("band"), ensure_ascii=False), json.dumps(rep.get("card"), ensure_ascii=False),
                  rep.get("detail_md") or ""]
        parts += [str(x) for x in (diag.get("judgment") or {}).get("violations") or []]
    return "\n".join(p for p in parts if p)


def extract_permits(diag, stages):
    """답변에서 언급한 행위허가 종류(1차 사업 10종) — 분류 규칙은 qa_eval.PERMIT_TYPES"""
    from qa_eval import PERMIT_NAMES, classify_permits
    ev = classify_permits(visible_text(diag, stages), with_evidence=True)
    names = [n for n in PERMIT_NAMES if n in ev]
    return dict(AI필요허가="; ".join(names), AI허가근거=" | ".join(f"{n}: …{ev[n]}…" for n in names))


def extract_scale(diag, stages):
    """적용 용도지역의 건폐율·용적률·층수와 그 근거 법령
    - 건폐율/용적률: diagnosis.scale (bcr, far_min~far_max), 적용 용도지역: scale.pieces[].zone
    - 층수: '건폐율·용적률·층수 검토' 단계(building_permit) 마크다운의 '적용 기준' 표
    - 근거: scale.specials[].basis(특례) 또는 scale.ordinance + 층수 근거 문장"""
    sc = diag.get("scale") or {}
    zones = list(dict.fromkeys(p.get("zone") for p in sc.get("pieces") or [] if p.get("zone")))
    if not zones:
        zones = [z.get("zone") for z in diag.get("scale_by_zone") or [] if z.get("zone")]
    bcr = _num(sc.get("bcr"))
    fmin, fmax = _num(sc.get("far_min")), _num(sc.get("far_max"))
    far = fmax if fmax is not None else fmin
    md = ""
    for s_ in stages:
        if s_.get("id") == "building_permit" and s_.get("status") == "done":
            md = (s_.get("detail") or {}).get("markdown") or md
    floor, floor_law = "", ""
    if "적용 기준" in md:
        sec = md.split("적용 기준", 1)[1].split("###", 1)[0]
        rows = [[c.strip() for c in ln.strip().strip("|").split("|")]
                for ln in sec.splitlines() if ln.strip().startswith("|") and "---" not in ln]
        if rows and rows[0] and rows[0][0].startswith("용도"):
            head, body = rows[0], rows[1:]
            fi = next((i for i, h in enumerate(head) if "층" in h), None)
            if fi is not None and body:
                pick = None
                for z in zones:                              # 적용 용도지역 행 우선
                    pick = next((r for r in body if r and r[0].replace("**", "").startswith(z)), None)
                    if pick and len(pick) > fi and pick[fi] not in ("", "—", "-"):
                        break
                cands = ([pick] if pick else []) + body      # 비어 있으면 다른 행(바탕 용도지역)에서
                floor = next((r[fi] for r in cands if r and len(r) > fi and r[fi] not in ("", "—", "-")), "")
        m = re.search(r"층수는\s*([^.。\n]+?)를\s*따릅니다", sec)
        floor_law = m.group(1).strip() if m else ""
    basis = [sp.get("basis") for sp in sc.get("specials") or [] if sp.get("basis") and sp.get("zone") in zones]
    if not basis and sc.get("ordinance"):
        basis = [sc["ordinance"]]
    if floor_law:
        basis.append(floor_law)
    far_txt = "" if far is None else (f"{fmin:g}~{fmax:g}" if fmin is not None and fmax is not None and fmin != fmax
                                      else f"{far:g}")
    parts = []
    if zones:
        parts.append("·".join(zones))
    if bcr is not None:
        parts.append(f"건폐율 {bcr:g}%")
    if far_txt:
        parts.append(f"용적률 {far_txt}%")
    if floor:
        parts.append(f"층수 {floor}")
    return dict(AI적용용도지역="·".join(zones), AI건폐율=bcr, AI용적률=far, AI층수=floor,
                AI규모근거=" ".join(f"[{b}]" for b in dict.fromkeys(basis)),
                AI규모요약=" · ".join(parts) + (f" ({'; '.join(dict.fromkeys(basis))})" if basis and parts else ""))


def load_raw_rows(raw_dir):
    """raw 폴더의 응답 원문 → 추출 결과 행 리스트"""
    import glob
    import re
    rows = []
    meta = {}
    mp = os.path.join(os.path.dirname(os.path.normpath(raw_dir)), "queries.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as fp:
            meta = json.load(fp)
    for f in sorted(glob.glob(os.path.join(raw_dir, "*.json")),
                    key=lambda p: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p)]):
        cid, k = os.path.basename(f)[:-5].rsplit("_", 1)
        with open(f, encoding="utf-8") as fp:
            events = [(e["event"], e["data"]) for e in json.load(fp)]
        row = dict(case_id=int(cid) if cid.isdigit() else cid, 회차=int(k))
        row.update(meta.get(f"{cid}_{k}", {}))
        row.update(extract(events))
        rows.append(row)
    return rows


def reextract(raw_dir, out):
    """raw 폴더의 응답 원문만으로 결과 파일 재생성 (재질의 없음)"""
    rows = load_raw_rows(raw_dir)
    df = pd.DataFrame(rows)
    df.to_excel(out, index=False)
    print(f"{raw_dir} {len(rows)}건에서 재추출 → {out}")
    if len(df) and (df["회차"].max() > 1 or ("변형" in df and df["변형"].fillna(0).max() > 0)):
        consistency_report(df, os.path.join(os.path.dirname(out) or ".", "consistency_report.xlsx"))


def consistency_report(df, out):
    """같은 케이스를 여러 번 질의한 결과의 일관성 리포트
    - 동일 질의 반복: 회차별 비교
    - 변형 질의(변형 컬럼 > 0): 변형(표현)별 비교 + 변형별 판정 정답률"""
    from itertools import combinations
    from qa_eval import extract_citations, norm_verdict

    is_var = "변형" in df.columns and pd.to_numeric(df["변형"], errors="coerce").fillna(0).max() > 0
    lab = "변형" if is_var else "회"
    rows = []
    for cid, g in df.groupby("case_id", sort=False):
        g = g.sort_values("회차")
        verdicts = [norm_verdict(v) for v in g["AI판정"]]
        top = max(set(verdicts), key=verdicts.count)
        cites = [extract_citations(x) for x in g["AI근거"]]
        pairs = list(combinations(cites, 2))
        jac = [len(a & b) / len(a | b) if (a | b) else 1.0 for a, b in pairs]
        row = dict(case_id=cid, 질의수=len(g),
                   판정일관=len(set(verdicts)) == 1,
                   최빈판정=top,
                   판정일치율=round(verdicts.count(top) / len(verdicts), 3),
                   근거완전일치=all(c == cites[0] for c in cites),
                   근거유사도_평균=round(sum(jac) / len(jac), 3) if jac else 1.0,
                   평균소요초=round(g["소요초"].mean(), 1) if "소요초" in g and g["소요초"].notna().any() else "")
        if "정답판정" in g and g["정답판정"].notna().any():
            gv = norm_verdict(g["정답판정"].dropna().iloc[0])
            row["정답판정"] = gv
            row["정답일치율"] = round(sum(v == gv for v in verdicts) / len(verdicts), 3)
        if is_var:
            row["판정이_다른_변형"] = ", ".join(f"변형{int(vn)}({v})" for vn, v in zip(g["변형"], verdicts) if v != top)
        for k, v, c in zip(g["회차"], verdicts, g["AI근거"]):
            row[f"{k}{lab}_판정"] = v
            row[f"{k}{lab}_근거"] = c
        rows.append(row)
    res = pd.DataFrame(rows)
    items = [
        ("질의 방식", "변형 질의 (같은 의도·다른 표현)" if is_var else "동일 질의 반복"),
        ("케이스 수", len(res)),
        ("케이스당 질의 수", int(df.groupby("case_id").size().max())),
        ("판정 일관성 (모든 질의 판정 동일 비율)", res["판정일관"].mean()),
        ("근거 완전 일치 비율", res["근거완전일치"].mean()),
        ("근거 유사도 평균 (질의 간 Jaccard)", res["근거유사도_평균"].mean()),
    ]
    if "정답일치율" in res:
        items.append(("정답 일치율 평균 (전체 질의 기준)", res["정답일치율"].mean()))
    summary = pd.DataFrame(items, columns=["지표", "값"])

    by_var = None
    if is_var:                                              # 변형(표현)별: 어떤 표현에서 판정이 흔들리는지
        d = df.copy()
        d["AI유형"] = d["AI판정"].map(norm_verdict)
        top_map = res.set_index("case_id")["최빈판정"]
        d["최빈과_일치"] = d.apply(lambda r: r["AI유형"] == top_map.get(r["case_id"]), axis=1)
        agg = dict(질의수=("case_id", "count"), 최빈판정_일치율=("최빈과_일치", "mean"),
                   응답없음_되묻기=("AI유형", lambda s: int(s.isin(["되묻기", "응답없음", "오류"]).sum())))
        if "정답판정" in d and d["정답판정"].notna().any():
            d["정답일치"] = d["AI유형"] == d["정답판정"].map(norm_verdict)
            agg["정답_일치율"] = ("정답일치", "mean")
        by_var = d.groupby("변형").agg(**agg).reset_index()
        ex = d.sort_values("case_id").groupby("변형")["질의"].first()
        by_var.insert(1, "질의 예시", by_var["변형"].map(ex))
        for c in ["최빈판정_일치율", "정답_일치율"]:
            if c in by_var:
                by_var[c] = by_var[c].round(3)
    with pd.ExcelWriter(out) as w:
        summary.to_excel(w, sheet_name="요약", index=False)
        res.to_excel(w, sheet_name="케이스별", index=False)
        if by_var is not None:
            by_var.to_excel(w, sheet_name="변형별", index=False)
        df.to_excel(w, sheet_name="전체질의", index=False)
    print(summary.to_string(index=False))
    if by_var is not None:
        print(by_var.to_string(index=False))
    print(f"일관성 리포트 → {out}")


# 컬럼명이 조금 달라도 찾도록 후보 이름 목록 (공백 무시, 부분 일치)
COL_CANDIDATES = {
    "주소": ["주소", "지번", "소재지", "위치", "대상지", "address"],
    "시설물": ["시설물", "시설", "건축물", "용도", "facility"],
    "case_id": ["case_id", "케이스", "번호", "no", "id", "순번"],
}


def find_col(cols, key, override=None):
    if override:
        return override
    norm = {c: str(c).replace(" ", "").replace("\n", "").lower() for c in cols}
    for cand in COL_CANDIDATES[key]:
        for c, n in norm.items():
            if cand.lower() in n:
                return c
    return None


# QA 대시보드 결과는 dashboard/ 아래 (성과지표 시험 결과는 spec/ 아래)
DASH = "dashboard"
RAW_DIR = os.path.join(DASH, "raw")                     # [정확도] 응답 원문
PRED = os.path.join(DASH, "pred.xlsx")                  # [정확도] 추출 결과
RESULT = os.path.join(DASH, "qa_result.xlsx")           # [정확도] 채점 결과
F1_RESULT = os.path.join(DASH, "f1_result.xlsx")        # [AI 2] F1 결과
CONSIST = os.path.join(DASH, "consistency")             # [일관성] 실행 시각별 폴더


def migrate_dashboard():
    """예전 위치(폴더 맨 위)의 대시보드 결과를 dashboard/ 로 옮김 (한 번만, 이미 있으면 건너뜀)"""
    import shutil
    moved = []
    os.makedirs(DASH, exist_ok=True)
    for old, new in (("raw", RAW_DIR), ("pred.xlsx", PRED), ("qa_result.xlsx", RESULT),
                     ("f1_result.xlsx", F1_RESULT), ("consistency", CONSIST)):
        if os.path.exists(old) and not os.path.exists(new):
            try:
                shutil.move(old, new)
                moved.append(f"{old} → {new}")
            except OSError:                                   # 엑셀로 열려 있는 등
                pass
    return moved


STATUS_FILE = "run_status.json"      # 화면(qa_app.py)이 읽는 진행 상황 파일
STOP_FILE = "run_stop.flag"          # 이 파일이 생기면 다음 질의 전에 중지


_status_lock = threading.Lock()


def write_status(path, st):
    """진행 상황 파일 쓰기. Windows에서 화면이 파일을 읽는 순간에는 교체가 막히므로 재시도하고,
    그래도 실패하면 이번 갱신만 건너뜀 (상태 파일 때문에 실행이 멈추지 않도록)"""
    if not path:
        return
    st["updated"] = time.time()
    with _status_lock:                                        # 본 작업과 heartbeat가 동시에 쓰지 않도록
        tmp = f"{path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False, default=str)
        except OSError:
            return
        for i in range(20):
            try:
                os.replace(tmp, path)                         # 반쯤 쓴 파일을 읽지 않도록 원자적 교체
                return
            except PermissionError:
                time.sleep(0.05 * (i + 1))
            except OSError:
                break
        try:
            os.remove(tmp)
        except OSError:
            pass


def load_gold_auto(path, sheet=0, header=None, addr=None, fac=None):
    """헤더 행 자동 인식(1행 → 2행) + 주소/시설물/case_id 컬럼 찾기"""
    for h in ([header] if header is not None else [0, 1]):
        gold = pd.read_excel(path, sheet_name=sheet, header=h)
        gold.columns = [str(c).strip() for c in gold.columns]
        addr_col, fac_col = find_col(gold.columns, "주소", addr), find_col(gold.columns, "시설물", fac)
        addr_col = addr_col if addr_col in gold.columns else None   # 지정한 컬럼명이 실제로 있는지 확인
        fac_col = fac_col if fac_col in gold.columns else None
        if "질의문" in gold.columns or (addr_col and fac_col):
            break
    id_col = find_col(gold.columns, "case_id")
    if id_col is None:
        gold["case_id"] = range(1, len(gold) + 1)
        id_col = "case_id"
    gold = gold.rename(columns={id_col: "case_id"})
    return gold, addr_col, fac_col, id_col


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gold")
    ap.add_argument("--out", help="결과 파일 (기본: 정확도 dashboard/pred.xlsx / 일관성 dashboard/consistency/<시각>/pred_repeat.xlsx)")
    ap.add_argument("--repeat", type=int, default=1, help="일관성 측정용 반복 횟수")
    ap.add_argument("--header", type=int, default=None, help="헤더가 있는 행 번호(0부터). 생략하면 자동 인식")
    ap.add_argument("--sheet", default=0, help="시트 이름 또는 번호")
    ap.add_argument("--addr-col", help="주소 컬럼명 직접 지정")
    ap.add_argument("--facility-col", help="시설물 컬럼명 직접 지정")
    ap.add_argument("--reextract", action="store_true", help="재질의 없이 raw 원문에서 결과 파일만 다시 만듦")
    ap.add_argument("--raw-dir", help="--reextract 대상 raw 폴더 (예: dashboard/consistency/20261020_1200/raw)")
    ap.add_argument("--variants", action="store_true",
                    help="변형 질의(같은 의도·다른 표현)로 일관성 측정. 기본 템플릿 5종 또는 --variant-file")
    ap.add_argument("--variant-file", help="변형 템플릿 파일 (한 줄에 하나, {주소}·{시설물} 사용)")
    ap.add_argument("--cases", help="실행할 case_id만 (예: 1,3,5). 생략하면 전체")
    ap.add_argument("--keep", action="store_true", help="정확도 모드에서 --cases 외 케이스의 기존 pred.xlsx 결과 유지")
    ap.add_argument("--run-dir", help="일관성 결과 폴더 직접 지정")
    ap.add_argument("--base", help="시스템 API 주소")
    ap.add_argument("--timeout", type=int, help="질의 1건 최대 대기(초)")
    ap.add_argument("--question-tmpl", help="질의문 형식 (예: '{주소} {시설물} 건축 가능한가요?')")
    ap.add_argument("--status-file", help="진행 상황 JSON 경로 (화면 연동용)")
    ap.add_argument("--resume", action="store_true",
                    help="중단된 실행 이어서: 이미 받은 질의(raw)는 건너뜀. 일관성은 --run-dir 필요")
    a = ap.parse_args()
    for m in migrate_dashboard():
        print("결과 위치 이동:", m)

    global BASE, TIMEOUT, QUESTION_TMPL
    BASE = (a.base or BASE).rstrip("/")
    TIMEOUT = a.timeout or TIMEOUT
    QUESTION_TMPL = a.question_tmpl or QUESTION_TMPL
    variants = (load_variants(a.variant_file) if a.variant_file else DEFAULT_VARIANTS) \
        if (a.variants or a.variant_file) else None
    consist = a.repeat > 1 or bool(variants)

    # 출력 위치: 정확도 측정(1회)과 일관성 측정(N회 반복 / 변형 질의)을 분리
    if consist:
        run_dir = a.run_dir or os.path.join(
            CONSIST, time.strftime("%Y%m%d_%H%M%S") + ("_변형" if variants else "_반복"))
        raw_dir = os.path.join(run_dir, "raw")
        out = a.out or os.path.join(run_dir, "pred_repeat.xlsx")
    else:
        run_dir, raw_dir, out = DASH, RAW_DIR, a.out or PRED

    if a.reextract:
        rd = a.raw_dir or RAW_DIR
        return reextract(rd, a.out or (os.path.join(os.path.dirname(rd.rstrip("/\\")), "pred_repeat.xlsx")
                                       if a.raw_dir else PRED))

    sheet = int(a.sheet) if str(a.sheet).isdigit() else a.sheet
    gold, addr_col, fac_col, id_col = load_gold_auto(a.gold, sheet, a.header, a.addr_col, a.facility_col)
    has_q = "질의문" in gold.columns
    if not has_q and (addr_col is None or fac_col is None):
        print("주소/시설물 컬럼을 찾지 못했습니다. 엑셀의 실제 컬럼:")
        for i, c in enumerate(gold.columns):
            print(f"  {i}: {c!r}")
        print('→ --addr-col "컬럼명" --facility-col "컬럼명" 으로 지정하거나 --header 로 헤더 행을 지정하세요.')
        write_status(a.status_file, dict(state="error", message="주소/시설물 컬럼을 찾지 못했습니다."))
        return
    print(f"사용 컬럼 → case_id: {id_col}, 주소: {addr_col}, 시설물: {fac_col}, 질의문: {has_q}")

    gold = gold[gold["case_id"].notna()]
    if a.cases:
        want = {c.strip() for c in a.cases.split(",") if c.strip()}
        gold = gold[gold["case_id"].astype(str).isin(want) |
                    gold["case_id"].map(lambda x: str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)).isin(want)]

    keep = pd.DataFrame()
    if not consist and a.keep and a.cases and os.path.exists(out):
        keep = pd.read_excel(out)
        keep = keep[~keep["case_id"].isin(gold["case_id"])]
    resume_ok = set()                                           # 이어서 실행: 이미 정상 응답을 받은 질의
    if a.resume and not consist and os.path.exists(out):
        prev = pd.read_excel(out)
        good = prev[~prev["AI판정"].astype(str).isin(["오류", "nan"])]
        resume_ok = {(str(c), 1) for c in good["case_id"]}
        keep = pd.concat([keep, good[~good["case_id"].isin(keep.get("case_id", []))]], ignore_index=True)

    plan = []
    for _, g in gold.iterrows():
        if not (has_q and pd.notna(g.get("질의문"))) and pd.isna(g[addr_col]):
            continue                                         # 빈 행(병합셀 등) 건너뜀
        for k, vn, q in build_queries(g, addr_col, fac_col, a.repeat, variants):
            plan.append((g, k, vn, q))

    os.makedirs(raw_dir, exist_ok=True)
    rows = []
    if a.resume and consist:                                    # 일관성: raw에 이미 있는 질의는 건너뜀
        prev_rows = load_raw_rows(raw_dir)
        good = [r for r in prev_rows if str(r.get("AI판정")) not in ("오류", "nan", "None")]
        resume_ok = {(str(r["case_id"]), int(r["회차"])) for r in good}
        rows = good
    if resume_ok:
        before = len(plan)
        plan = [x for x in plan if (str(x[0]["case_id"]), int(x[1])) not in resume_ok]
        print(f"이어서 실행: 이미 받은 {before - len(plan)}건은 건너뛰고 {len(plan)}건 질의")
    print(f"저장 위치 → raw: {raw_dir}, 결과: {out}  · 질의 {len(plan)}건")
    if variants:
        print("변형 질의 템플릿:\n  " + "\n  ".join(f"{i + 1}. {t}" for i, t in enumerate(variants)))
        with open(os.path.join(run_dir, "variants_used.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(variants) + "\n")

    argv = sys.argv[1:]
    if consist and "--run-dir" not in argv:
        argv += ["--run-dir", run_dir]                           # 이어서 실행할 때 같은 폴더를 쓰도록
    base_done = len(rows) if consist else len(resume_ok)
    status = dict(state="running", pid=os.getpid(), started=time.time(),
                  mode="일관성-변형" if variants else ("일관성-반복" if consist else "정확도"),
                  run_dir=run_dir, out=out, total=len(plan) + base_done, done=base_done, current=None,
                  message="이어서 실행" if a.resume else "", argv=argv)
    write_status(a.status_file, status)

    # 질의 응답을 기다리는 동안에도 '살아 있음'을 알리는 신호(heartbeat)
    alive = threading.Event()

    def heartbeat():
        while not alive.wait(3):
            write_status(a.status_file, status)
    if a.status_file:
        threading.Thread(target=heartbeat, daemon=True).start()

    gv_col = "정답판정" if "정답판정" in gold.columns else None
    try:
        for n, (g, k, vn, q) in enumerate(plan):
            if os.path.exists(STOP_FILE):
                os.remove(STOP_FILE)
                status.update(state="stopped", message=f"사용자가 중지 ({base_done + n}/{status['total']}건 완료)")
                print(status["message"])
                break
            status["current"] = dict(case_id=g["case_id"], 회차=k, 질의=q)
            write_status(a.status_file, status)
            t0 = time.time()
            row = dict(case_id=g["case_id"], 회차=k, 변형=vn, 질의=q,
                       정답판정=g[gv_col] if gv_col else None)
            try:
                sid, events = stream_query(q)
                with open(os.path.join(raw_dir, f"{g['case_id']}_{k}.json"), "w", encoding="utf-8") as f:
                    json.dump([{"event": e, "data": d} for e, d in events], f, ensure_ascii=False)
                row.update(extract(events), session_id=sid, 오류="")
            except Exception as e:                                 # noqa: BLE001
                row.update(AI판정="오류", 오류=str(e)[:300])
            row["소요초"] = round(time.time() - t0, 1)
            rows.append(row)
            print(f"[{g['case_id']}#{k}] {row.get('AI판정')} {row.get('AI근거', '')[:80]} ({row['소요초']}s)", flush=True)
            pd.concat([keep, pd.DataFrame(rows)], ignore_index=True).to_excel(out, index=False)   # 1건마다 저장
            if consist:
                save_meta(run_dir, rows)
            status.update(done=base_done + n + 1, current=None)
            write_status(a.status_file, status)

        # 마무리: 일관성 → 리포트 / 정확도 → 채점(qa_result.xlsx)
        if rows:
            if consist:
                consistency_report(pd.DataFrame(rows), os.path.join(run_dir, "consistency_report.xlsx"))
            elif gv_col:
                import qa_eval
                s_, c_, r_ = qa_eval.evaluate(qa_eval.load_gold(a.gold), pd.read_excel(out))
                qa_eval.save_result(s_, c_, r_, RESULT)
        if status["state"] == "running":
            status.update(state="done", message=f"완료 ({status['done']}/{status['total']}건)")
        print(f"완료: {out}")
    except Exception as e:                                         # noqa: BLE001
        status.update(state="error", message=f"{type(e).__name__}: {e}")
        raise
    finally:
        alive.set()
        status["current"] = None
        status["finished"] = time.time()
        write_status(a.status_file, status)


if __name__ == "__main__":
    try:
        main()
    except Exception as _e:                                         # noqa: BLE001
        import sys as _sys
        _sf = _sys.argv[_sys.argv.index("--status-file") + 1] if "--status-file" in _sys.argv else None
        if _sf:
            try:
                with open(_sf, encoding="utf-8") as _f:
                    _st = json.load(_f)
            except Exception:                                       # noqa: BLE001
                _st = {}
            if _st.get("state") in (None, "running"):
                _st.update(state="error", message=f"{type(_e).__name__}: {_e}", finished=time.time())
                write_status(_sf, _st)
        raise
