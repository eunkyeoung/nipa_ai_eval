"""
성과지표 시험 실행기 (AI 1 ~ AI 5)
--------------------------------
시험셋(CSV/XLSX)의 질의를 AIPS에 보내고, 항목별 로그(diagnose_log_aiN.jsonl)와 결과(result.xlsx)를 만듭니다.

  python spec_runner.py AI1 --testset spec_data/testset_ai1.csv --answer spec_data/answer_ai1.csv
  python spec_runner.py AI2 --testset spec_data/testset_ai2.csv --answer spec_data/answer_ai2.csv
  python spec_runner.py AI3 --testset spec_data/testset_ai3.csv --repeat 5
  python spec_runner.py AI4 --testset spec_data/testset_ai4.csv --answer spec_data/answer_ai4.csv
  python spec_runner.py AI5 --testset spec_data/testset_ai5.csv --answer spec_data/answer_ai5.csv --lawdb spec_data/law_article_db.csv

  python spec_runner.py AI1 --rescore spec/AI1/20261022_101500 --answer ...   # 재질의 없이 다시 채점
  python spec_runner.py AI1 --freeze --testset … --answer … [--lawdb …] [--base …] [--timeout 600]   # 운영자 동결 (spec_data/freeze_ai1.json, 11-17)
결과 폴더: spec/AIn/<시각>/  (raw/, diagnose_log_aiN.jsonl, meta.json, result.xlsx, summary.json)
"""
import argparse
import json
import os
import shutil
import sys
import threading
import time

import pandas as pd

import qa_runner
import spec_eval as S
import spec_freeze as F

ID_COL = {"AI2": "질의ID"}


def log_key(item):
    return ID_COL.get(item, "사례ID")


