"""
성과지표 시험 실행기 — [AI 7] 자연어 기반 3D 시뮬레이션 명령 정확도
--------------------------------------------------------------
시험셋의 자연어 3D 명령을 공간오케(115:17006 — 3D 명령도 여기서 map_3d 에이전트로 감, 2026-11-13 실측)에 한 건씩 보내고,
명령 해석 로그 · 3D 실행 결과 로그 · 결과표를 만듭니다. 구조는 spec_runner_ai6.py 와 같습니다.

  python spec_runner_ai7.py --testset spec_data/testset_ai7.csv --answer spec_data/answer_ai7.csv \
                            [--intent-map spec_data/intent_map_ai7.csv] [--cases AI7-0001,AI7-0036] \
                            [--base http://192.168.200.115:17006] [--deadline 300]
  python spec_runner_ai7.py --rescore spec/AI7/<시각> [--answer ...]   # 다시 묻지 않고 원문으로 재채점(개발용, 새 폴더)
  python spec_runner_ai7.py --freeze                                  # 운영자 동결 (spec_data/freeze_ai7.json)

결과 폴더: spec/AI7/<시각>/
  raw/<사례ID>.json                  요청·응답 원문(SSE 이벤트 전부)
  command_parse_log_ai7.jsonl        명령 1건당 1행 — 해석 의도·핵심 파라미터·실행 대상 기능·규격 출력 JSON
  sim_execution_log_ai7.jsonl        명령 1건당 1행 — 3D 명령·호출 결과·정답 대조·판정
  data/ · meta.json · progress.xlsx · result.xlsx · summary.json

판정 범위: ① 의도 · ② 파라미터. ③ 3D 실행(실행 후 3D 상태·캡처)은 시험 범위 밖이라 판정하지 않습니다
(AIPS 사전진단 성능테스트 — 3D 분석 파라미터가 제대로 들어오는지 확인, ai7_eval.JUDGE_EXEC). 동결 뒤 50건 전체 실행 = 공식.
선행상태(카메라 방위·LOD·기상 등)는 기록만 합니다.
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
import uuid

import ai6_client as C
import ai7_eval as E
from spec_runner_ai6 import (STOP_FILE, append_jsonl, pause, read_jsonl, save_meta, save_tables, sha256,  # noqa: F401
                             write_jsonl, write_status)

SCRIPT = "spec_runner_ai7.py"
MODE = "성과지표 AI7"
BASE = os.environ.get("AI7_BASE", C.BASE).rstrip("/")
DEADLINE = float(os.environ.get("AI7_DEADLINE", "300"))   # 규격서 6.2.7 '시뮬레이션 실행 대기 상한 ○분' 미정 — 임시 300초(개발팀 확인 후 확정)
ORIGIN = os.environ.get("AI7_ORIGIN", "qa-ai7")             # X-Request-Origin — 공간오케 기록에서 AI7 QA 질의를 따로 모으는 표지
GAP = float(os.environ.get("AI7_GAP", "2"))
COOLDOWN = float(os.environ.get("AI7_COOLDOWN", "10"))
MAX_CONN_FAILS = 3
MAX_RESP_ERRORS = 3
FREEZE_FILE = "freeze_ai7.json"
STOP_NOTE = "사용자 중지 — 끝까지 돌리기 전"
RUN_NOTE = "끝까지 실행하기 전(실행 중이거나 중간에 멈춤)"
CRASH_NOTE = "비정상 종료"
PENDING_NOTES = (STOP_NOTE, RUN_NOTE)
TABLES = ("총괄 결과", "명령 유형 7종별 세부 결과", "오류 원인 유형별 건수", "사례별 판정")


def new_thread_id(case_id=""):
    tag = "".join(ch for ch in str(case_id) if ch.isalnum() or ch in "-_")[:24]
    return f"qa-ai7-{tag}-{uuid.uuid4().hex[:8]}" if tag else f"qa-ai7-{uuid.uuid4().hex[:12]}"


def refuse(status_file, msg):
    write_status(status_file, dict(state="error", script=SCRIPT, mode=MODE, message=msg,
                                   started=time.time(), finished=time.time(), total=0, done=0))
    raise SystemExit(msg)


def rules_sha256(intent_map):
    return sha256(intent_map) if intent_map else "기본:" + hashlib.sha256(E.rules_csv().encode("utf-8")).hexdigest()


def freeze_record(testset, answer, intent_map, deadline, base=None):
    return dict(testset_sha256=sha256(testset), answer_sha256=sha256(answer), intent_map_sha256=rules_sha256(intent_map),
                criteria_sha256=E.criteria_fingerprint(), deadline_s=float(deadline), base=(base or BASE).rstrip("/"))


def freeze_check(freeze_path, cur):
    if not os.path.exists(freeze_path):
        return None
    with open(freeze_path, encoding="utf-8") as f:
        fz = json.load(f)
    names = {"testset_sha256": "시험셋", "answer_sha256": "정답셋", "intent_map_sha256": "의도 대응표",
             "criteria_sha256": "판정 기준(규칙·옵션)", "deadline_s": "응답 대기 상한", "base": "공간오케 주소"}
    return [names[k] for k in names if k in fz and str(fz.get(k)) != str(cur.get(k))]


def resume_outlook(run_dir):
    """멈춘 실행 폴더 → 보냈지만 판정 기록도 응답 원문도 없는 사례 ID (이어서 돌려도 다시 보내지 않음 — 공식이 될 수 없음)"""
    done = {str(r.get("사례ID")) for r in read_jsonl(os.path.join(run_dir, E.SIM_LOG))}
    sent = [str(r.get("사례ID")) for r in read_jsonl(os.path.join(run_dir, "attempts.jsonl"))]
    return [c for c in dict.fromkeys(sent) if c not in done and not os.path.exists(os.path.join(run_dir, "raw", f"{c}.json"))]


def score_run(run_dir, testset_path, quiet=False):
    """실행 폴더 채점 → summary.json · result.xlsx"""
    sim = read_jsonl(os.path.join(run_dir, E.SIM_LOG))
    ts = E.read_rows(testset_path)
    meta = {}
    mp = os.path.join(run_dir, "meta.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
    planned = set(meta.get("cases") or [])
    if planned:
        ts = [t for t in ts if str(t.get("사례ID")) in planned]
    s = E.score(sim, ts, parse_rows=read_jsonl(os.path.join(run_dir, E.PARSE_LOG)))
    tables = dict(zip(TABLES, (s["총괄"], s["유형별"], s["오류원인별"], s["사례별"])))
    files = save_tables(os.path.join(run_dir, "result.xlsx"), tables)
    summ = dict(item="AI7", value=s["value"], target=E.TARGET, cmp=">=", passed=s["passed"], n=s["n"], ok=s["ok"],
                aux={"응답 오류": s["응답오류"], "되묻기": s["되묻기"], "③ 판정": "함" if E.JUDGE_EXEC else "시험 범위 밖(①② 기준)",
                     "유형별": {r["명령 유형"]: r["정확도(%)"] for r in s["유형별"]},
                     "오류원인별": {r["오류 원인"]: r["건수"] for r in s["오류원인별"]}},
                files=files, scored=time.time())
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summ, f, ensure_ascii=False, indent=1, default=str)
    if not quiet:
        print(f"[AI7] 정확도 {'-' if s['value'] is None else format(s['value'], '.1f')} % ({s['ok']}/{s['n']}) · "
              f"응답 오류 {s['응답오류']} · 되묻기 {s['되묻기']}", flush=True)
    return s


def judge_one(case, gold, res, rules):
    turn = C.gather(res.get("events") or [])
    ext = E.extract(turn, rules)
    verdict = E.judge(gold or {}, ext, res, turn)
    return E.parse_log_row(case, res, ext), E.sim_log_row(case, gold or {}, res, ext, verdict), verdict


def progress_row(gr, pr=None):
    """실행 중 화면 표(progress.xlsx) 한 줄"""
    ox = lambda x: "O" if x is True else "X" if x is False else "-"   # noqa: E731
    row = {"사례ID": gr.get("사례ID"), "회차": 1, "명령유형": gr.get("명령유형"), "질의": gr.get("명령문")}
    row.update(E.spec_cells((pr or {}).get("규격출력")))
    row.update({"①": ox(gr.get("①의도")), "②": ox(gr.get("②파라미터")), "③": ox(gr.get("③3D실행")),
                "판정": gr.get("판정"), "오류원인": gr.get("오류원인") or "",
                "불일치": " | ".join(gr.get("불일치내용") or []), "소요초": gr.get("소요초")})
    return row


def load_raw(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    res = dict(raw.get("response") or {})
    res["events"] = [(e.get("event"), e.get("data")) for e in res.get("events") or []]
    return raw, res


def rescore(run_dir, answer, testset, intent_map):
    """원문(raw/*.json)을 다시 판정해 새 폴더 '<실행 폴더>_재판정_<시각>'에 씀 (원래 폴더는 그대로, 결과는 예비)"""
    src = os.path.normpath(run_dir)
    snap = os.path.join(src, "data")
    pick = lambda p, name: p or next(iter(sorted(glob.glob(os.path.join(snap, name)))), None)   # noqa: E731
    answer, testset, intent_map = pick(answer, "answer_ai7.*"), pick(testset, "testset_ai7.*"), pick(intent_map, "intent_map_ai7.*")
    rules = E.load_rules(intent_map) if intent_map else E.load_rules()
    gold = {str(r.get("사례ID")): r for r in E.read_rows(answer)}
    cases = {str(r.get("사례ID")): r for r in E.read_rows(testset)}
    root = re.sub(r"(_재판정_\d{6}(_\d+)?)+$", "", src)
    new = f"{root}_재판정_{time.strftime('%H%M%S')}"
    k = 2
    while os.path.exists(new):
        new, k = f"{root}_재판정_{time.strftime('%H%M%S')}_{k}", k + 1
    shutil.copytree(os.path.join(src, "raw"), os.path.join(new, "raw"))
    os.makedirs(os.path.join(new, "data"), exist_ok=True)
    for p in (testset, answer, intent_map):
        if p and os.path.exists(p):
            shutil.copy2(p, os.path.join(new, "data", os.path.basename(p)))
    parse_rows, sim_rows = [], []
    for p in sorted(glob.glob(os.path.join(new, "raw", "*.json"))):
        raw, res = load_raw(p)
        cid = str(raw.get("사례ID") or os.path.splitext(os.path.basename(p))[0])
        pr, gr, _ = judge_one(cases.get(cid, {"사례ID": cid, "명령문": raw.get("명령문")}), gold.get(cid), res, rules)
        parse_rows.append(pr)
        sim_rows.append(gr)
    write_jsonl(os.path.join(new, E.PARSE_LOG), parse_rows)
    write_jsonl(os.path.join(new, E.SIM_LOG), sim_rows)
    meta = {}
    om = os.path.join(src, "meta.json")
    if os.path.exists(om):
        with open(om, encoding="utf-8") as f:
            meta = json.load(f)
    first = meta.get("원본") or src
    why = [w for w in meta.get("예비사유") or [] if not str(w).startswith("다시 판정(")
           and w not in PENDING_NOTES and not str(w).startswith(CRASH_NOTE)]
    meta.pop("완료전", None)
    meta.update(원본=first, 공식=False, 예비사유=why + [f"다시 판정(원본 {os.path.basename(first)})"],
                rescored=dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), answer=answer, answer_sha256=sha256(answer),
                              intent_map=intent_map or "기본 대응표", intent_map_sha256=rules_sha256(intent_map)),
                criteria_sha256=E.criteria_fingerprint())
    save_meta(os.path.join(new, "meta.json"), meta)
    return new, score_run(new, os.path.join(new, "data", os.path.basename(testset)) if testset else testset)


def main():
    ap = argparse.ArgumentParser(description="[AI 7] 자연어 기반 3D 시뮬레이션 명령 시험 실행")
    ap.add_argument("--testset")
    ap.add_argument("--answer")
    ap.add_argument("--intent-map", help="의도 대응표 (없으면 ai7_eval.DEFAULT_RULES)")
    ap.add_argument("--cases", help="실행할 사례ID만 (쉼표 구분, 비우면 전체)")
    ap.add_argument("--base", help=f"공간오케 주소 (기본 {BASE})")
    ap.add_argument("--deadline", type=float, help=f"사례당 응답 대기 상한 초 (기본 {DEADLINE:g})")
    ap.add_argument("--run-dir")
    ap.add_argument("--resume", action="store_true", help="아직 안 보낸 사례만 이어서 실행")
    ap.add_argument("--rescore", help="다시 묻지 않고 이 결과 폴더를 원문으로 재채점")
    ap.add_argument("--no-precheck", action="store_true", help="사전 점검을 건너뜀(개발용)")
    ap.add_argument("--probe", help="사전 점검 때 공간오케에 보낼 주소 검색 명령 1건")
    ap.add_argument("--status-file")
    ap.add_argument("--freeze", action="store_true", help="운영자용: 지금 시험 데이터·판정 기준으로 동결 기록(spec_data/freeze_ai7.json)")
    a = ap.parse_args()
    if a.freeze:
        ts_p = a.testset or os.path.join("spec_data", "testset_ai7.csv")
        d = os.path.dirname(ts_p) or "."
        an_p = a.answer or os.path.join(d, "answer_ai7.csv")
        im_p = a.intent_map or (os.path.join(d, "intent_map_ai7.csv") if os.path.exists(os.path.join(d, "intent_map_ai7.csv")) else None)
        rules = E.load_rules(im_p) if im_p else E.load_rules()
        probs = E.blocking(E.validate(E.read_rows(ts_p), E.read_rows(an_p), rules))
        if probs:
            raise SystemExit(f"시험 데이터 점검 문제 {len(probs)}건이 있어 동결하지 않아요: " + " / ".join(probs[:3]))
        rec = freeze_record(ts_p, an_p, im_p, float(a.deadline or DEADLINE), a.base)
        path = os.path.join(os.path.dirname(os.path.abspath(ts_p)), FREEZE_FILE)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({**rec, "frozen_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "files": {"testset": ts_p, "answer": an_p, "intent_map": im_p or "기본 대응표"}}, f, ensure_ascii=False, indent=1)
        print(f"[AI7] 동결 기록 저장: {path}", flush=True)
        return
    if a.rescore:
        new, _ = rescore(a.rescore, a.answer, a.testset, a.intent_map)
        print(f"[AI7] 다시 판정 결과 폴더: {new} (예비 — 원래 실행 폴더는 그대로)", flush=True)
        return

    live = (a.testset, a.answer, a.intent_map)
    if a.resume and a.run_dir:                             # 이어서 실행은 처음 실행 때 사본(data/)으로
        cp = lambda p: os.path.join(a.run_dir, "data", os.path.basename(p))   # noqa: E731
        a.testset, a.answer, a.intent_map = [cp(p) if p and os.path.exists(cp(p)) else p for p in live]
    base = (a.base or BASE).rstrip("/")
    deadline = float(a.deadline or DEADLINE)
    ts = E.read_rows(a.testset)
    if not ts:
        refuse(a.status_file, f"시험셋을 읽지 못했습니다: {a.testset}")
    if "사례ID" not in ts[0] or "명령문" not in ts[0]:
        refuse(a.status_file, f"시험셋에 '사례ID', '명령문' 컬럼이 필요합니다. 현재: {list(ts[0])}")
    answers = {str(r.get("사례ID")): r for r in E.read_rows(a.answer)} if a.answer else {}
    rules = E.load_rules(a.intent_map) if a.intent_map else E.load_rules()
    ts = [r for r in ts if str(r.get("사례ID", "")).strip()]
    if a.cases:
        want = {c.strip() for c in a.cases.split(",") if c.strip()}
        ts = [r for r in ts if r["사례ID"] in want]
    outside = any(not re.match(r"^AI7-\d{4}$", str(r.get("사례ID"))) for r in ts)
    pilot = bool(a.cases or a.no_precheck or outside)
    data_check = E.validate(ts if not a.cases else E.read_rows(a.testset), list(answers.values()), rules)
    if not pilot and E.blocking(data_check):
        refuse(a.status_file, f"시험 데이터 점검 문제 {len(E.blocking(data_check))}건이 있어 전체 실행을 시작하지 않아요: "
               + " / ".join(E.blocking(data_check)[:3]) + (" …" if len(E.blocking(data_check)) > 3 else "")
               + " — 일부 사례만 돌리려면 --cases")
    freeze_path = os.path.join(os.path.dirname(os.path.abspath(live[0])), FREEZE_FILE)
    frozen = freeze_check(freeze_path, freeze_record(*live, deadline, base))
    if frozen:
        refuse(a.status_file, "동결 이후 바뀐 항목이 있어 실행하지 않아요: " + ", ".join(frozen)
               + " — 의도한 변경이면 운영자가 다시 동결하세요(python spec_runner_ai7.py --freeze).")

    run_dir = a.run_dir or os.path.join("spec", "AI7", time.strftime("%Y%m%d_%H%M%S"))
    raw_dir = os.path.join(run_dir, "raw")
    new_dir = not os.path.exists(run_dir)
    os.makedirs(raw_dir, exist_ok=True)
    snap = os.path.join(run_dir, "data")
    os.makedirs(snap, exist_ok=True)
    for p in (a.testset, a.answer, a.intent_map):
        if p and os.path.exists(p) and not os.path.exists(os.path.join(snap, os.path.basename(p))):
            shutil.copy2(p, snap)
    parse_path, sim_path = os.path.join(run_dir, E.PARSE_LOG), os.path.join(run_dir, E.SIM_LOG)
    att_path = os.path.join(run_dir, "attempts.jsonl")

    done = set()
    if a.resume:                                           # 보낸 사례는 다시 보내지 않음(명령 1회 입력) — 원문이 있으면 그것으로 판정
        done = {str(r.get("사례ID")) for r in read_jsonl(sim_path)}
        for at in read_jsonl(att_path):
            cid = str(at.get("사례ID"))
            r = next((x for x in ts if x["사례ID"] == cid), None)
            if r is None or cid in done:
                continue
            rp = os.path.join(raw_dir, f"{cid}.json")
            if os.path.exists(rp):
                _, res = load_raw(rp)
            else:
                res = dict(url=f"{base}/api/chat", request={"message": r["명령문"], "thread_id": at.get("thread_id")},
                           thread_id=at.get("thread_id"), events=[], ended_by="exception", elapsed=None,
                           error="QA 실행이 응답을 기록하기 전에 멈춤 — 명령 1회 입력 원칙에 따라 다시 보내지 않음")
            pr, gr, _ = judge_one(r, answers.get(cid), res, rules)
            append_jsonl(parse_path, pr)
            append_jsonl(sim_path, gr)
            done.add(cid)
    plan = [r for r in ts if r["사례ID"] not in done]

    argv = sys.argv[1:]
    if "--run-dir" not in argv:
        argv += ["--run-dir", run_dir]
    total = len(plan) + len(done)
    status = dict(state="running", pid=os.getpid(), started=time.time(), script=SCRIPT, mode=MODE,
                  run_dir=run_dir, out=os.path.join(run_dir, "progress.xlsx"), total=total, done=len(done), current=None,
                  message="사전 점검 중…", argv=argv)
    write_status(a.status_file, status)
    alive = threading.Event()

    def heartbeat():
        while not alive.wait(3):
            write_status(a.status_file, status)
    if a.status_file:
        threading.Thread(target=heartbeat, daemon=True).start()

    mp = os.path.join(run_dir, "meta.json")
    fresh = None
    if a.resume and os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
        meta.setdefault("resumed", []).append(time.strftime("%Y-%m-%d %H:%M:%S"))
    else:
        why = [x for x, on in (("일부 ID만 실행", a.cases), ("사전 점검 생략", a.no_precheck), ("시험셋 밖 ID(예비 실측)", outside),
                               ("동결 전 실행", not os.path.exists(freeze_path))) if on]
        meta = dict(item="AI7", 시험항목="[AI 7] 자연어 기반 3D 시뮬레이션 명령 처리 기능", base=base, endpoint=f"{base}/api/chat",
                    deadline_s=deadline, origin=ORIGIN, cases=[r["사례ID"] for r in ts] if a.cases else [],
                    testset=a.testset, testset_sha256=sha256(a.testset), answer=a.answer, answer_sha256=sha256(a.answer),
                    intent_map=a.intent_map, intent_map_sha256=sha256(a.intent_map),
                    intent_rules=[{k: (sorted(v) if isinstance(v, set) else v) for k, v in r.items()} for r in rules],
                    data_check=data_check, intent_rules_sha256=rules_sha256(a.intent_map),
                    criteria_sha256=E.criteria_fingerprint(), judge_exec=E.JUDGE_EXEC,
                    frozen=os.path.exists(freeze_path), 공식=False, 예비사유=why, gap_s=GAP,
                    started=time.strftime("%Y-%m-%d %H:%M:%S"))
        if meta["frozen"] and not pilot:
            meta.update(완료전=True, 예비사유=meta["예비사유"] + [RUN_NOTE])
        if not a.no_precheck:
            fresh = meta["precheck"] = C.precheck(base, probe_message=a.probe)
            meta["ready"] = C.ready_info(base)
            meta["llm"] = C.llm_info(base)
    save_meta(mp, meta)

    status["message"] = "이어서 실행" if a.resume else ""
    bad = [r for r in fresh or [] if r.get("결과") != "정상"]
    if bad:
        alive.set()
        status.update(state="error", message="사전 점검 실패: " + ", ".join(f"{r['항목']}({r['내용'][:40]})" for r in bad),
                      finished=time.time())
        if new_dir:
            shutil.rmtree(run_dir, ignore_errors=True)
            status.pop("argv", None)
        write_status(a.status_file, status)
        raise SystemExit(status["message"])
    write_status(a.status_file, status)

    parsed = {str(p.get("사례ID")): p for p in read_jsonl(parse_path)}
    prog = [progress_row(g, parsed.get(str(g.get("사례ID")))) for g in read_jsonl(sim_path)]
    print(f"[AI7] 명령 {len(plan)}건 · 공간오케 {base} · 대기 상한 {deadline:g}초 · 저장 위치 {run_dir}", flush=True)
    fails = errs = 0
    try:
        for r in plan:
            if os.path.exists(STOP_FILE):
                os.remove(STOP_FILE)
                status.update(state="stopped", message=f"사용자가 중지 ({status['done']}/{total}건 완료)")
                if meta.get("완료전"):
                    meta.update(공식=False, 예비사유=[w for w in meta.get("예비사유") or [] if w not in PENDING_NOTES] + [STOP_NOTE])
                    save_meta(mp, meta)
                break
            cid, cmd = r["사례ID"], r["명령문"]                 # 문장은 그대로 — strip 도 하지 않음
            status["current"] = dict(case_id=cid, 회차=1, 질의=cmd)
            write_status(a.status_file, status)
            sent_at = time.strftime("%Y-%m-%d %H:%M:%S")
            tid = new_thread_id(cid)
            append_jsonl(att_path, {"사례ID": cid, "thread_id": tid, "at": sent_at})
            res = C.stream_chat(cmd, thread_id=tid, base=base, deadline=deadline, origin=ORIGIN)
            res["요청시각"] = sent_at
            with open(os.path.join(raw_dir, f"{cid}.json"), "w", encoding="utf-8") as f:
                json.dump({"사례ID": cid, "명령문": cmd, "선행상태": r.get("선행상태") or "",
                           "response": {**{k: v for k, v in res.items() if k != "events"},
                                        "events": [{"event": e, "data": d} for e, d in res["events"]]}},
                          f, ensure_ascii=False, default=str)
            pr, gr, v = judge_one(r, answers.get(cid), res, rules)
            append_jsonl(parse_path, pr)
            append_jsonl(sim_path, gr)
            prog.append(progress_row(gr, pr))
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
            errs = errs + 1 if gr.get("오류원인") == "응답 오류" else 0
            if errs >= MAX_RESP_ERRORS or fails >= MAX_CONN_FAILS:
                kind = f"연결 실패 {fails}회" if fails >= MAX_CONN_FAILS else f"응답 오류 {errs}건"
                status.update(state="error", message=f"{kind} 연속 ({status['done']}/{total}건) — 시스템·환경 문제로 보고 멈췄어요. "
                                                     f"마지막 오류: {str(gr.get('응답오류') or '')[:120]}")
                status.pop("argv", None)
                meta.pop("완료전", None)
                meta.update(공식=False, 예비사유=[w for w in meta.get("예비사유") or [] if w not in PENDING_NOTES]
                            + [f"{kind} 연속으로 멈춤 — 전체 재수행 대상"])
                save_meta(mp, meta)
                break
            pause(COOLDOWN if res.get("ended_by") == "deadline" else GAP)
        if status["state"] == "running":
            if meta.pop("완료전", None):
                why = [w for w in meta.get("예비사유") or [] if w not in PENDING_NOTES and not str(w).startswith(CRASH_NOTE)]
                fz = freeze_check(freeze_path, freeze_record(a.testset, a.answer, a.intent_map, deadline, base))
                if fz is None or fz:
                    why.append("실행을 마칠 때 동결 기록과 다름" + (f" ({', '.join(fz)})" if fz else " (동결 풀림)"))
                meta.update(예비사유=why, 공식=bool(meta.get("frozen")) and not why)
                save_meta(mp, meta)
            snap_ts = os.path.join(snap, os.path.basename(a.testset))
            s = score_run(run_dir, snap_ts if os.path.exists(snap_ts) else a.testset)
            msg = f"완료 ({status['done']}/{total}건)"
            if s["value"] is not None:
                msg += f" · 자연어 기반 3D 시뮬레이션 명령 정확도 {s['value']:.1f} %"
            status.update(state="done", message=msg)
        print(status.get("message"), flush=True)
    except Exception as e:                                  # noqa: BLE001
        status.update(state="error", message=f"{type(e).__name__}: {e}")
        if meta.get("완료전"):
            meta["예비사유"] = [w for w in meta.get("예비사유") or [] if w not in PENDING_NOTES] + [f"{CRASH_NOTE}({type(e).__name__})"]
            try:
                save_meta(mp, meta)
            except OSError:
                pass
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
