"""
성과지표 시험 실행기 — [AI 6] 자연어 기반 지도 제어 정확도
------------------------------------------------------
시험셋의 자연어 지도 제어 명령을 공간오케(지도제어·공간분석 Sub 오케스트레이터)에 한 건씩 보내고,
명령 해석 로그 · 실행 결과 로그 · 결과표를 만듭니다. 화면(규격서 시험 → AI6)이 이 파일을 실행합니다.

  python spec_runner_ai6.py --testset spec_data/testset_ai6.csv --answer spec_data/answer_ai6.csv \
                            --intent-map spec_data/intent_map_ai6.csv [--cases AI6-0001,AI6-0002] \
                            [--base http://192.168.200.115:17006] [--deadline 300]
  python spec_runner_ai6.py --rescore spec/AI6/20261110_150000 --answer ... --intent-map ...   # 다시 묻지 않고 원문으로 재채점(개발용)

결과 폴더: spec/AI6/<시각>/
  raw/<사례ID>.json                  요청·응답 원문(SSE 이벤트 전부)
  command_parse_log_ai6.jsonl        명령 1건당 1행 — 해석 의도·핵심 파라미터·실행 대상 기능·규격 출력 JSON
  gis_execution_log_ai6.jsonl        명령 1건당 1행 — 지도 제어 명령·표출 도형·정답 대조·판정
  data/                              실행 당시 시험셋·정답셋·의도 대응표 사본(채점은 이 사본으로)
  meta.json · progress.xlsx · result.xlsx · summary.json

규칙: 사례마다 새 thread_id · 순차 실행 · 문장은 그대로 · 사례당 총 대기 상한 초과 = 응답 오류 · 되묻기에 답하지 않음(=오류)
      이어서 실행(--resume)은 아직 안 보낸 사례만 보냄(명령 1회 입력 원칙). 응답 오류 사례를 다시 보내려면 --retry-errors
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time

import ai6_client as C
import ai6_eval as E

try:                                                       # 화면과 같은 상태 파일 규칙(qa_runner) — 로컬 시험처럼 pandas 가 없으면 같은 동작을 여기서
    import qa_runner as _QR
    write_status, STOP_FILE = _QR.write_status, _QR.STOP_FILE
except Exception:                                          # noqa: BLE001
    STOP_FILE = "run_stop.flag"
    _lock = threading.Lock()

    def write_status(path, st):
        if not path:
            return
        st["updated"] = time.time()
        with _lock:
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False, default=str)
            os.replace(tmp, path)

SCRIPT = "spec_runner_ai6.py"
GAP = float(os.environ.get("AI6_GAP", "2"))                # 사례 사이 쉬는 시간(초) — 공간오케에 몰아 보내지 않게
COOLDOWN = float(os.environ.get("AI6_COOLDOWN", "10"))     # 대기 상한 초과(취소) 뒤 쉬는 시간(초)
MAX_CONN_FAILS = 3                                          # 연결 실패가 이만큼 이어지면 환경 문제로 보고 멈춤(전체 재수행 대상)
FREEZE_FILE = "freeze_ai6.json"                             # 시험 데이터 폴더의 동결 기록 — 있으면 해시가 같아야 실행


def refuse(status_file, msg):
    """실행 전에 멈춤 — 상태 파일에 오류로 남기고 끝냄"""
    write_status(status_file, dict(state="error", script=SCRIPT, mode="성과지표 AI6", message=msg,
                                   started=time.time(), finished=time.time(), total=0, done=0))
    raise SystemExit(msg)


def pause(sec):
    """쉬는 동안에도 중지 요청이 오면 바로 깸"""
    end = time.monotonic() + sec
    while time.monotonic() < end and not os.path.exists(STOP_FILE):
        time.sleep(min(0.2, max(0.0, end - time.monotonic())))


def rules_sha256(intent_map):
    """의도 대응표 지문 — 파일이면 파일 해시, 없으면 기본 대응표 내용의 해시"""
    return sha256(intent_map) if intent_map else "기본:" + hashlib.sha256(E.rules_csv().encode("utf-8")).hexdigest()


def freeze_record(testset, answer, intent_map, deadline):
    """동결할 값 — 시험셋·정답셋·의도 대응표·판정 기준 지문·응답 대기 상한"""
    return dict(testset_sha256=sha256(testset), answer_sha256=sha256(answer), intent_map_sha256=rules_sha256(intent_map),
                criteria_sha256=E.criteria_fingerprint(), deadline_s=float(deadline))


def freeze_check(freeze_path, cur):
    """동결 기록과 지금 값을 비교 → 다른 항목 목록 (동결 안 했으면 None)"""
    if not os.path.exists(freeze_path):
        return None
    with open(freeze_path, encoding="utf-8") as f:
        fz = json.load(f)
    names = {"testset_sha256": "시험셋", "answer_sha256": "정답셋", "intent_map_sha256": "의도 대응표",
             "criteria_sha256": "판정 기준(코드)", "deadline_s": "응답 대기 상한"}
    return [names[k] for k in names if str(fz.get(k)) != str(cur.get(k))]


def sha256(path):
    if not path or not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_jsonl(path):
    rows = []
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
    return rows


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def append_jsonl(path, row):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def save_tables(path, tables):
    """결과표 저장 — pandas 가 있으면 한 엑셀에 시트별로, 없으면 표마다 CSV"""
    try:
        import pandas as pd
        with pd.ExcelWriter(path) as xw:
            for name, rows in tables.items():
                pd.DataFrame(rows).to_excel(xw, sheet_name=name[:31], index=False)
        return [path]
    except ImportError:
        import csv
        out = []
        for name, rows in tables.items():
            p = os.path.splitext(path)[0] + f"_{name}.csv"
            with open(p, "w", encoding="utf-8-sig", newline="") as f:
                if rows:
                    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                    w.writeheader()
                    for r in rows:
                        w.writerow({k: ("" if v is None else v) for k, v in r.items()})
            out.append(p)
        return out


def score_run(run_dir, testset_path, quiet=False):
    """실행 폴더 채점 → summary.json · result.xlsx. 시험셋에 있는데 결과가 없는 사례는 '미실행'(응답 오류)"""
    gis = read_jsonl(os.path.join(run_dir, E.GIS_LOG))
    ts = E.read_rows(testset_path)
    meta = {}
    mp = os.path.join(run_dir, "meta.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
    planned = set(meta.get("cases") or [])
    if planned:                                            # 일부 ID만 실행한 경우 그 ID만 분모
        ts = [t for t in ts if str(t.get("사례ID")) in planned]
    s = E.score(gis, ts)
    tables = {"총괄 결과": s["총괄"], "명령 유형 3종별 세부 결과": s["유형별"], "오류 원인 유형별 건수": s["오류원인별"],
              "사례별 판정": s["사례별"]}
    files = save_tables(os.path.join(run_dir, "result.xlsx"), tables)
    summ = dict(item="AI6", value=s["value"], target=E.TARGET, cmp=">=", passed=s["passed"], n=s["n"], ok=s["ok"],
                aux={"응답 오류": s["응답오류"], "되묻기": s["되묻기"],
                     "유형별": {r["명령 유형"]: r["정확도(%)"] for r in s["유형별"]},
                     "오류원인별": {r["오류 원인"]: r["건수"] for r in s["오류원인별"]}},
                files=files, scored=time.time())
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summ, f, ensure_ascii=False, indent=1, default=str)
    if not quiet:
        print(f"[AI6] 정확도 {'-' if s['value'] is None else format(s['value'], '.2f')} % ({s['ok']}/{s['n']}) · "
              f"응답 오류 {s['응답오류']} · 되묻기 {s['되묻기']}", flush=True)
    return s


def judge_one(case, gold, res, rules):
    turn = C.gather(res.get("events") or [])
    ext = E.extract(turn, rules)
    verdict = E.judge(gold or {}, ext, res, turn)
    return E.parse_log_row(case, res, ext), E.gis_log_row(case, gold or {}, res, ext, verdict), verdict


def rescore(run_dir, answer, testset, intent_map):
    """원문(raw/*.json)으로 두 로그를 다시 만들고 채점 — 정답·대응표를 고친 뒤 개발 중 확인용(공식 시험 결과는 다시 묻지 않은 원래 로그)"""
    snap = os.path.join(run_dir, "data")
    pick = lambda p, name: p or next(iter(sorted(glob.glob(os.path.join(snap, name)))), None)
    answer, testset, intent_map = pick(answer, "answer_ai6.*"), pick(testset, "testset_ai6.*"), pick(intent_map, "intent_map_ai6.*")
    rules = E.load_rules(intent_map) if intent_map else E.load_rules()
    gold = {str(r.get("사례ID")): r for r in E.read_rows(answer)}
    cases = {str(r.get("사례ID")): r for r in E.read_rows(testset)}
    keep = os.path.join(run_dir, "original_logs")                 # 처음 실행한 로그(공식 결과)는 한 번만 보관하고 다시 덮지 않음
    if not os.path.isdir(keep):
        os.makedirs(keep)
        names = [E.PARSE_LOG, E.GIS_LOG, "summary.json", "result.xlsx"] + \
            [os.path.basename(x) for x in glob.glob(os.path.join(run_dir, "result_*.csv"))]   # pandas 없을 때의 표별 CSV
        for name in names:
            if os.path.exists(os.path.join(run_dir, name)):
                shutil.copy2(os.path.join(run_dir, name), keep)
    parse_rows, gis_rows = [], []
    for p in sorted(glob.glob(os.path.join(run_dir, "raw", "*.json"))):
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        cid = str(raw.get("사례ID") or os.path.splitext(os.path.basename(p))[0])
        res = dict(raw.get("response") or {})
        res["events"] = [(e.get("event"), e.get("data")) for e in res.get("events") or []]
        pr, gr, _ = judge_one(cases.get(cid, {"사례ID": cid}), gold.get(cid), res, rules)
        parse_rows.append(pr)
        gis_rows.append(gr)
    write_jsonl(os.path.join(run_dir, E.PARSE_LOG), parse_rows)
    write_jsonl(os.path.join(run_dir, E.GIS_LOG), gis_rows)
    mp = os.path.join(run_dir, "meta.json")
    meta = json.load(open(mp, encoding="utf-8")) if os.path.exists(mp) else {}
    meta.setdefault("rescored", []).append(dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), answer_sha256=sha256(answer),
                                                intent_map_sha256=sha256(intent_map)))
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, default=str)
    return score_run(run_dir, testset)


def main():
    ap = argparse.ArgumentParser(description="[AI 6] 자연어 기반 지도 제어 시험 실행")
    ap.add_argument("--testset")
    ap.add_argument("--answer")
    ap.add_argument("--intent-map", help="의도 대응표 (없으면 ai6_eval.DEFAULT_RULES)")
    ap.add_argument("--cases", help="실행할 사례ID만 (쉼표 구분, 비우면 전체)")
    ap.add_argument("--base", help=f"공간오케 주소 (기본 {C.BASE})")
    ap.add_argument("--deadline", type=float, help=f"사례당 응답 대기 상한 초 (기본 {C.DEADLINE:g})")
    ap.add_argument("--run-dir")
    ap.add_argument("--resume", action="store_true", help="아직 안 보낸 사례만 이어서 실행")
    ap.add_argument("--retry-errors", action="store_true", help="이어서 실행 때 '응답 오류' 사례도 다시 보냄(기록이 남음)")
    ap.add_argument("--rescore", help="다시 묻지 않고 이 결과 폴더를 원문으로 재채점")
    ap.add_argument("--no-precheck", action="store_true", help="사전 점검을 건너뜀(개발용)")
    ap.add_argument("--probe", help="사전 점검 때 공간오케에 보낼 주소 검색 명령 1건(V-World 경유 확인)")
    ap.add_argument("--status-file")
    a = ap.parse_args()
    if a.rescore:
        rescore(a.rescore, a.answer, a.testset, a.intent_map)
        return

    base = (a.base or C.BASE).rstrip("/")
    deadline = float(a.deadline or C.DEADLINE)
    ts = E.read_rows(a.testset)
    if not ts:
        raise SystemExit(f"시험셋을 읽지 못했습니다: {a.testset}")
    if "사례ID" not in ts[0] or "명령문" not in ts[0]:
        raise SystemExit(f"시험셋에 '사례ID', '명령문' 컬럼이 필요합니다. 현재: {list(ts[0])}")
    answers = {str(r.get("사례ID")): r for r in E.read_rows(a.answer)} if a.answer else {}
    rules = E.load_rules(a.intent_map) if a.intent_map else E.load_rules()
    ts = [r for r in ts if str(r.get("사례ID", "")).strip()]
    if a.cases:
        want = {c.strip() for c in a.cases.split(",") if c.strip()}
        ts = [r for r in ts if r["사례ID"] in want]

    freeze_path = os.path.join(os.path.dirname(os.path.abspath(a.testset)), FREEZE_FILE)
    frozen = freeze_check(freeze_path, freeze_record(a.testset, a.answer, a.intent_map, deadline))
    if frozen:                                             # 규격: 의도 대응표는 시험 전에 확정하고 시험 기간 중 바꾸지 않음
        refuse(a.status_file, "동결 이후 바뀐 항목이 있어 실행하지 않아요: " + ", ".join(frozen)
               + " — 의도한 변경이면 화면에서 동결을 풀고 다시 동결하세요.")

    run_dir = a.run_dir or os.path.join("spec", "AI6", time.strftime("%Y%m%d_%H%M%S"))
    raw_dir = os.path.join(run_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    snap = os.path.join(run_dir, "data")
    os.makedirs(snap, exist_ok=True)
    for p in (a.testset, a.answer, a.intent_map):
        if p and os.path.exists(p) and not os.path.exists(os.path.join(snap, os.path.basename(p))):
            shutil.copy2(p, snap)
    parse_path, gis_path = os.path.join(run_dir, E.PARSE_LOG), os.path.join(run_dir, E.GIS_LOG)
    att_path = os.path.join(run_dir, "attempts.jsonl")      # 보내기 직전에 한 줄 — 이어서 실행 때 다시 보내지 않는 근거

    done = set()
    if a.resume:
        gis_rows = read_jsonl(gis_path)
        if a.retry_errors:                                 # 응답 오류 줄을 지우고 다시 보냄 (지운 줄은 retried.jsonl 에 보관)
            drop = {r["사례ID"] for r in gis_rows if r.get("오류원인") == "응답 오류"}
            if drop:
                for path in (gis_path, parse_path):
                    rows = read_jsonl(path)
                    for r in rows:
                        if r.get("사례ID") in drop:
                            append_jsonl(os.path.join(run_dir, "retried.jsonl"), {"file": os.path.basename(path), **r})
                    write_jsonl(path, [r for r in rows if r.get("사례ID") not in drop])
                gis_rows = [r for r in gis_rows if r.get("사례ID") not in drop]
        done = {str(r.get("사례ID")) for r in gis_rows}
        sent = {str(r.get("사례ID")): r for r in read_jsonl(att_path)}
        for r in [r for r in ts if r["사례ID"] in sent and r["사례ID"] not in done]:
            if a.retry_errors:                             # 예비 실행에서만 다시 보냄
                continue
            at = sent[r["사례ID"]]
            res = dict(url=f"{base}/api/chat", request={"message": r["명령문"], "thread_id": at.get("thread_id")},
                       thread_id=at.get("thread_id"), http_status=None, events=[], ended_by="exception", t_first_event=None,
                       elapsed=None, deadline=deadline, 요청시각=at.get("at"),
                       error="QA 실행이 응답을 기록하기 전에 멈춤 — 명령 1회 입력 원칙에 따라 다시 보내지 않음")
            pr, gr, _ = judge_one(r, answers.get(r["사례ID"]), res, rules)
            append_jsonl(parse_path, pr)
            append_jsonl(gis_path, gr)
            done.add(r["사례ID"])
    plan = [r for r in ts if r["사례ID"] not in done]

    outside = any(not re.match(r"^AI6-\d{4}$", str(r.get("사례ID"))) for r in ts)   # 예비 실측(P6-…) 등 → 공식 결과 아님
    mp = os.path.join(run_dir, "meta.json")
    if a.resume and os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            meta = {**json.load(f)}
        meta.setdefault("resumed", []).append(time.strftime("%Y-%m-%d %H:%M:%S"))
        if a.retry_errors:
            meta["공식"] = False
            meta.setdefault("예비사유", []).append("응답 오류 재전송")
    else:
        meta = dict(item="AI6", 시험항목="[AI 6] 자연어 기반 지도 제어 기능", base=base, endpoint=f"{base}/api/chat",
                    deadline_s=deadline, origin=C.ORIGIN, cases=[r["사례ID"] for r in ts] if a.cases else [],
                    testset=a.testset, testset_sha256=sha256(a.testset), answer=a.answer, answer_sha256=sha256(a.answer),
                    intent_map=a.intent_map, intent_map_sha256=sha256(a.intent_map),
                    intent_rules=[{k: (sorted(v) if isinstance(v, set) else v) for k, v in r.items()} for r in rules],
                    data_check=E.validate(ts if not a.cases else E.read_rows(a.testset), list(answers.values()), rules),
                    intent_rules_sha256=rules_sha256(a.intent_map), criteria_sha256=E.criteria_fingerprint(),
                    frozen=os.path.exists(freeze_path), 공식=not (a.cases or a.retry_errors or a.no_precheck or outside),
                    예비사유=[x for x, on in (("일부 ID만 실행", a.cases), ("응답 오류 재전송", a.retry_errors),
                                              ("사전 점검 생략", a.no_precheck), ("시험셋 밖 ID(예비 실측)", outside)) if on],
                    gap_s=GAP, started=time.strftime("%Y-%m-%d %H:%M:%S"))
        if not a.no_precheck:
            meta["precheck"] = C.precheck(base, probe_message=a.probe)
            meta["ready"] = C.ready_info(base)
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, default=str)

    argv = sys.argv[1:]
    if "--run-dir" not in argv:
        argv += ["--run-dir", run_dir]
    total = len(plan) + len(done)
    status = dict(state="running", pid=os.getpid(), started=time.time(), script=SCRIPT, mode="성과지표 AI6",
                  run_dir=run_dir, out=os.path.join(run_dir, "progress.xlsx"), total=total, done=len(done), current=None,
                  message="이어서 실행" if a.resume else "", argv=argv)
    bad = [r for r in meta.get("precheck") or [] if r.get("결과") != "정상"]
    if bad and not a.resume:                               # 사전 점검 실패 → 실행하지 않음(규격 시험환경 미충족)
        status.update(state="error", message="사전 점검 실패: " + ", ".join(f"{r['항목']}({r['내용'][:40]})" for r in bad),
                      finished=time.time())
        write_status(a.status_file, status)
        raise SystemExit(status["message"])
    write_status(a.status_file, status)
    alive = threading.Event()

    def heartbeat():
        while not alive.wait(3):
            write_status(a.status_file, status)
    if a.status_file:
        threading.Thread(target=heartbeat, daemon=True).start()

    prog = [{"사례ID": r.get("사례ID"), "회차": 1, "명령유형": r.get("명령유형"), "질의": r.get("명령문"), "판단유형": r.get("판정"),
             "오류원인": r.get("오류원인"), "소요초": r.get("소요초")} for r in read_jsonl(gis_path)]
    print(f"[AI6] 명령 {len(plan)}건 · 공간오케 {base} · 대기 상한 {deadline:g}초 · 저장 위치 {run_dir}", flush=True)
    fails = 0
    try:
        for r in plan:
            if os.path.exists(STOP_FILE):
                os.remove(STOP_FILE)
                status.update(state="stopped", message=f"사용자가 중지 ({status['done']}/{total}건 완료)")
                break
            cid, cmd = r["사례ID"], r["명령문"]                 # 문장은 그대로 — strip 도 하지 않음(시험셋에 적힌 그대로 입력)
            status["current"] = dict(case_id=cid, 회차=1, 질의=cmd)
            write_status(a.status_file, status)
            sent_at = time.strftime("%Y-%m-%d %H:%M:%S")
            tid = C.new_thread_id(cid)
            append_jsonl(att_path, {"사례ID": cid, "thread_id": tid, "at": sent_at})
            res = C.stream_chat(cmd, thread_id=tid, base=base, deadline=deadline)
            res["요청시각"] = sent_at
            with open(os.path.join(raw_dir, f"{cid}.json"), "w", encoding="utf-8") as f:
                json.dump({"사례ID": cid, "명령문": cmd, "response": {**{k: v for k, v in res.items() if k != "events"},
                                                                    "events": [{"event": e, "data": d} for e, d in res["events"]]}},
                          f, ensure_ascii=False, default=str)
            pr, gr, v = judge_one(r, answers.get(cid), res, rules)
            append_jsonl(parse_path, pr)
            append_jsonl(gis_path, gr)
            prog.append({"사례ID": cid, "회차": 1, "명령유형": r.get("명령유형"), "질의": cmd, "판단유형": gr["판정"],
                         "오류원인": gr["오류원인"], "①": v["①"], "②": v["②"], "③": v["③"], "소요초": res.get("elapsed")})
            try:
                import pandas as pd
                pd.DataFrame(prog).to_excel(status["out"], index=False)
            except (ImportError, OSError):
                pass
            print(f"[{cid}] {gr['판정']} {gr['오류원인']} ({res.get('elapsed')}s) {' | '.join(v['불일치'])[:200]}", flush=True)
            status.update(done=status["done"] + 1, current=None)
            write_status(a.status_file, status)
            conn_fail = res.get("ended_by") == "exception" or (res.get("ended_by") == "http" and (res.get("http_status") or 0) >= 500)
            fails = fails + 1 if conn_fail else 0
            if fails >= MAX_CONN_FAILS:
                status.update(state="error", message=f"연결 실패 {fails}회 연속 ({status['done']}/{total}건) — 시험 환경 문제로 보고 멈췄어요. "
                                                     "원인을 고친 뒤 전체를 다시 실행하세요(규격: 환경이 바뀌면 전체 재수행).")
                break
            pause(COOLDOWN if res.get("ended_by") == "deadline" else GAP)
        if status["state"] == "running":
            snap_ts = os.path.join(snap, os.path.basename(a.testset)) if os.path.exists(os.path.join(snap, os.path.basename(a.testset))) else a.testset
            s = score_run(run_dir, snap_ts)
            msg = f"완료 ({status['done']}/{total}건)"
            if s["value"] is not None:
                msg += f" · 자연어 기반 지도 제어 정확도 {s['value']:.2f} % {'달성' if s['passed'] else '미달성'}"
            status.update(state="done", message=msg)
        print(status.get("message"), flush=True)
    except Exception as e:                                  # noqa: BLE001
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
    except BaseException as _e:                                      # noqa: BLE001
        _sf = sys.argv[sys.argv.index("--status-file") + 1] if "--status-file" in sys.argv else None
        if _sf and not isinstance(_e, (KeyboardInterrupt, SystemExit)):
            try:
                with open(_sf, encoding="utf-8") as _f:
                    _st = json.load(_f)
            except Exception:                                       # noqa: BLE001
                _st = {}
            if _st.get("state") in (None, "running"):
                _st.update(state="error", message=f"{type(_e).__name__}: {_e}", finished=time.time())
                write_status(_sf, _st)
        raise
