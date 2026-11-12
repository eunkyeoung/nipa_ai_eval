# [DA 2-1] CQ 질의 응답 정확도 — CQ 목록·SPARQL·정답 동결(freeze), 실행(run)
# 규격서 6.1.1·제작지시서 0.2v: CQ 1건 = 질의 1건 = 정답 1건 = 판정 1건. 결과 집합이 정답 집합과 완전히 같을 때만 정답
#   (순서 무시·중복 제거, 일부 일치는 오답). 질의에 정답값을 넣거나 LIMIT으로 결과 수를 맞추지 않음.
# 정규화: 결과 노드의 rdfs:label → '법령명.조.항' (항이 없으면 '법령명.조', 별표는 '법령명.별표N', 호·목은 비교에서 뺌)
# 대상: 동결한 ttl(스키마+인스턴스)을 메모리에 적재해 실행
#
#   python tools/da2_cq.py freeze              → spec_data/da2/aips_da2_cq_testset.zip (da2_cq_testset.csv + sparql/*.rq)
#   python tools/da2_cq.py run <out_dir>       → <out_dir>/validation_result_da2.csv
import csv, io, json, os, re, sys, time, zipfile

DA2 = "spec_data/da2"
TTL = {k: f"{DA2}/landcheck_{k}_v1.0.ttl" for k in ("schema", "instance")}
ZIP = f"{DA2}/aips_da2_cq_testset.zip"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
ALIAS = [("국토의계획및이용에관한법률", "국토계획법")]
RX = re.compile(r"^(\S+)\s+(?:제(\d+)조(?:의(\d+))?(?:제(\d+)항)?|별표(\d+(?:의\d+)?))")
COLS = ["cq_id", "odp_type", "municipality", "question", "inference_required", "query_file", "expected_bindings"]


def norm(label):
    m = RX.match(str(label).strip())
    if not m:
        return str(label)
    law = m.group(1)
    for a, b in ALIAS:
        law = law.replace(a, b)
    if m.group(5):
        return f"{law}.별표{m.group(5)}"
    jo = m.group(2) + (f"의{m.group(3)}" if m.group(3) else "")
    return f"{law}.{jo}" + (f".{m.group(4)}" if m.group(4) else "")


def freeze():
    """지금 작성된 CQ(tools/da2_cq_recv.py)를 zip 으로 동결"""
    sys.path.insert(0, ".")
    from tools import da2_cq_recv as R
    buf = io.BytesIO()
    rows = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for cid, typ, area, cq, gold, note in R.CQS:
            fn = f"sparql/{cid}.rq"
            z.writestr(fn, f"# {cid} {cq.splitlines()[0]}\n" + R.query(cid) + "\n")
            rows.append([cid, typ, area, cq, "false", fn, json.dumps(sorted(gold), ensure_ascii=False)])
        s = io.StringIO()
        w = csv.writer(s)
        w.writerow(COLS)
        w.writerows(rows)
        z.writestr("da2_cq_testset.csv", "﻿" + s.getvalue())
    with open(ZIP, "wb") as f:
        f.write(buf.getvalue())
    print(ZIP, len(rows), "건")


def load(zp=ZIP):
    with zipfile.ZipFile(zp) as z:
        rows = list(csv.DictReader(io.StringIO(z.read("da2_cq_testset.csv").decode("utf-8-sig"))))
        for r in rows:
            r["_query"] = z.read(r["query_file"]).decode("utf-8")
    return rows


def run(out_dir, zp=ZIP, only=None, store=None, progress=None):
    import pyoxigraph as ox
    rows = load(zp)
    if only:
        rows = [r for r in rows if r["cq_id"] in only]
    st = store
    if st is None:
        st = ox.Store()
        for k in ("schema", "instance"):
            st.bulk_load(path=TTL[k], format=ox.RdfFormat.TURTLE)
    lab = ox.NamedNode(LABEL)
    res = []
    for i, r in enumerate(rows):
        t = time.time()
        exp = sorted(set(json.loads(r["expected_bindings"])))
        rec = {c: r[c] for c in COLS}
        rec.update(actual_bindings="", missing_bindings="", extra_bindings="", match="", status="completed", error_message="")
        try:
            got = set()
            sol = st.query(r["_query"])
            var = sol.variables[0]
            for b in sol:
                n = b[var]
                if n is None:
                    continue
                lb = [q.object.value for q in st.quads_for_pattern(n, lab, None)] if isinstance(n, ox.NamedNode) else []
                got.add(norm(lb[0] if lb else n.value))
            got = sorted(got)
            rec.update(actual_bindings=json.dumps(got, ensure_ascii=False),
                       missing_bindings=json.dumps(sorted(set(exp) - set(got)), ensure_ascii=False),
                       extra_bindings=json.dumps(sorted(set(got) - set(exp)), ensure_ascii=False),
                       match=str(set(got) == set(exp)).lower())
        except Exception as e:                                             # noqa: BLE001
            rec.update(status="error", error_message=f"{type(e).__name__}: {str(e)[:300]}")
        rec["elapsed_ms"] = int((time.time() - t) * 1000)
        res.append(rec)
        print(r["cq_id"], rec["match"] or rec["status"], flush=True)
        if progress:
            progress(i + 1, len(rows), r["cq_id"])
    os.makedirs(out_dir, exist_ok=True)
    cols = COLS + ["actual_bindings", "missing_bindings", "extra_bindings", "match", "elapsed_ms", "status", "error_message"]
    with open(os.path.join(out_dir, "validation_result_da2.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(res)
    n = len(res)
    ok = sum(x["match"] == "true" for x in res)
    print(f"일치 {ok}/{n} = {int(ok / n * 10000) / 100 if n else 0:.2f}%")
    return res


if __name__ == "__main__":
    if sys.argv[1] == "freeze":
        freeze()
    elif sys.argv[1] == "run":
        run(sys.argv[2], only=set(sys.argv[3:]) or None)
