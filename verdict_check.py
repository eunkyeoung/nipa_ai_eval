"""
판단 여부 점검 — 질의를 일괄로 보내 시스템이 '허가 가능 / 허가 불가'로 판단했는지, 판단하지 못했는지 확인
------------------------------------------------------------------------------------------------
  python verdict_check.py --input check_data/queries.csv           # '질의문' 컬럼 (또는 주소+시설물)
  python verdict_check.py --input gold.xlsx
  python verdict_check.py --input spec_data/testset_ai1.csv
  python verdict_check.py --rescore check/20261022_183000             # 재질의 없이 결과표만 다시 만듦

결과: check/<시각>/  raw/ (응답 원문), check_result.xlsx (요약·미판단 유형별·질의별), summary.json

판단 구분
  판단함   : possible·conditional → 허가 가능 / impossible → 허가 불가
  판단 못함: needs_review → 검토 필요 / 진단 없이 되물음 → 되묻기 / 응답 없음 / 통신 오류·시간 초과 / 그 외 값
"""
import argparse
import json
import os
import sys
import threading
import time

import pandas as pd

import qa_runner

ROOT = "check"
DATA = "check_data"
ID_COLS = ["사례ID", "질의ID", "case_id", "ID", "id", "번호"]
DECIDED = {"possible": "허가 가능", "conditional": "허가 가능", "impossible": "허가 불가"}
UNDECIDED_LABEL = {"needs_review": "검토 필요", "되묻기": "되묻기", "응답없음": "응답 없음", "오류": "통신 오류·시간 초과"}
UNDECIDED_ORDER = ["검토 필요", "되묻기", "응답 없음", "통신 오류·시간 초과"]


def read_queries(path, tmpl=None):
    """CSV/XLSX('질의문' 또는 주소+시설물 컬럼) 또는 TXT(한 줄에 질의 하나) → [(ID, 질의문)]"""
    if path.lower().endswith(".txt"):
        with open(path, encoding="utf-8-sig") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        return [(f"Q-{i + 1:04d}", q) for i, q in enumerate(lines)]
    if path.lower().endswith((".xlsx", ".xls")):
        df = qa_runner.load_gold_auto(path)[0] if "gold" in os.path.basename(path).lower() else pd.read_excel(path, dtype=str)
    else:
        for enc in ("utf-8-sig", "cp949"):
            try:
                df = pd.read_csv(path, dtype=str, encoding=enc)
                break
            except UnicodeDecodeError:
                continue
    df.columns = [str(c).strip() for c in df.columns]
    idc = next((c for c in ID_COLS if c in df.columns), None)
    addr = qa_runner.find_col(df.columns, "주소")
    fac = qa_runner.find_col(df.columns, "시설물")
    out = []
    for i, r in df.iterrows():
        q = str(r.get("질의문", "") or "").strip() if "질의문" in df.columns else ""
        if (not q or q.lower() == "nan") and addr and fac and pd.notna(r.get(addr)) and pd.notna(r.get(fac)):
            q = (tmpl or qa_runner.QUESTION_TMPL).format(주소=str(r[addr]).strip(), 시설물=str(r[fac]).strip())
        if not q or q.lower() == "nan":
            continue
        rid = str(r[idc]).replace(".0", "") if idc and pd.notna(r.get(idc)) else f"Q-{i + 1:04d}"
        out.append((rid, q))
    return out


def classify(events):
    """응답 → 판단 여부·유형·사유"""
    base = qa_runner.extract(events)
    v = str(base.get("AI판정") or "")
    diag, reply = None, ""
    for ev, d in events:
        if ev == "orc.response" and d.get("event") == "diagnosis":
            diag = d.get("data") or {}
        elif ev == "orc.response" and d.get("event") == "reply":
            reply = str((d.get("data") or {}).get("reply", ""))
    j = (diag or {}).get("judgment") or {}
    band = ((diag or {}).get("report") or {}).get("band") or {}
    reasons = []
    if v not in DECIDED and diag:
        mg = j.get("matrix_gate") or {}
        if str(mg.get("final")) == "?":
            zs = ", ".join(f"{z.get('zone')}:{z.get('sym')}" for z in mg.get("zone_results") or []
                           if z.get("sym") not in ("O", "X"))
            reasons.append("용도지역 허용 여부 미확정(?)" + (f": {zs}" if zs else ""))
        for c in j.get("conditions") or []:
            if c.get("status") not in ("pass", "fail"):
                reasons.append(f"조건 미확정({c.get('status')}): {c.get('natural') or c.get('source') or c.get('rule_id')}")
        if band.get("line"):
            reasons.append("결론: " + str(band.get("line")))
    msg = reply or (base.get("AI요약") or "") + (" / " + base.get("AI설명") if base.get("AI설명") else "")
    return dict(판단여부="판단함" if v in DECIDED else "판단 못함",
                판단결과=DECIDED.get(v, ""),
                미판단유형="" if v in DECIDED else UNDECIDED_LABEL.get(v, f"기타({v or '값 없음'})"),
                시스템판정=v, 미판단사유=" | ".join(reasons), 시스템메시지=msg.strip(" /"),
                근거=base.get("AI근거", ""))