def run_score(item, run_dir, answer=None, testset=None, lawdb=None):
    r = S.score(item, run_dir, answer, testset, lawdb)
    if r is None:
        return None
    S.save_result(r, os.path.join(run_dir, "result.xlsx"))
    summ = dict(item=item, value=r["value"], target=S.SPEC[item]["target"], cmp=S.SPEC[item]["cmp"],
                passed=r["passed"], n=r["n"], aux=r.get("aux") or {}, scored=time.time())
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summ, f, ensure_ascii=False, indent=1, default=str)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("item", choices=list(S.SPEC))
    ap.add_argument("--testset")
    ap.add_argument("--answer")
    ap.add_argument("--lawdb")
    ap.add_argument("--repeat", type=int, default=None, help="AI3 반복 횟수 (기본 5)")
    ap.add_argument("--cases", help="실행할 ID만 (쉼표 구분)")
    ap.add_argument("--run-dir")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--rescore", help="재질의 없이 이 결과 폴더를 다시 채점")
    ap.add_argument("--reextract", action="store_true",
                    help="--rescore와 함께: raw/ 응답 원문에서 판정·근거 조문을 다시 추출해 로그를 고친 뒤 채점 (원래 로그는 .bak 보관)")
    ap.add_argument("--base")
    ap.add_argument("--ref-base", default=S.REF_BASE,
                    help="AI1 참조 정보(PNU·용도지역·규제구역·토지특성) 조회 서버. 빈 값이면 조회 안 함")
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--status-file")
    ap.add_argument("--freeze", action="store_true",
                    help="운영자용: 지금 시험 데이터·조건·버전으로 동결 기록(spec_data/freeze_aiN.json). 확인 질의 1건을 보냄")
    a = ap.parse_args()
    item = a.item
    if a.rescore:
        if a.reextract:
            reextract(item, a.rescore)
        r = run_score(item, a.rescore, a.answer, a.testset, a.lawdb)
        print("채점 결과:", None if r is None else f"{r['value']} % ({'달성' if r['passed'] else '미달성'})")
        return

    qa_runner.BASE = (a.base or qa_runner.BASE).rstrip("/")
    qa_runner.TIMEOUT = a.timeout or qa_runner.TIMEOUT
    repeat0 = a.repeat or (5 if item == "AI3" else 1)
    fz_dir = os.path.dirname(os.path.abspath(a.testset)) if a.testset else F.DATA
    if a.freeze:                                                  # 운영자 동결 — 확인 질의 1건으로 룰엔진 버전·빌드까지 기록
        ts0 = S.read_table(a.testset)
        q0 = str(ts0["질의문"].iloc[0]).strip()
        ver = F.current_versions(q0, qa_runner.BASE, qa_runner.TIMEOUT)
        rec = F.freeze_record(item, a.testset, a.answer, a.lawdb, qa_runner.BASE, qa_runner.TIMEOUT, repeat0, ver)
        with open(F.freeze_path(item, fz_dir), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        if (ver.get("확인질의") or {}).get("오류"):
            print("⚠️ 확인 질의에서 룰엔진 버전을 읽지 못함:", ver["확인질의"]["오류"])
        return
    fz = F.read_freeze(item, fz_dir)                              # 동결 기록이 있으면 시험 조건·버전이 같아야 실행(11-17)

    def refuse(msg):
        qa_runner.write_status(a.status_file, dict(state="error", script="spec_runner.py", mode=f"성과지표 {item}",
                                                   message=msg, finished=time.time()))
        raise SystemExit(msg)
    if fz:
        diff = F.check_conditions(fz, a.testset, a.answer, a.lawdb, qa_runner.BASE, qa_runner.TIMEOUT, repeat0)
        if diff:
            refuse("동결 이후 바뀐 시험 조건이 있어 실행하지 않아요: " + ", ".join(diff)
                   + f" — 의도한 변경이면 운영자가 다시 동결하세요(python spec_runner.py {item} --freeze …).")
        vdiff = F.check_versions(fz, F.current_versions())
        if vdiff:
            refuse("동결 버전과 다른 항목이 있어 실행하지 않아요: " + ", ".join(vdiff)
                   + " — 규격서: 버전이 바뀌면 다시 동결하고 전체 테스트셋을 재수행.")
    ts = S.read_table(a.testset)
    if ts is None or ts.empty:
        raise SystemExit(f"시험셋을 읽지 못했습니다: {a.testset}")
    key = log_key(item)
    if key not in ts.columns or "질의문" not in ts.columns:
        raise SystemExit(f"시험셋에 '{key}', '질의문' 컬럼이 필요합니다. 현재: {list(ts.columns)}")
    ts = ts[ts[key].str.strip() != ""]
    if a.cases:
        want = {c.strip() for c in a.cases.split(",") if c.strip()}
        ts = ts[ts[key].isin(want)]
    repeat = repeat0

    run_dir = a.run_dir or os.path.join("spec", item, time.strftime("%Y%m%d_%H%M%S"))
    raw_dir = os.path.join(run_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    log_path = os.path.join(run_dir, S.SPEC[item]["log"])
    snap = os.path.join(run_dir, "data")                          # 실행 당시 시험셋·정답셋 사본 → 나중에 정답셋이 바뀌어도 이 실행은 그대로 채점
    os.makedirs(snap, exist_ok=True)
    for p in (a.testset, a.answer, a.lawdb):
        if p and os.path.exists(p) and not os.path.exists(os.path.join(snap, os.path.basename(p))):
            shutil.copy2(p, snap)

    done_keys = set()
    if a.resume and os.path.exists(log_path):                     # 오류 줄은 버리고 정상 줄만 남김
        keep = []
        with open(log_path, encoding="utf-8") as f:
            for ln in f:
                try:
                    d = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if not S.error_text(d):                              # 통신 오류·응답 중단 건은 버리고 다시 질의
                    keep.append(ln if ln.endswith("\n") else ln + "\n")
                    done_keys.add((str(d[key]), int(d["회차"])))
        with open(log_path, "w", encoding="utf-8") as f:
            f.writelines(keep)

    plan = [(r, k) for _, r in ts.iterrows() for k in range(1, repeat + 1)
            if (str(r[key]), k) not in done_keys]
    meta = dict(item=item, 시험항목=S.SPEC[item]["name"], base=qa_runner.BASE, repeat=repeat,
                testset=a.testset, testset_sha256=S.sha256(a.testset),
                answer=a.answer, answer_sha256=S.sha256(a.answer),
                lawdb=a.lawdb, lawdb_sha256=S.sha256(a.lawdb), started=time.strftime("%Y-%m-%d %H:%M:%S"))
    meta["모델명"], meta["모델명출처"] = S.current_model()                 # 응답에 없는 모델명 → 실행 시작 때 콘솔 조회
    meta["법령DB"], meta["법령DB출처"] = S.current_law_db()               # 응답에 없는 법령 DB 기준 → 법령 수집 관리자 조회
    meta["인프라"] = S.infra_versions()                                    # vLLM·Fuseki·Milvus 버전 (설치정보 v1.1·서버 조회)
    meta["법령해석"] = S.current_itpt()                                    # 법령해석사례는 매일 04시 갱신 → 실행 시작 때 상태 기록
    meta["동결"] = fz.get("frozen_at") if fz else None                    # 동결 기록(11-17~) — 없으면 None
    mp = os.path.join(run_dir, "meta.json")
    if a.resume and os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            meta = {**json.load(f), "resumed": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    argv = sys.argv[1:]
    if "--run-dir" not in argv:
        argv += ["--run-dir", run_dir]
    total = len(plan) + len(done_keys)
    status = dict(state="running", pid=os.getpid(), started=time.time(), script="spec_runner.py",
                  mode=f"성과지표 {item}", run_dir=run_dir, out=os.path.join(run_dir, "progress.xlsx"),
                  total=total, done=len(done_keys), current=None,
                  message="이어서 실행" if a.resume else "", argv=argv)
    qa_runner.write_status(a.status_file, status)
    alive = threading.Event()

    def heartbeat():
        while not alive.wait(3):
            qa_runner.write_status(a.status_file, status)
    if a.status_file:
        threading.Thread(target=heartbeat, daemon=True).start()

    prog = []
    print(f"[{item}] 질의 {len(plan)}건 · 저장 위치 {run_dir}", flush=True)
    try:
        for n, (r, k) in enumerate(plan):
            if os.path.exists(qa_runner.STOP_FILE):
                os.remove(qa_runner.STOP_FILE)
                status.update(state="stopped", message=f"사용자가 중지 ({status['done']}/{total}건 완료)")
                break
            q = str(r["질의문"]).strip()
            rid = str(r[key])
            status["current"] = dict(case_id=rid, 회차=k, 질의=q)
            qa_runner.write_status(a.status_file, status)
            t0 = time.time()
            rec = {c: r[c] for c in ts.columns if c != "질의문"}
            rec.update(회차=k, 질의문=q, 요청시각=time.strftime("%Y-%m-%d %H:%M:%S"))
            try:
                sid, events = qa_runner.stream_query(q)
                with open(os.path.join(raw_dir, f"{rid}_{k}.json"), "w", encoding="utf-8") as f:
                    json.dump([{"event": e, "data": d} for e, d in events], f, ensure_ascii=False)
                base = qa_runner.extract(events)
                rec.update(S.map_response(events, base), 단계로그=S.step_log(events, base),
                           session_id=sid, 오류="")
                rec["오류"] = S.error_text(rec)              # 판정 없이 끝난 응답 → '응답 중단' (이어서 실행 때 다시 질의)
                if fz.get("룰엔진버전") and not status.get("eng_checked"):   # 첫 응답의 룰엔진 버전이 동결과 다르면 멈춤(규격: 재동결·전체 재수행)
                    eng = ", ".join(S.versions_in(json.dumps([{"event": e, "data": d} for e, d in events], ensure_ascii=False))
                                    .get("룰엔진버전") or [])
                    if eng:
                        status["eng_checked"] = True
                        if eng != fz["룰엔진버전"]:
                            status.update(state="error", message=f"룰셋(룰엔진) 버전이 동결({fz['룰엔진버전']})과 달라({eng}) 멈췄어요 "
                                                                 "— 규격서: 다시 동결하고 전체 테스트셋을 재수행.")
            except Exception as e:                                  # noqa: BLE001
                rec.update(판단유형=S.UNDECIDED, 단계로그=[], 오류=f"{type(e).__name__}: {str(e)[:300]}")
            rec["소요초"] = round(time.time() - t0, 1)
            if item == "AI1" and a.ref_base and "주소" in ts.columns:   # 참조 정보 — 질의 입력과 별개, 소요초에 미포함
                rec.update(S.fetch_ref(r.get("주소"), r.get("시설물"), a.ref_base))
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            prog.append({"사례ID": rid, "회차": k, "질의": q, "판단유형": rec.get("판단유형"),
                         "근거법령": "; ".join(rec.get("적용법령") or []), "근거조문": "; ".join(rec.get("근거조문") or []),
                         "적용룰셋": "; ".join(rec.get("적용룰셋") or []),
                         "소요초": rec["소요초"], "오류": rec["오류"]})
            try:
                pd.DataFrame(prog).to_excel(status["out"], index=False)
            except OSError:
                pass
            print(f"[{rid}#{k}] {rec.get('판단유형')} {rec.get('오류', '')} ({rec['소요초']}s)", flush=True)
            status.update(done=status["done"] + 1, current=None)
            qa_runner.write_status(a.status_file, status)
            if status["state"] == "error":                          # 룰엔진 버전 불일치 → 더 보내지 않음
                break
        if status["state"] == "running":
            snap_of = lambda p: os.path.join(snap, os.path.basename(p.replace("\\", "/"))) if p and os.path.exists(os.path.join(snap, os.path.basename(p.replace("\\", "/")))) else p
            r = run_score(item, run_dir, snap_of(a.answer), snap_of(a.testset), snap_of(a.lawdb))   # 실행 중 원본이 바뀌어도 시작 당시 사본으로 채점
            msg = f"완료 ({status['done']}/{total}건)"
            if r is not None and r["value"] is not None:
                msg += f" · {S.SPEC[item]['metric']} {r['value']} % {'달성' if r['passed'] else '미달성'}"
            status.update(state="done", message=msg)
        print(status.get("message"), flush=True)
    except Exception as e:                                          # noqa: BLE001
        status.update(state="error", message=f"{type(e).__name__}: {e}")
        raise
    finally:
        alive.set()
        status["current"] = None
        status["finished"] = time.time()
        qa_runner.write_status(a.status_file, status)


def reextract(item, run_dir):
    """raw/<ID>_<회차>.json 원문에서 판정·근거 조문을 현재 추출 규칙으로 다시 뽑아 로그 갱신 (질의 재전송 없음)"""
    import shutil
    p = os.path.join(run_dir, S.SPEC[item]["log"])
    if not os.path.exists(p + ".bak"):
        shutil.copy2(p, p + ".bak")
    rows = [json.loads(ln) for ln in open(p, encoding="utf-8") if ln.strip()]
    key = "질의ID" if rows and "질의ID" in rows[0] else "사례ID"
    n = 0
    for r in rows:
        rp = os.path.join(run_dir, "raw", f"{r[key]}_{r['회차']}.json")
        if not os.path.exists(rp):
            continue
        events = [(e["event"], e["data"]) for e in json.load(open(rp, encoding="utf-8"))]
        base = qa_runner.extract(events)
        r.update(S.map_response(events, base), 단계로그=S.step_log(events, base))
        if r.get("오류") == S.CUT_OFF:                    # '응답 중단'은 추출 결과로 정해진 값 → 새 추출로 다시 판정 (통신 오류는 그대로)
            r["오류"] = ""
            r["오류"] = S.error_text(r)
        n += 1
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(f"원문 재추출: {n}/{len(rows)}건")


if __name__ == "__main__":
    try:
        main()
    except BaseException as _e:                                      # noqa: BLE001
        _sf = sys.argv[sys.argv.index("--status-file") + 1] if "--status-file" in sys.argv else None
        if _sf and not isinstance(_e, KeyboardInterrupt):
            try:
                with open(_sf, encoding="utf-8") as _f:
                    _st = json.load(_f)
            except Exception:                                       # noqa: BLE001
                _st = {}
            if _st.get("state") in (None, "running"):
                _st.update(state="error", message=f"{type(_e).__name__}: {_e}", finished=time.time())
                qa_runner.write_status(_sf, _st)
        raise
