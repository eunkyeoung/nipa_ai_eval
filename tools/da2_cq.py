# [DA 2-1] CQ 질의 응답 정확도 — CQ 목록·SPARQL·정답 동결(freeze), 실행(run)
# 규격서 6.1.1·제작지시서 0.2v: CQ 1건 = 질의 1건 = 정답 1건 = 판정 1건. 결과 집합이 정답 집합과 완전히 같을 때만 정답
#   (순서 무시·중복 제거, 일부 일치는 오답). 질의에 정답값을 넣거나 LIMIT으로 결과 수를 맞추지 않음.
# 정규화: 결과 노드의 rdfs:label → '법령명.조.항' (항이 없으면 '법령명.조', 별표는 '법령명.별표N', 호·목은 비교에서 뺌)
# 대상: 동결한 ttl(스키마+인스턴스)을 메모리에 적재해 실행
#
#   python tools/da2_cq.py freeze [모듈]        → spec_data/da2/aips_da2_cq_testset.zip (da2_cq_testset.csv + sparql/*.rq). 모듈 기본 da2_cq50_jo(1안 조 단위)
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


# ── 보이는 표기(화면·엑셀): 내부 비교 키 '건축법.44.1' → '건축법 제44조 제1항' (11-12 사용자: 점·가운데 점 대신 '제○조 제○항') ──
_ORD = re.compile(r"^(.+?(?:광역시|시|군))(건축|도시계획|주차장)조례$")
_KEY = re.compile(r"([가-힣]+)\.(별표\d+(?:의\d+)?|\d+(?:의\d+)?)(?:\.(\d+))?")


def law_disp(name):
    """'건축법시행령' → '건축법 시행령', '음성군주차장조례' → '음성군 주차장 조례'"""
    if name == "인천광역시계양구개발제한구역내건축행위등허가에관한조례":
        return "인천광역시 계양구 개발제한구역 내 건축행위 등 허가에 관한 조례"
    m = _ORD.match(name)
    if m:
        return f"{m.group(1)} {m.group(2)} 조례"
    return re.sub(r"(시행령|시행규칙)$", r" \1", name)


def cite(k):
    """비교 키 1개 → 법령 표기. 별표는 '○○ 별표 2'"""
    m = _KEY.fullmatch(str(k).strip())
    if not m:
        return str(k)
    law, jo, hang = law_disp(m.group(1)), m.group(2), m.group(3)
    if jo.startswith("별표"):
        return f"{law} 별표 {jo[2:]}"
    a, _, b = jo.partition("의")
    return f"{law} 제{a}조" + (f"의{b}" if b else "") + (f" 제{hang}항" if hang else "")


def cite_list(v):
    """JSON 목록 문자열(또는 목록) → '건축법 제44조, 건축법 시행령 제28조'. 빈 목록은 '-'"""
    try:
        xs = json.loads(v) if isinstance(v, str) else list(v or [])
    except Exception:                                                   # noqa: BLE001
        return str(v)
    return ", ".join(cite(x) for x in xs) if xs else "-"


def cite_text(s):
    """글 속의 비교 키·붙여 쓴 조항·가운데 점을 법령 표기로 ('제44조제1항·제2항' → '제44조 제1항, 제2항')"""
    s = _KEY.sub(lambda m: cite(m.group(0)), str(s or ""))
    s = re.sub(r"\[별표 ?(\d+(?:의\d+)?)\]", r"별표 \1", s)
    s = re.sub(r"(조(?:의\d+)?|항|호)(제\d)", r"\1 \2", s)
    return re.sub(r"(?<=[조항호목\d])·(?=\s*(?:제\d|별표|법 |영 |같은 법|시행령|시행규칙))", ", ", s)


def reason(exp, got, keys):
    """불일치 사유: 정답·결과 키와 온톨로지에 있는 조문 키로 판정"""
    jo = lambda k: ".".join(k.split(".")[:2])                         # '법령.조'
    out = []
    for k in sorted(set(exp) - set(got)):
        if k not in keys:
            out.append(f"정답 조문 {k}이(가) 온톨로지에 없음 — 질의로 낼 수 없음")
        elif not got:
            out.append(f"{k}: 질의 결과가 없음 — 질문의 출발점(법령·조문)이 온톨로지에 없거나 연결이 없음")
        elif jo(k) in got:
            out.append(f"{k}: 결과가 항이 아니라 조({jo(k)})로 나옴 — 위임 연결이 조 단위라 항을 구분할 수 없음")
        else:
            out.append(f"{k}: 정답 조문까지 이어지는 연결이 없음 — 질문 개념에서 이 조문으로 가는 관계가 온톨로지에 없음")
    gold_jo = {jo(k) for k in exp}
    extra = sorted(set(got) - set(exp))
    same = [k for k in extra if jo(k) in gold_jo and k.count(".") == 2]
    if same:
        out.append(f"같은 조의 다른 항이 함께 나옴({', '.join(same)}) — 온톨로지 연결이 조 단위라 질문 개념만으로는 정답 항만 고를 수 없음")
    other = [k for k in extra if k not in same and not any(k == jo(x) for x in exp)]   # 조 단위로 나온 것은 위에서 설명
    if other:
        out.append(f"정답에 없는 다른 조문이 함께 나옴({', '.join(other[:8])}{' 외' if len(other) > 8 else ''}) — 질문 개념으로 걸리는 조문이 정답보다 넓음")
    return " / ".join(out)


def freeze(mod="da2_cq50_jo"):
    """지금 작성된 CQ를 zip 으로 동결. 기본 = 1안 조 단위(tools/da2_cq50_jo.py, 11-12). 항 단위 원본은 mod='da2_cq50'"""
    sys.path.insert(0, ".")
    import importlib
    R = importlib.import_module(f"tools.{mod}")
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
    keys = set()                                                       # 온톨로지에 있는 조문 키(사유 판정용)
    for b in st.query("PREFIX aip:<https://onto.go.kr/aip/> PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#> SELECT ?lb WHERE { "
                      "{ ?n a aip:Article } UNION { ?n a aip:Appendix } UNION { ?n a aip:ArticleUnit . ?n aip:unitType \"항\" } ?n rdfs:label ?lb }"):
        keys.add(norm(b["lb"].value))
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
            if set(got) != set(exp):
                rec["reason"] = reason(exp, got, keys)
        except Exception as e:                                             # noqa: BLE001
            rec.update(status="error", error_message=f"{type(e).__name__}: {str(e)[:300]}")
        rec["elapsed_ms"] = int((time.time() - t) * 1000)
        res.append(rec)
        print(r["cq_id"], rec["match"] or rec["status"], flush=True)
        if progress:
            progress(i + 1, len(rows), r["cq_id"])
    os.makedirs(out_dir, exist_ok=True)
    cols = COLS + ["actual_bindings", "missing_bindings", "extra_bindings", "match", "reason", "elapsed_ms", "status", "error_message"]
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
        freeze(*sys.argv[2:3])
    elif sys.argv[1] == "run":
        run(sys.argv[2], only=set(sys.argv[3:]) or None)