def build_report(run_dir, save=True):
    """raw/ + queries.json → check_result.xlsx, summary.json"""
    with open(os.path.join(run_dir, "queries.json"), encoding="utf-8") as f:
        qs = json.load(f)
    rows = []
    for q in qs:
        p = os.path.join(run_dir, "raw", f"{q['ID']}.json")
        if q.get("오류"):
            c = dict(판단여부="판단 못함", 판단결과="", 미판단유형="통신 오류·시간 초과", 시스템판정="오류",
                     미판단사유=q["오류"], 시스템메시지="", 근거="")
        elif os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                c = classify([(e["event"], e["data"]) for e in json.load(f)])
        else:
            continue
        rows.append({"ID": q["ID"], "질의문": q["질의문"], **c, "소요초": q.get("소요초")})
    df = pd.DataFrame(rows)
    n = len(df)
    dec = int((df["판단여부"] == "판단함").sum()) if n else 0
    trunc = lambda x: None if not n else int(x * 10000 / n) / 100
    summ = pd.DataFrame([("전체 질의", n), ("판단함", dec), ("  └ 허가 가능", int((df.get("판단결과") == "허가 가능").sum()) if n else 0),
                         ("  └ 허가 불가", int((df.get("판단결과") == "허가 불가").sum()) if n else 0),
                         ("판단 못함", n - dec), ("판단률(%)", trunc(dec)), ("미판단률(%)", trunc(n - dec))],
                        columns=["항목", "값"])
    und = df[df["판단여부"] == "판단 못함"] if n else df
    kinds = [k for k in UNDECIDED_ORDER if (und["미판단유형"] == k).any()] + \
        sorted(set(und["미판단유형"]) - set(UNDECIDED_ORDER)) if len(und) else []
    by = pd.DataFrame([{"미판단 유형": k, "건수": int((und["미판단유형"] == k).sum()),
                        "예시 ID": ", ".join(und[und["미판단유형"] == k]["ID"].head(5))} for k in kinds],
                      columns=["미판단 유형", "건수", "예시 ID"])
    if not save:
        return df, summ, by
    with pd.ExcelWriter(os.path.join(run_dir, "check_result.xlsx")) as w:
        summ.to_excel(w, sheet_name="요약", index=False)
        by.to_excel(w, sheet_name="미판단유형별", index=False)
        df.to_excel(w, sheet_name="질의별", index=False)
        und.to_excel(w, sheet_name="판단못함_목록", index=False)
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(dict(n=n, decided=dec, undecided=n - dec, rate=trunc(dec)), f, ensure_ascii=False)
    return df, summ, by


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="질의 파일 (CSV/XLSX: '질의문' 또는 주소+시설물 컬럼, TXT: 한 줄에 하나)")
    ap.add_argument("--cases", help="실행할 ID만 (쉼표 구분)")
    ap.add_argument("--run-dir")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--rescore", help="재질의 없이 이 결과 폴더의 결과표만 다시 만듦")
    ap.add_argument("--base")
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--question-tmpl")
    ap.add_argument("--status-file")
    a = ap.parse_args()
    if a.rescore:
        df, summ, _ = build_report(a.rescore)
        print(summ.to_string(index=False))
        return
    qa_runner.BASE = (a.base or qa_runner.BASE).rstrip("/")
    qa_runner.TIMEOUT = a.timeout or qa_runner.TIMEOUT
    qs = read_queries(a.input, a.question_tmpl)
    if a.cases:
        want = {c.strip() for c in a.cases.split(",") if c.strip()}
        qs = [x for x in qs if x[0] in want]
    if not qs:
        raise SystemExit(f"질의를 찾지 못했습니다: {a.input} ('질의문' 또는 주소·시설물 컬럼 필요)")

    run_dir = a.run_dir or os.path.join(ROOT, time.strftime("%Y%m%d_%H%M%S"))
    raw_dir = os.path.join(run_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    qpath = os.path.join(run_dir, "queries.json")
    meta = []
    if a.resume and os.path.exists(qpath):
        with open(qpath, encoding="utf-8") as f:
            meta = [m for m in json.load(f) if not m.get("오류")]
    done_ids = {m["ID"] for m in meta}
    plan = [x for x in qs if x[0] not in done_ids]
    with open(os.path.join(run_dir, "input.json"), "w", encoding="utf-8") as f:
        json.dump(dict(input=a.input, base=qa_runner.BASE, started=time.strftime("%Y-%m-%d %H:%M:%S")), f, ensure_ascii=False)

    argv = sys.argv[1:] + ([] if "--run-dir" in sys.argv else ["--run-dir", run_dir])
    total = len(plan) + len(done_ids)
    status = dict(state="running", pid=os.getpid(), started=time.time(), script="verdict_check.py", mode="판단 여부 점검",
                  run_dir=run_dir, out=os.path.join(run_dir, "progress.xlsx"), total=total, done=len(done_ids),
                  current=None, message="이어서 실행" if a.resume else "", argv=argv)
    qa_runner.write_status(a.status_file, status)
    alive = threading.Event()

    def heartbeat():
        while not alive.wait(3):
            qa_runner.write_status(a.status_file, status)
    if a.status_file:
        threading.Thread(target=heartbeat, daemon=True).start()
    prog = []
    try:
        for rid, q in plan:
            if os.path.exists(qa_runner.STOP_FILE):
                os.remove(qa_runner.STOP_FILE)
                status.update(state="stopped", message=f"사용자가 중지 ({status['done']}/{total}건 완료)")
                break
            status["current"] = dict(case_id=rid, 회차=1, 질의=q)
            qa_runner.write_status(a.status_file, status)
            t0 = time.time()
            m = {"ID": rid, "질의문": q, "오류": ""}
            try:
                sid, events = qa_runner.stream_query(q)
                with open(os.path.join(raw_dir, f"{rid}.json"), "w", encoding="utf-8") as f:
                    json.dump([{"event": e, "data": d} for e, d in events], f, ensure_ascii=False)
                c = classify(events)
                m["session_id"] = sid
            except Exception as e:                                   # noqa: BLE001
                m["오류"] = f"{type(e).__name__}: {str(e)[:200]}"
                c = dict(판단여부="판단 못함", 미판단유형="통신 오류·시간 초과", 판단결과="")
            m["소요초"] = round(time.time() - t0, 1)
            meta.append(m)
            with open(qpath, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
            prog.append({"사례ID": rid, "질의": q, "판단유형": c.get("판단결과") or c.get("미판단유형"), "소요초": m["소요초"]})
            try:
                pd.DataFrame(prog).to_excel(status["out"], index=False)
            except OSError:
                pass
            print(f"[{rid}] {c['판단여부']} {c.get('판단결과') or c.get('미판단유형')} ({m['소요초']}s)", flush=True)
            status.update(done=status["done"] + 1, current=None)
            qa_runner.write_status(a.status_file, status)
        df, summ, _ = build_report(run_dir)
        if status["state"] == "running":
            und = int((df["판단여부"] == "판단 못함").sum()) if len(df) else 0
            status.update(state="done", message=f"완료 ({status['done']}/{total}건) · 판단 못함 {und}건")
        print(status.get("message"), flush=True)
    except Exception as e:                                           # noqa: BLE001
        status.update(state="error", message=f"{type(e).__name__}: {e}")
        raise
    finally:
        alive.set()
        status["current"] = None
        status["finished"] = time.time()
        qa_runner.write_status(a.status_file, status)


if __name__ == "__main__":
    try:
        main()
    except BaseException as _e:                                       # noqa: BLE001
        _sf = sys.argv[sys.argv.index("--status-file") + 1] if "--status-file" in sys.argv else None
        if _sf and not isinstance(_e, KeyboardInterrupt):
            try:
                with open(_sf, encoding="utf-8") as _f:
                    _st = json.load(_f)
            except Exception:                                        # noqa: BLE001
                _st = {}
            if _st.get("state") in (None, "running"):
                _st.update(state="error", message=f"{type(_e).__name__}: {_e}", finished=time.time())
                qa_runner.write_status(_sf, _st)
        raise
