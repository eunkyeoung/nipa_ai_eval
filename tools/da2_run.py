# [DA 2] 온톨로지(ODP) 설계 완성도 — 구성지표 2-1 ~ 2-4 실행기 (QA 화면의 'DA2 시험 실행' 버튼이 부름)
#   python tools/da2_run.py [--parts 1,2,3,4] [--run-dir spec/DA2/<시각>] [--status-file run_status.json] [--ids CQ-001,...]
# 결과: <run_dir>/da2-<번호>/validation_result_da2.csv, <run_dir>/summary.json, meta.json(검증 자산 SHA-256·도구 버전)
# 종합 = 2-1 × 0.35 + 2-2 × 0.15 + 2-3 × 0.30 + 2-4 × 0.20 — 네 구성지표를 모두 전체 실행(부분 선택 없음)했을 때만 계산
import argparse, csv, hashlib, json, os, shutil, sys, time, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qa_runner                                                    # noqa: E402 (상태 파일 형식을 다른 시험과 같게)

DA2 = "spec_data/da2"
ASSETS = {"cq": f"{DA2}/da2_cq_testset.zip", "schema_check": f"{DA2}/da2_schema_checklist.zip",
          "mapping": f"{DA2}/da2_mapping_testset.csv", "schema": f"{DA2}/landcheck_schema_v1.0.ttl",
          "instance": f"{DA2}/landcheck_instance_v1.0.ttl", "shapes": f"{DA2}/landcheck_shapes_v1.0.ttl"}
NEED = {1: ("cq", "schema", "instance"), 2: ("schema_check", "schema", "instance", "shapes"),
        3: ("mapping", "schema", "instance"), 4: ("schema", "instance")}
PARTS = {1: ("DA 2-1", "CQ 질의 응답 정확도", 0.35), 2: ("DA 2-2", "온톨로지 스키마 적합률", 0.15),
         3: ("DA 2-3", "온톨로지 기반 데이터 매핑 정확도", 0.30), 4: ("DA 2-4", "논리적 일관성·무결성", 0.20)}
TARGET = 96.0


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def trunc2(v):
    return None if v is None else int(v * 100 + 1e-9) / 100


def count(path, col):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    ok = sum(r.get(col) == "true" for r in rows)
    err = sum(r.get("status") == "error" for r in rows)
    return len(rows), ok, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default="1,2,3,4")
    ap.add_argument("--run-dir")
    ap.add_argument("--status-file")
    ap.add_argument("--ids", default="", help="DA2-1 CQ ID 일부만 실행(부분 실행 — 종합 점수 계산 안 함)")
    a = ap.parse_args()
    parts = sorted({int(x) for x in a.parts.split(",") if x.strip()})
    ids = {x.strip() for x in a.ids.split(",") if x.strip()}
    run_dir = a.run_dir or os.path.join("spec", "DA2", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.join(run_dir, "data"), exist_ok=True)
    status = dict(state="running", pid=os.getpid(), started=time.time(), script="tools/da2_run.py", mode="성과지표 DA2",
                  run_dir=run_dir, out=run_dir, total=len(parts), done=0, current=None, message="", argv=sys.argv[1:])
    qa_runner.write_status(a.status_file, status)
    missing = [ASSETS[k] for p in parts for k in NEED[p] if not os.path.exists(ASSETS[k])]
    if missing:
        status.update(state="error", message="검증 자산 없음: " + ", ".join(sorted(set(missing))))
        qa_runner.write_status(a.status_file, status)
        print(status["message"])
        return
    meta = dict(item="DA2", parts=parts, ids=sorted(ids), scope="partial" if (ids or parts != [1, 2, 3, 4]) else "full",
                started=time.strftime("%Y-%m-%d %H:%M:%S"), assets={}, tools={})
    for k, p in ASSETS.items():                                        # 검증 자산 해시 기록, 작은 파일은 사본 보관
        if os.path.exists(p):
            meta["assets"][k] = dict(path=p, sha256=sha256(p), bytes=os.path.getsize(p))
            if os.path.getsize(p) < 20 << 20:
                shutil.copy2(p, os.path.join(run_dir, "data"))
    for m in ("pyoxigraph", "pyshacl", "owlready2", "rdflib"):
        try:
            meta["tools"][m] = __import__(m).__version__ if hasattr(__import__(m), "__version__") else "ok"
        except Exception:                                              # noqa: BLE001
            meta["tools"][m] = "없음"
    meta["tools"]["reasoner"] = "Pellet 2.3.1 (owlready2 내장)"
    meta["tools"]["oops"] = "https://oops.linkeddata.es/rest"
    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    from tools import da2_cq, da2_schema_check, da2_mapping, da2_consistency
    res = {}
    for i, p in enumerate(parts):
        no, name, w = PARTS[p]
        out = os.path.join(run_dir, f"da2-{p}")
        status.update(current=dict(case_id=no, 회차=1, 질의=name), message=f"{no} {name} 실행 중")
        qa_runner.write_status(a.status_file, status)
        t = time.time()
        try:
            if p == 1:
                da2_cq.run(out, only=ids or None)
                n, ok, err = count(os.path.join(out, "validation_result_da2.csv"), "match")
                val = trunc2(ok / n * 100) if n else None
                res[p] = dict(value=val, n=n, ok=ok, error=err)
            elif p == 2:
                da2_schema_check.run(out)
                n, ok, err = count(os.path.join(out, "validation_result_da2.csv"), "conforms")
                res[p] = dict(value=trunc2(ok / n * 100) if n else None, n=n, ok=ok, error=err)
            elif p == 3:
                da2_mapping.run(out)
                n, ok, err = count(os.path.join(out, "validation_result_da2.csv"), "exact_match")
                res[p] = dict(value=trunc2(ok / n * 100) if n else None, n=n, ok=ok, error=err)
            else:
                sm = da2_consistency.run(out)
                res[p] = dict(value=trunc2(sm["value"]) if sm.get("value") is not None else None, n=None, detail=sm,
                              error=0 if sm.get("value") is not None else 1)
        except Exception as e:                                         # noqa: BLE001
            traceback.print_exc()
            res[p] = dict(value=None, n=None, error=1, message=f"{type(e).__name__}: {e}")
        res[p]["elapsed_s"] = round(time.time() - t, 1)
        status["done"] = i + 1
        qa_runner.write_status(a.status_file, status)
    full = meta["scope"] == "full" and all(res.get(p, {}).get("value") is not None for p in (1, 2, 3, 4))
    total = trunc2(sum(res[p]["value"] * PARTS[p][2] for p in (1, 2, 3, 4))) if full else None
    summ = dict(item="DA2", value=total, target=TARGET, cmp=">=", passed=(total is not None and total >= TARGET),
                n=res.get(1, {}).get("n"), scope=meta["scope"], scored=time.time(),
                parts={PARTS[p][0]: dict(name=PARTS[p][1], weight=PARTS[p][2], **res[p]) for p in res})
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summ, f, ensure_ascii=False, indent=1, default=str)
    msg = " · ".join(f"{PARTS[p][0]} {res[p]['value'] if res[p]['value'] is not None else '오류'}" for p in res)
    status.update(state="done", current=None,
                  message=f"완료 · {msg}" + (f" · 종합 {total:.2f} % {'달성' if summ['passed'] else '미달성'}" if total is not None else " · 부분 실행(종합 점수 없음)"))
    qa_runner.write_status(a.status_file, status)
    print(status["message"])


if __name__ == "__main__":
    main()
