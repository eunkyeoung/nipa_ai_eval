"""
성과지표 시험 실행기 — [AI 6] 자연어 기반 지도 제어 정확도
------------------------------------------------------
시험셋의 자연어 지도 제어 명령을 공간오케(지도제어·공간분석 Sub 오케스트레이터)에 한 건씩 보내고,
명령 해석 로그 · 실행 결과 로그 · 결과표를 만듭니다. 화면(규격서 시험 → AI6)이 이 파일을 실행합니다.

  python spec_runner_ai6.py --testset spec_data/testset_ai6.csv --answer spec_data/answer_ai6.csv \
                            --intent-map spec_data/intent_map_ai6.csv [--cases AI6-0001,AI6-0002] \
                            [--base http://192.168.200.115:17006] [--deadline 180]
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
MAX_RESP_ERRORS = 3                                         # 응답 오류(시스템 오류·시간 초과 등)가 이만큼 이어지면 시스템 장애로 보고 멈춤(전체 재수행 대상)
#                                                             2026-11-12 공간분석 API(KGEOP) 시간 초과 때 22건이 모두 응답 오류였음
FREEZE_FILE = "freeze_ai6.json"                             # 시험 데이터 폴더의 동결 기록 — 있으면 해시가 같아야 실행
STOP_NOTE = "사용자 중지 — 끝까지 돌리기 전(↻ 이어서로 마저 돌리면 공식)"   # 동결한 전체 실행을 멈췄을 때의 예비 사유
RUN_NOTE = "끝까지 실행하기 전(실행 중이거나 중간에 멈춤)"   # 공식 조건 실행은 시작 때 예비로 쓰고, 정상으로 끝날 때만 공식으로 바꿈
CRASH_NOTE = "비정상 종료"                                   # 실행 중 예외로 멈춤 — 이어서 돌려 공식이 되는지는 resume_outlook() 이 알려 줌
INCOMPLETE_NOTE = "원본 실행이 끝까지 돌지 않음"            # 다시 판정 폴더: 원본이 중간에 멈춘 실행
PENDING_NOTES = (STOP_NOTE, RUN_NOTE)


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


def freeze_record(testset, answer, intent_map, deadline, base=None):
    """동결할 값 — 시험셋·정답셋·의도 대응표·판정 기준 지문·응답 대기 상한·공간오케 주소"""
    return dict(testset_sha256=sha256(testset), answer_sha256=sha256(answer), intent_map_sha256=rules_sha256(intent_map),
                criteria_sha256=E.criteria_fingerprint(), deadline_s=float(deadline), base=(base or C.BASE).rstrip("/"))


def freeze_check(freeze_path, cur):
    """동결 기록과 지금 값을 비교 → 다른 항목 목록 (동결 안 했으면 None)"""
    if not os.path.exists(freeze_path):
        return None
    with open(freeze_path, encoding="utf-8") as f:
        fz = json.load(f)
    names = {"testset_sha256": "시험셋", "answer_sha256": "정답셋", "intent_map_sha256": "의도 대응표",
             "criteria_sha256": "판정 기준(규칙·옵션)", "deadline_s": "응답 대기 상한", "base": "공간오케 주소"}
    return [names[k] for k in names if k in fz and str(fz.get(k)) != str(cur.get(k))]


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


def resume_outlook(run_dir):
    """멈춘 실행 폴더 → 보냈지만 판정 기록도 응답 원문도 없는 사례 ID 목록.
    '↻ 이어서' 때 이 사례들은 명령 1회 입력 원칙에 따라 다시 보내지 않고 응답 오류로 남으므로, 하나라도 있으면 이어서 돌려도 공식이 아님"""
    done = {str(r.get("사례ID")) for r in read_jsonl(os.path.join(run_dir, E.GIS_LOG))}
    sent = [str(r.get("사례ID")) for r in read_jsonl(os.path.join(run_dir, "attempts.jsonl"))]
    return [c for c in dict.fromkeys(sent) if c not in done and not os.path.exists(os.path.join(run_dir, "raw", f"{c}.json"))]


def save_meta(path, meta):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, default=str)


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
    ts = (E.read_rows(testset_path) if testset_path and os.path.exists(testset_path) else    # 시험셋을 못 찾으면 실행한 사례만으로(화면 채점과 같게)
          [{"사례ID": r.get("사례ID"), "명령유형": r.get("명령유형"), "명령문": r.get("명령문")} for r in gis])
    meta = {}
    mp = os.path.join(run_dir, "meta.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
    planned = set(meta.get("cases") or [])
    if planned:                                            # 일부 ID만 실행한 경우 그 ID만 분모
        ts = [t for t in ts if str(t.get("사례ID")) in planned]
    s = E.score(gis, ts, parse_rows=read_jsonl(os.path.join(run_dir, E.PARSE_LOG)))   # 사례별 표에 규격 출력 4칸
    none = [{"사례ID": "-", "명령 유형": "-", "명령문": "-", "오류 원인": "-", "오류 주체": "-",   # 오류가 없어도 시트는 남김
             "사유": f"오류 없음 — {s['n']}건 모두 정상 수행" if s["n"] else "실행한 사례 없음", "불일치내용": "-"}]
    tables = {"총괄 결과": s["총괄"], "명령 유형 3종별 세부 결과": s["유형별"], "오류 원인 유형별 건수": s["오류원인별"],
              "오류 사례": s["오류사례"] or none, "사례별 판정": s["사례별"]}
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


def progress_row(gr, pr=None):
    """실행 중 화면 표(progress.xlsx) 한 줄 — 사례가 끝날 때마다 규격서 2-6 출력 항목(해석 의도·핵심 파라미터·실행 대상 기능·
    지도 상태)과 판정(①②③ · 정상 수행/오류 · 오류 원인)을 바로 보여 줌. gr = 실행 결과 로그 행, pr = 명령 해석 로그 행"""
    ox = lambda x: "O" if x is True else "X" if x is False else "-"
    row = {"사례ID": gr.get("사례ID"), "회차": 1, "명령유형": gr.get("명령유형"), "질의": gr.get("명령문")}
    row.update(E.spec_cells((pr or {}).get("규격출력")))
    row.update({"①": ox(gr.get("①의도")), "②": ox(gr.get("②파라미터")), "③": ox(gr.get("③지도제어")),
                "판정": gr.get("판정"), "오류원인": gr.get("오류원인") or "",
                "사유": E.explain_logged(gr, pr)[1],                       # 오류면 실제로 일어난 일(평문), 정상 수행이면 빈칸
                "불일치": " | ".join(gr.get("불일치내용") or []), "소요초": gr.get("소요초")})
    return row


def rescore(run_dir, answer, testset, intent_map):
    """원문(raw/*.json)을 다시 판정해 새 폴더 '<실행 폴더>_재판정_<시각>'에 씀 — 정답·대응표를 고친 뒤 확인용(예비).
    공식 실행 폴더는 건드리지 않음(실행 뒤에 대응표를 바꿔 점수가 바뀌는 일을 막음). 돌려주는 값: (새 폴더, 채점 결과)"""
    src = os.path.normpath(run_dir)
    snap = os.path.join(src, "data")
    pick = lambda p, name: p or next(iter(sorted(glob.glob(os.path.join(snap, name)))), None)
    answer, testset, intent_map = pick(answer, "answer_ai6.*"), pick(testset, "testset_ai6.*"), pick(intent_map, "intent_map_ai6.*")
    rules = E.load_rules(intent_map) if intent_map else E.load_rules()
    gold = {str(r.get("사례ID")): r for r in E.read_rows(answer)}
    cases = {str(r.get("사례ID")): r for r in E.read_rows(testset)}
    root = re.sub(r"(_재판정_\d{6}(_\d+)?)+$", "", src)            # 다시 판정한 폴더를 또 판정해도 이름이 겹겹이 붙지 않게
    new = f"{root}_재판정_{time.strftime('%H%M%S')}"
    k = 2
    while os.path.exists(new):                                   # 같은 초에 두 번 누른 경우
        new, k = f"{root}_재판정_{time.strftime('%H%M%S')}_{k}", k + 1
    shutil.copytree(os.path.join(src, "raw"), os.path.join(new, "raw"))
    os.makedirs(os.path.join(new, "data"), exist_ok=True)
    for p in (testset, answer, intent_map):                      # 이 판정에 쓴 파일
        if p and os.path.exists(p):
            shutil.copy2(p, os.path.join(new, "data", os.path.basename(p)))
    run_dir = new
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
    om = os.path.join(src, "meta.json")
    meta = {}
    if os.path.exists(om):
        with open(om, encoding="utf-8") as f:
            meta = json.load(f)
    first = meta.get("원본") or src                               # 처음 실행한 폴더(다시 판정을 거듭해도 그대로)
    why = [w for w in meta.get("예비사유") or [] if not str(w).startswith("다시 판정(")
           and w not in PENDING_NOTES and not str(w).startswith(CRASH_NOTE)]   # 다시 판정 폴더는 이어서 돌릴 수 없음 — 보류 문구는 빼고
    if (meta.pop("완료전", None) or meta.pop("중지", None)) and INCOMPLETE_NOTE not in why:
        why.append(INCOMPLETE_NOTE)
    meta.pop("중지", None)
    meta.update(원본=first, 공식=False, 예비사유=why + [f"다시 판정(원본 {os.path.basename(first)})"],
                rescored=dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), answer=answer, answer_sha256=sha256(answer),
                              intent_map=intent_map or "기본 대응표", intent_map_sha256=rules_sha256(intent_map)),
                criteria_sha256=E.criteria_fingerprint(), started=meta.get("started"))
    with open(os.path.join(new, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, default=str)
    return new, score_run(new, os.path.join(new, "data", os.path.basename(testset)) if testset else testset)


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
    ap.add_argument("--freeze", action="store_true",
                    help="운영자용: spec_data 의 시험셋·정답셋·의도 대응표와 지금 판정 기준·대기 상한·공간오케 주소로 동결 기록을 새로 씀 "
                         "(시험관 화면에는 동결 버튼이 없음 · 풀려면 spec_data/freeze_ai6.json 을 지움)")
    a = ap.parse_args()
    if a.freeze:                                           # 운영자 동결 — 화면의 옛 '🔒 동결' 버튼과 같은 기록
        ts_p = a.testset or os.path.join("spec_data", "testset_ai6.csv")
        d = os.path.dirname(ts_p) or "."                    # 정답셋·대응표 기본값은 시험셋과 같은 폴더
        an_p = a.answer or os.path.join(d, "answer_ai6.csv")
        im_p = a.intent_map or (os.path.join(d, "intent_map_ai6.csv") if os.path.exists(os.path.join(d, "intent_map_ai6.csv")) else None)
        if not (os.path.exists(ts_p) and os.path.exists(an_p)):
            raise SystemExit(f"시험셋·정답셋 파일이 없어요: {ts_p}, {an_p}")
        rules = E.load_rules(im_p) if im_p else E.load_rules()     # 대응표 형식이 틀리면 여기서 멈춤
        probs = E.blocking(E.validate(E.read_rows(ts_p), E.read_rows(an_p), rules))
        if probs:
            raise SystemExit(f"시험 데이터 점검 문제 {len(probs)}건이 있어 동결하지 않아요: " + " / ".join(probs[:3]))
        rec = freeze_record(ts_p, an_p, im_p, float(a.deadline or C.DEADLINE), a.base)
        path = os.path.join(os.path.dirname(os.path.abspath(ts_p)), FREEZE_FILE)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({**rec, "frozen_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "files": {"testset": ts_p, "answer": an_p, "intent_map": im_p or "기본 대응표"}}, f, ensure_ascii=False, indent=1)
        print(f"[AI6] 동결 기록 저장: {path}", flush=True)
        for k, v in rec.items():
            print(f"  {k}: {v}", flush=True)
        return
    if a.rescore:
        new, _ = rescore(a.rescore, a.answer, a.testset, a.intent_map)
        print(f"[AI6] 다시 판정 결과 폴더: {new} (예비 — 원래 실행 폴더는 그대로)", flush=True)
        return

    live = (a.testset, a.answer, a.intent_map)             # 동결 확인은 지금 시험 데이터 폴더의 파일로
    if a.resume and a.run_dir:                             # 이어서 실행은 처음 실행 때 사본(data/)으로 보내고 판정 — 그 사이 바뀐 파일이 섞이지 않게
        cp = lambda p: os.path.join(a.run_dir, "data", os.path.basename(p))
        a.testset, a.answer, a.intent_map = [cp(p) if p and os.path.exists(cp(p)) else p for p in live]
    base = (a.base or C.BASE).rstrip("/")
    deadline = float(a.deadline or C.DEADLINE)
    ts = E.read_rows(a.testset)
    if not ts:                                             # 상태 파일에도 오류로 남김(화면이 '시작 중'에 머물지 않게)
        refuse(a.status_file, f"시험셋을 읽지 못했습니다: {a.testset}")
    if "사례ID" not in ts[0] or "명령문" not in ts[0]:
        refuse(a.status_file, f"시험셋에 '사례ID', '명령문' 컬럼이 필요합니다. 현재: {list(ts[0])}")
    answers = {str(r.get("사례ID")): r for r in E.read_rows(a.answer)} if a.answer else {}
    rules = E.load_rules(a.intent_map) if a.intent_map else E.load_rules()
    ts = [r for r in ts if str(r.get("사례ID", "")).strip()]
    if a.cases:
        want = {c.strip() for c in a.cases.split(",") if c.strip()}
        ts = [r for r in ts if r["사례ID"] in want]

    outside = any(not re.match(r"^AI6-\d{4}$", str(r.get("사례ID"))) for r in ts)   # 예비 실측(P6-…) 등 → 공식 결과 아님
    pilot = bool(a.cases or a.retry_errors or a.no_precheck or outside)
    data_check = E.validate(ts if not a.cases else E.read_rows(a.testset), list(answers.values()), rules)
    if not pilot and E.blocking(data_check):                    # 이어서 실행도 다시 점검 — 동결을 풀고 정답을 고친 뒤 이어 돌리는 경우
        refuse(a.status_file, f"시험 데이터 점검 문제 {len(E.blocking(data_check))}건이 있어 공식 실행을 시작하지 않아요: "
               + " / ".join(E.blocking(data_check)[:3]) + (" …" if len(E.blocking(data_check)) > 3 else ""))
    freeze_path = os.path.join(os.path.dirname(os.path.abspath(live[0])), FREEZE_FILE)
    frozen = freeze_check(freeze_path, freeze_record(*live, deadline, base))
    if frozen:                                             # 규격: 의도 대응표는 시험 전에 확정하고 시험 기간 중 바꾸지 않음
        refuse(a.status_file, "동결 이후 바뀐 항목이 있어 실행하지 않아요: " + ", ".join(frozen)
               + " — 의도한 변경이면 운영자가 다시 동결하세요(python spec_runner_ai6.py --freeze).")

    run_dir = a.run_dir or os.path.join("spec", "AI6", time.strftime("%Y%m%d_%H%M%S"))
    raw_dir = os.path.join(run_dir, "raw")
    new_dir = not os.path.exists(run_dir)                  # 이번에 만든 폴더 — 사전 점검에서 막히면 지움
    os.makedirs(raw_dir, exist_ok=True)
    snap = os.path.join(run_dir, "data")
    os.makedirs(snap, exist_ok=True)
    for p in (a.testset, a.answer, a.intent_map):
        if p and os.path.exists(p) and not os.path.exists(os.path.join(snap, os.path.basename(p))):
            shutil.copy2(p, snap)
    parse_path, gis_path = os.path.join(run_dir, E.PARSE_LOG), os.path.join(run_dir, E.GIS_LOG)
    att_path = os.path.join(run_dir, "attempts.jsonl")      # 보내기 직전에 한 줄 — 이어서 실행 때 다시 보내지 않는 근거

    done, lost = set(), 0
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
                       error=E.LOST_NOTE)
            try:                                           # 응답 원문은 저장했는데 기록 전에 멈춘 경우 → 저장된 원문으로 판정
                with open(os.path.join(raw_dir, f"{r['사례ID']}.json"), encoding="utf-8") as f:
                    saved = dict(json.load(f)["response"])
                saved["events"] = [(e.get("event"), e.get("data")) for e in saved.get("events") or []]
                pr, gr, _ = judge_one(r, answers.get(r["사례ID"]), saved, rules)
            except Exception:                              # noqa: BLE001 — 원문이 없거나 깨졌거나 판정이 안 되면 응답 오류로 남김
                pr, gr, _ = judge_one(r, answers.get(r["사례ID"]), res, rules)
                lost += 1
            append_jsonl(parse_path, pr)
            append_jsonl(gis_path, gr)
            done.add(r["사례ID"])
    plan = [r for r in ts if r["사례ID"] not in done]

    argv = sys.argv[1:]
    if "--run-dir" not in argv:
        argv += ["--run-dir", run_dir]
    total = len(plan) + len(done)
    status = dict(state="running", pid=os.getpid(), started=time.time(), script=SCRIPT, mode="성과지표 AI6",
                  run_dir=run_dir, out=os.path.join(run_dir, "progress.xlsx"), total=total, done=len(done), current=None,
                  message="사전 점검 중…", argv=argv)
    write_status(a.status_file, status)                    # 사전 점검(주소 검색 포함 최대 2분)보다 먼저 — 20초 넘게 갱신이 없으면 화면이 비정상 종료로 봄
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
            meta = {**json.load(f)}
        meta.setdefault("resumed", []).append(time.strftime("%Y-%m-%d %H:%M:%S"))
        if meta.pop("중지", None) or meta.get("공식"):            # 예전 기록(중지 표시·시작 때 공식) → 끝낼 때 공식 여부를 다시 정함
            meta.update(공식=False, 완료전=True)
        cur = freeze_record(a.testset, a.answer, a.intent_map, deadline, base)   # 처음 실행 때와 같은 조건인가 — 그사이 동결을 다시 걸었어도
        changed = [name for key, mkey, name in (("testset_sha256", "testset_sha256", "시험셋"), ("answer_sha256", "answer_sha256", "정답셋"),
                                                ("intent_map_sha256", "intent_rules_sha256", "의도 대응표"),
                                                ("criteria_sha256", "criteria_sha256", "판정 기준(규칙·옵션)"),
                                                ("deadline_s", "deadline_s", "응답 대기 상한"), ("base", "base", "공간오케 주소"))
                   if meta.get(mkey) is not None and (float(meta[mkey]) != float(cur[key]) if key == "deadline_s"
                                                      else str(meta[mkey]) != str(cur.get(key)))]
        blockers = []                                      # 이어서 돌려도 공식이 될 수 없는 사유 — 시작할 때 바로 예비로
        if changed:
            blockers.append("이어서 실행 때 처음 실행과 조건이 다름 (" + ", ".join(changed) + ")")
        if a.retry_errors:
            blockers.append("응답 오류 재전송")
        if lost:                                           # QA 쪽 중단 — 시스템 무응답이 아니므로 공식 결과로 쓰지 않고 전체 재수행 권장
            blockers.append(f"QA 중단으로 응답을 기록 못 한 사례 {lost}건(응답 오류로 남김) — 전체 재수행 권장")
        if meta.get("완료전"):
            fz0 = freeze_check(freeze_path, freeze_record(a.testset, a.answer, a.intent_map, deadline, base))   # 보낸 데이터(사본) 대 지금 동결 기록
            if fz0 is None or fz0:
                blockers.append("이어서 실행 때 동결 기록과 다름" + (f" ({', '.join(fz0)})" if fz0 else " (동결 풀림)"))
            if pilot and not a.retry_errors:
                blockers.append("이어서 실행을 예비 조건으로 함(일부 ID·사전 점검 생략 등)")
        if blockers:
            meta.pop("완료전", None)
            meta.update(공식=False, 예비사유=[w for w in meta.get("예비사유") or [] if w not in PENDING_NOTES
                                             and not str(w).startswith(CRASH_NOTE)] + blockers)
        if not a.no_precheck:                              # 이어서 실행도 환경을 다시 점검 (그때의 /ready·LLM 값도 함께 — 결과 화면 동결 버전 표)
            fresh = C.precheck(base, probe_message=a.probe)
            meta.setdefault("resume_precheck", []).append(dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), rows=fresh,
                                                               ready=C.ready_info(base), llm=C.llm_info(base)))
    else:
        meta = dict(item="AI6", 시험항목="[AI 6] 자연어 기반 지도 제어 기능", base=base, endpoint=f"{base}/api/chat",
                    deadline_s=deadline, origin=C.ORIGIN, cases=[r["사례ID"] for r in ts] if a.cases else [],
                    testset=a.testset, testset_sha256=sha256(a.testset), answer=a.answer, answer_sha256=sha256(a.answer),
                    intent_map=a.intent_map, intent_map_sha256=sha256(a.intent_map),
                    intent_rules=[{k: (sorted(v) if isinstance(v, set) else v) for k, v in r.items()} for r in rules],
                    data_check=data_check,
                    intent_rules_sha256=rules_sha256(a.intent_map), criteria_sha256=E.criteria_fingerprint(),
                    frozen=os.path.exists(freeze_path), 공식=False,   # 공식 여부는 정상으로 끝날 때 정함(중간에 죽은 실행이 공식으로 남지 않게)
                    예비사유=[x for x, on in (("일부 ID만 실행", a.cases), ("응답 오류 재전송", a.retry_errors),
                                              ("사전 점검 생략", a.no_precheck), ("시험셋 밖 ID(예비 실측)", outside),
                                              ("동결 전 실행", not os.path.exists(freeze_path))) if on],
                    gap_s=GAP, started=time.strftime("%Y-%m-%d %H:%M:%S"))
        if meta["frozen"] and not pilot:                   # 공식 조건 실행 — 끝까지 마치면 공식
            meta.update(완료전=True, 예비사유=meta["예비사유"] + [RUN_NOTE])
        if not a.no_precheck:
            fresh = meta["precheck"] = C.precheck(base, probe_message=a.probe)
            meta["ready"] = C.ready_info(base)
            meta["llm"] = C.llm_info(base)                 # LLM 서버가 띄운 모델(키가 있을 때) — 키는 기록 안 함
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, default=str)

    status["message"] = "이어서 실행" if a.resume else ""
    bad = [r for r in fresh or [] if r.get("결과") != "정상"]   # 이번 실행에서 한 점검만
    if bad:                               # 사전 점검 실패 → 실행하지 않음(규격 시험환경 미충족)
        alive.set()
        status.update(state="error", stop_kind="precheck", message="사전 점검 실패: " + ", ".join(f"{r['항목']}({r['내용'][:40]})" for r in bad),
                      finished=time.time())
        if new_dir:                                        # 시작도 못 한 폴더는 남기지 않음(최신 결과 '미실행'으로 잡히지 않게) · '↻ 이어서'도 안 냄
            shutil.rmtree(run_dir, ignore_errors=True)
            status.pop("argv", None)
        write_status(a.status_file, status)
        raise SystemExit(status["message"])
    write_status(a.status_file, status)

    parsed = {str(p.get("사례ID")): p for p in read_jsonl(parse_path)}     # 이어서 실행: 이미 끝난 사례도 같은 칸으로
    prog = [progress_row(g, parsed.get(str(g.get("사례ID")))) for g in read_jsonl(gis_path)]
    print(f"[AI6] 명령 {len(plan)}건 · 공간오케 {base} · 대기 상한 {deadline:g}초 · 저장 위치 {run_dir}", flush=True)
    fails = errs = 0
    try:
        for r in plan:
            if os.path.exists(STOP_FILE):
                os.remove(STOP_FILE)
                status.update(state="stopped", message=f"사용자가 중지 ({status['done']}/{total}건 완료)")
                if meta.get("완료전"):                         # 다 돌리기 전에는 공식 점수로 쓰지 않음 — '↻ 이어서'로 끝까지 돌리면 공식
                    meta.update(공식=False, 예비사유=[w for w in meta.get("예비사유") or [] if w not in PENDING_NOTES] + [STOP_NOTE])
                    save_meta(mp, meta)
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
            if errs >= MAX_RESP_ERRORS and fails < MAX_CONN_FAILS:   # 시스템 장애 — 남은 사례도 같은 오류가 날 것이라 멈춤(이어서 실행 없음)
                status.update(state="error", stop_kind="system", message=f"응답 오류 {errs}건 연속 ({status['done']}/{total}건) — 시스템 장애로 보고 멈췄어요. "
                                                     f"원인을 고친 뒤 전체를 다시 실행하세요. 마지막 오류: {str(gr.get('응답오류') or '')[:120]}")
                status.pop("argv", None)
                meta.pop("완료전", None)
                meta.update(공식=False, 예비사유=[w for w in meta.get("예비사유") or [] if w not in PENDING_NOTES]
                            + [f"응답 오류 {errs}건 연속으로 멈춤(시스템 장애) — 전체 재수행 대상"])
                save_meta(mp, meta)
                break
            if fails >= MAX_CONN_FAILS:
                status.update(state="error", stop_kind="environment", message=f"연결 실패 {fails}회 연속 ({status['done']}/{total}건) — 시험 환경 문제로 보고 멈췄어요. "
                                                     "원인을 고친 뒤 전체를 다시 실행하세요(규격: 환경이 바뀌면 전체 재수행).")
                status.pop("argv", None)                   # 화면에 '↻ 이어서'를 내지 않음 — 이 폴더를 이어 돌려도 공식이 아님
                meta.pop("완료전", None)
                meta.update(공식=False, 예비사유=[w for w in meta.get("예비사유") or [] if w not in PENDING_NOTES]
                            + [f"연결 실패 {fails}회 연속으로 멈춤 — 전체 재수행 대상"])
                with open(mp, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=1, default=str)
                break
            pause(COOLDOWN if res.get("ended_by") == "deadline" else GAP)
        if status["state"] == "running":
            if meta.pop("완료전", None):                    # 끝까지 마침(처음부터 · 중지·비정상 종료 뒤 이어서) — 보낸 데이터(사본)가 지금도 동결 그대로이고 다른 예비 사유가 없으면 공식
                why = [w for w in meta.get("예비사유") or [] if w not in PENDING_NOTES and not str(w).startswith(CRASH_NOTE)]
                fz = freeze_check(freeze_path, freeze_record(a.testset, a.answer, a.intent_map, deadline, base))
                if fz is None or fz:
                    why.append("실행을 마칠 때 동결 기록과 다름" + (f" ({', '.join(fz)})" if fz else " (동결 풀림)"))
                if pilot and not why:
                    why.append("이어서 실행을 예비 조건으로 함(일부 ID·사전 점검 생략 등)")
                meta.update(예비사유=why, 공식=bool(meta.get("frozen")) and not why)
                save_meta(mp, meta)
            snap_ts = os.path.join(snap, os.path.basename(a.testset)) if os.path.exists(os.path.join(snap, os.path.basename(a.testset))) else a.testset
            s = score_run(run_dir, snap_ts)
            msg = f"완료 ({status['done']}/{total}건)"
            if s["value"] is not None:
                msg += f" · 자연어 기반 지도 제어 정확도 {s['value']:.2f} % {'달성' if s['passed'] else '미달성'}"
            status.update(state="done", message=msg)
        print(status.get("message"), flush=True)
    except Exception as e:                                  # noqa: BLE001
        status.update(state="error", message=f"{type(e).__name__}: {e}")
        if meta.get("완료전"):                              # 공식 조건 실행이 중간에 죽음 → 예비로 남김(↻ 이어서로 마저 돌리면 공식)
            meta["예비사유"] = [w for w in meta.get("예비사유") or [] if w not in PENDING_NOTES and not str(w).startswith(CRASH_NOTE)] \
                + [f"{CRASH_NOTE}({type(e).__name__})"]
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
