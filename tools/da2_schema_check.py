# [DA 2-2] 온톨로지 스키마 적합률 — 평가항목(ASK 질의·SHACL Shape) 정의·동결·실행
# 규격서 6.1.2: 평가항목 1개 = SPARQL ASK 1개 또는 SHACL Shape 1개, 결과 true = 적합.
#   항목군: 핵심 ODP 패턴 / 핵심 클래스 / 객체 관계·데이터 속성 / SHACL 제약조건
#   적합률(%) = 적합(true) 항목 수 / 전체 항목 수 × 100
# 시험 대상: 동결한 ttl 3파일(spec_data/da2/landcheck_{schema,instance,shapes}_v1.0.ttl)을 메모리에 적재해 실행
#   (Fuseki 원본이 아니라 해시로 고정한 파일이 대상)
# 역할 구분: DA2-2는 '데이터가 스키마 선언·설계 패턴을 지키는가'. 선언 자체의 누락·모순(OOPS! 계열)은 DA2-4에서 봄.
#
#   python tools/da2_schema_check.py freeze          → spec_data/da2/aips_da2_schema_checklist.zip (목록 CSV + checks/*.rq)
#   python tools/da2_schema_check.py run <out_dir>   → <out_dir>/validation_result_da2.csv
import csv, io, json, os, sys, time, zipfile

DA2 = "spec_data/da2"
TTL = {k: f"{DA2}/landcheck_{k}_v1.0.ttl" for k in ("schema", "instance", "shapes")}
ZIP = f"{DA2}/aips_da2_schema_checklist.zip"

PFX = """PREFIX aip: <https://onto.go.kr/aip/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""

G_ODP, G_CLS, G_PRP, G_SH = "핵심 ODP 패턴", "핵심 클래스", "객체 관계·데이터 속성", "SHACL 제약조건"


def not_typed(var, *classes):
    """?var 가 classes 중 어느 것의 인스턴스도 아님"""
    vals = " ".join(f"aip:{c}" for c in classes)
    return f"FILTER NOT EXISTS {{ VALUES ?_t_{var} {{ {vals} }} ?{var} a ?_t_{var} }}"


def node_hier(node, h, law):
    """node(조·별표·항·호·목)와 그 법령의 위계 — 고정 단계(조 → 항 → 호 → 목)를 나열한 하위 질의 (가변 경로는 대용량에서 느림)"""
    steps = ["aip:hasArticle", "aip:hasAppendix", "aip:hasArticle/aip:hasUnit",
             "aip:hasArticle/aip:hasUnit/aip:hasUnit", "aip:hasArticle/aip:hasUnit/aip:hasUnit/aip:hasUnit"]
    u = " UNION ".join(f"{{ ?{law} {p} ?{node} }}" for p in steps)
    return f"{{ SELECT ?{node} ?{h} WHERE {{ ?{law} aip:hierarchy ?{h} . {u} }} }}"


# (ID, 항목군, 점검 내용, 존재 패턴, 위반 패턴)  → ASK { 존재 개수 > 0 그리고 위반 개수 = 0 }
# 위반 패턴의 ?focus 는 위반 노드(부적합일 때 메시지에 표본 표시)
ASK_ITEMS = [
    # ── 핵심 ODP 패턴 ─────────────────────────────────────────────────────
    ("SCH-ODP-01", G_ODP, "[규제 조건] 구역(Zone)이 규제 조문(조·항·별표)에 restrictedBy로 연결되고, 연결 대상이 모두 조문 노드임",
     "?z a aip:Zone ; aip:restrictedBy ?r .",
     "?focus aip:restrictedBy ?r . " + not_typed("r", "Article", "ArticleUnit", "Appendix")),
    ("SCH-ODP-02", G_ODP, "[규제 조건] 구역마다 근거 법령(governedBy)이 있음",
     "?z a aip:Zone ; aip:governedBy ?l .",
     "?focus a aip:Zone . FILTER NOT EXISTS { ?focus aip:governedBy ?l }"),
    ("SCH-ODP-03", G_ODP, "[규제 조건] 한도 진술(LimitStatement)은 모두 조문·별표의 hasLimit로 연결됨(고아 진술 없음)",
     "?x aip:hasLimit ?focus . ?focus a aip:LimitStatement .",
     "?focus a aip:LimitStatement . FILTER NOT EXISTS { ?x aip:hasLimit ?focus }"),
    ("SCH-ODP-04", G_ODP, "[규제 조건] 한도 조건(LimitCondition)은 모두 한도 진술의 hasLimitCondition으로 연결됨",
     "?s aip:hasLimitCondition ?c . ?c a aip:LimitCondition .",
     "?focus a aip:LimitCondition . FILTER NOT EXISTS { ?s aip:hasLimitCondition ?focus }"),
    ("SCH-ODP-05", G_ODP, "[예외] 적용 배제 항목(Exemption)마다 대상 구역(inZone)과 배제 조문(exemptsArticle)이 있음",
     "?e a aip:Exemption ; aip:inZone ?z ; aip:exemptsArticle ?a .",
     "?focus a aip:Exemption . FILTER (NOT EXISTS { ?focus aip:inZone ?z } || NOT EXISTS { ?focus aip:exemptsArticle ?a })"),
    ("SCH-ODP-06", G_ODP, "[예외] 배제 조문(exemptsArticle) 대상이 모두 조문 노드(조·항·별표)임",
     "?e aip:exemptsArticle ?a .",
     "?e aip:exemptsArticle ?focus . " + not_typed("focus", "Article", "ArticleUnit", "Appendix")),
    ("SCH-ODP-07", G_ODP, "[예외] 적용 제외(excludesArticle)의 주체·대상이 모두 조문 노드임",
     "?s aip:excludesArticle ?o .",
     "{ ?focus aip:excludesArticle ?o . " + not_typed("focus", "Article", "ArticleUnit", "Appendix") + " } UNION "
     "{ ?s aip:excludesArticle ?focus . " + not_typed("focus", "Article", "ArticleUnit") + " }"),
    ("SCH-ODP-08", G_ODP, "[위계] 구체화(implementedBy)는 상위 법령 → 같거나 하위 법령 방향임(법 → 시행령·규칙 → 조례)",
     "?a aip:implementedBy ?b .",
     node_hier("focus", "ha", "la") + " ?focus aip:implementedBy ?b . " + node_hier("b", "hb", "lb") + " FILTER (?hb < ?ha)"),
    ("SCH-ODP-09", G_ODP, "[위계] 상위 법령(parentLaw)은 하위 법령보다 위계가 높음(위계 숫자가 작음)",
     "?l aip:parentLaw ?p .",
     "?focus aip:parentLaw ?p . ?focus aip:hierarchy ?hc . ?p aip:hierarchy ?hp . FILTER (?hp >= ?hc)"),
    ("SCH-ODP-10", G_ODP, "[별표 근거] 별표의 근거 조문(relatedArticle)이 모두 조·항 노드임",
     "?p a aip:Appendix ; aip:relatedArticle ?a .",
     "?p aip:relatedArticle ?focus . " + not_typed("focus", "Article", "ArticleUnit")),
    # ── 핵심 클래스 ──────────────────────────────────────────────────────
    *[(f"SCH-CLS-{i:02d}", G_CLS, f"클래스 {c}: owl:Class 선언·라벨·설명(rdfs:comment)이 있고 인스턴스가 1개 이상 있음",
       f"aip:{c} a owl:Class ; rdfs:label ?l ; rdfs:comment ?m . ?x a aip:{c} .",
       "FILTER (false)")
      for i, c in enumerate(["Law", "Article", "ArticleUnit", "Appendix", "Zone", "Exemption",
                             "LimitStatement", "LimitCondition", "Sido"], 1)],
    ("SCH-CLS-10", G_CLS, "인스턴스 데이터에 쓰인 핵심 어휘(aip:) 클래스가 모두 스키마에 owl:Class로 선언됨 (하위 경로 어휘 aip/rule/ 등은 별도 어휘라 제외, DA2-4에서 점검)",
     "?x a ?c . FILTER (STRSTARTS(STR(?c), STR(aip:)) && !CONTAINS(STRAFTER(STR(?c), STR(aip:)), '/'))",
     "?x a ?focus . FILTER (STRSTARTS(STR(?focus), STR(aip:)) && !CONTAINS(STRAFTER(STR(?focus), STR(aip:)), '/')) FILTER NOT EXISTS { ?focus a owl:Class }"),
    # ── 객체 관계·데이터 속성 ─────────────────────────────────────────────
    ("SCH-PRP-01", G_PRP, "인스턴스 데이터에 쓰인 핵심 어휘(aip:) 속성이 모두 스키마에 ObjectProperty·DatatypeProperty로 선언됨 (aip/rule/ 등 별도 어휘 제외, DA2-4에서 점검)",
     "?s ?p ?o . FILTER (STRSTARTS(STR(?p), STR(aip:)) && !CONTAINS(STRAFTER(STR(?p), STR(aip:)), '/'))",
     "{ SELECT DISTINCT ?focus WHERE { ?s ?focus ?o . FILTER (STRSTARTS(STR(?focus), STR(aip:)) && !CONTAINS(STRAFTER(STR(?focus), STR(aip:)), '/')) } } "
     "FILTER NOT EXISTS { VALUES ?pt { owl:ObjectProperty owl:DatatypeProperty owl:AnnotationProperty } ?focus a ?pt }"),
    ("SCH-PRP-02", G_PRP, "객체 속성(ObjectProperty)의 값이 모두 IRI임(리터럴 값 없음)",
     "?p a owl:ObjectProperty . ?s ?p ?o .",
     "?p a owl:ObjectProperty . ?focus ?p ?o . FILTER isLiteral(?o)"),
    ("SCH-PRP-03", G_PRP, "데이터 속성(DatatypeProperty)의 값이 모두 리터럴임",
     "?p a owl:DatatypeProperty . ?s ?p ?o .",
     "?p a owl:DatatypeProperty . ?focus ?p ?o . FILTER (!isLiteral(?o))"),
    ("SCH-PRP-04", G_PRP, "hasArticle: 법령(Law) → 조문(Article) (선언된 domain·range 준수)",
     "?l aip:hasArticle ?a .",
     "{ ?focus aip:hasArticle ?a . " + not_typed("focus", "Law") + " } UNION { ?l aip:hasArticle ?focus . " + not_typed("focus", "Article") + " }"),
    ("SCH-PRP-05", G_PRP, "hasAppendix: 법령(Law) → 별표(Appendix)",
     "?l aip:hasAppendix ?a .",
     "{ ?focus aip:hasAppendix ?a . " + not_typed("focus", "Law") + " } UNION { ?l aip:hasAppendix ?focus . " + not_typed("focus", "Appendix") + " }"),
    ("SCH-PRP-06", G_PRP, "hasUnit: 값이 모두 조문 하위단위(ArticleUnit)임 (선언된 range 준수)",
     "?a aip:hasUnit ?u .",
     "?a aip:hasUnit ?focus . " + not_typed("focus", "ArticleUnit")),
    ("SCH-PRP-07", G_PRP, "implementedBy: 주체는 조·항, 대상은 조·항·별표 (선언된 domain·range 준수)",
     "?a aip:implementedBy ?b .",
     "{ ?focus aip:implementedBy ?b . " + not_typed("focus", "Article", "ArticleUnit") + " } UNION "
     "{ ?a aip:implementedBy ?focus . " + not_typed("focus", "Article", "ArticleUnit", "Appendix") + " }"),
    ("SCH-PRP-08", G_PRP, "restrictedBy·governedBy·designatedBy: 주체가 모두 구역(Zone)임",
     "?z aip:restrictedBy ?r .",
     "VALUES ?p { aip:restrictedBy aip:governedBy aip:designatedBy } ?focus ?p ?o . " + not_typed("focus", "Zone")),
    ("SCH-PRP-09", G_PRP, "법령마다 법령명(lawName, 문자열)과 위계(hierarchy, 정수)가 있음",
     "?l a aip:Law ; aip:lawName ?n ; aip:hierarchy ?h .",
     "?focus a aip:Law . FILTER (NOT EXISTS { ?focus aip:lawName ?n } || NOT EXISTS { ?focus aip:hierarchy ?h2 . FILTER (datatype(?h2) = xsd:integer) })"),
    ("SCH-PRP-10", G_PRP, "함수형 속성(owl:FunctionalProperty)은 주체마다 값이 1개 이하임",
     "?p a owl:FunctionalProperty . ?s ?p ?o .",
     "?p a owl:FunctionalProperty . ?focus ?p ?o1 , ?o2 . FILTER (?o1 != ?o2)"),
    # 11-12 규격서 v.14: 평가항목 50개 — 선언된 domain·range 준수 점검을 아직 보지 않은 주요 관계 3개로 확장
    ("SCH-PRP-11", G_PRP, "hasLimit: 주체는 조·항·별표, 대상은 한도 진술(LimitStatement) (선언된 domain·range 준수)",
     "?x aip:hasLimit ?l .",
     "{ ?focus aip:hasLimit ?l . " + not_typed("focus", "Article", "ArticleUnit", "Appendix") + " } UNION "
     "{ ?x aip:hasLimit ?focus . " + not_typed("focus", "LimitStatement") + " }"),
    ("SCH-PRP-12", G_PRP, "delegatesTo: 주체는 조·항·별표, 대상은 법령·조·항 (선언된 domain·range 준수)",
     "?a aip:delegatesTo ?b .",
     "{ ?focus aip:delegatesTo ?b . " + not_typed("focus", "Article", "ArticleUnit", "Appendix") + " } UNION "
     "{ ?a aip:delegatesTo ?focus . " + not_typed("focus", "Law", "Article", "ArticleUnit") + " }"),
    ("SCH-PRP-13", G_PRP, "relatedArticle: 주체가 모두 별표(Appendix) (선언된 domain 준수)",
     "?p aip:relatedArticle ?a .",
     "?focus aip:relatedArticle ?a . " + not_typed("focus", "Appendix")),
]


def ask_text(item):
    _id, grp, desc, exists, viol = item
    return (f"# {_id} [{grp}] {desc}\n# true = 적합 (존재 패턴 1건 이상, 위반 패턴 0건)\n" + PFX +
            f"ASK {{\n  {{ SELECT (COUNT(*) AS ?n_exist) WHERE {{ {exists} }} }}\n"
            f"  {{ SELECT (COUNT(*) AS ?n_viol) WHERE {{ {viol} }} }}\n"
            f"  FILTER (?n_exist > 0 && ?n_viol = 0)\n}}\n")


def viol_text(item):
    """부적합일 때 위반 노드 표본(메시지용). 판정에는 쓰지 않음"""
    return PFX + f"SELECT DISTINCT ?focus WHERE {{ {item[4]} }} LIMIT 5"


def shape_items():
    """SHACL Shape 파일의 NodeShape 1개 = 평가항목 1개"""
    import rdflib
    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    g = rdflib.Graph()
    g.parse(TTL["shapes"])
    out = []
    for i, s in enumerate(sorted(g.subjects(rdflib.RDF.type, SH.NodeShape), key=str), 1):
        name = str(s).split("/")[-1]
        tgt = [str(x).split("/")[-1] for x in g.objects(s, SH.targetClass)] or \
              [f"{str(p).split('#')[-1]}" for p in (SH.targetSubjectsOf, SH.targetObjectsOf, SH.targetNode) if (s, p, None) in g]
        out.append((f"SCH-SHC-{i:02d}", G_SH, f"SHACL {name} (대상: {', '.join(tgt) or '-'}) 위반 없음", str(s)))
    return out


def freeze():
    rows = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for it in ASK_ITEMS:
            fn = f"checks/{it[0]}.rq"
            z.writestr(fn, ask_text(it))
            rows.append([it[0], it[1], it[2], "SPARQL ASK", fn, "true"])
        for _id, grp, desc, uri in shape_items():
            rows.append([_id, grp, desc, "SHACL", f"landcheck_shapes_v1.0.ttl#{uri.split('/')[-1]}", "true"])
        s = io.StringIO()
        w = csv.writer(s)
        w.writerow(["check_id", "item_group", "description", "method", "validator_file", "expected"])
        w.writerows(rows)
        z.writestr("da2_schema_checklist.csv", "﻿" + s.getvalue())
    with open(ZIP, "wb") as f:
        f.write(buf.getvalue())
    print(ZIP, len(rows), "항목")


def load_checklist(zp=ZIP):
    with zipfile.ZipFile(zp) as z:
        rows = list(csv.DictReader(io.StringIO(z.read("da2_schema_checklist.csv").decode("utf-8-sig"))))
        rq = {r["check_id"]: z.read(r["validator_file"]).decode("utf-8") for r in rows if r["method"] == "SPARQL ASK"}
    return rows, rq


def run(out_dir, zp=ZIP, only=None):
    import pyoxigraph as ox
    import pyshacl
    import rdflib
    rows, rq = load_checklist(zp)
    if only:
        rows = [r for r in rows if r["check_id"] in only]
    t0 = time.time()
    st = ox.Store()
    for k in ("schema", "instance", "shapes"):
        st.bulk_load(path=TTL[k], format=ox.RdfFormat.TURTLE)
    print(f"적재 {len(st):,} 트리플 ({time.time() - t0:.1f}s)", flush=True)
    by_id = {it[0]: it for it in ASK_ITEMS}
    res = []
    for r in rows:
        if r["method"] != "SPARQL ASK":
            continue
        t = time.time()
        rec = dict(r, actual="", conforms="", focus_node="", result_path="", message="", status="completed", error_message="")
        try:
            ok = bool(st.query(rq[r["check_id"]]))
            rec["actual"] = str(ok).lower()
            rec["conforms"] = str(ok == (r["expected"] == "true")).lower()
            if not ok and r["check_id"] in by_id:
                foc = [str(b["focus"].value) if hasattr(b["focus"], "value") else str(b["focus"]) for b in st.query(viol_text(by_id[r["check_id"]]))]
                rec["focus_node"] = json.dumps(foc, ensure_ascii=False)
                rec["message"] = "위반 노드 있음" if foc else "존재 패턴 없음(해당 데이터 없음)"
        except Exception as e:                                      # noqa: BLE001
            rec.update(status="error", error_message=f"{type(e).__name__}: {e}")
        rec["elapsed_ms"] = int((time.time() - t) * 1000)
        res.append(rec)
        print(r["check_id"], rec["actual"], rec["message"], rec["elapsed_ms"], "ms", flush=True)
    shc = [r for r in rows if r["method"] == "SHACL"]
    if shc:                                                       # Shape 전체를 한 번 검증하고 결과를 NodeShape별로 모음
        t = time.time()
        d = rdflib.Graph()
        d.parse(TTL["instance"])
        d.parse(TTL["schema"])
        sg = rdflib.Graph()
        sg.parse(TTL["shapes"])
        SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        parent = {}
        for ns in sg.subjects(rdflib.RDF.type, SH.NodeShape):
            parent[ns] = ns
            for ps in sg.objects(ns, SH.property):
                parent[ps] = ns
        try:
            _, rg, _ = pyshacl.validate(d, shacl_graph=sg, inference="none", allow_warnings=True)
            viols = {}
            for v in rg.subjects(rdflib.RDF.type, SH.ValidationResult):
                if rg.value(v, SH.resultSeverity) != SH.Violation:
                    continue
                ns = parent.get(rg.value(v, SH.sourceShape), rg.value(v, SH.sourceShape))
                viols.setdefault(str(ns).split("/")[-1], []).append(
                    (str(rg.value(v, SH.focusNode)), str(rg.value(v, SH.resultPath) or ""), str(rg.value(v, SH.resultMessage) or "")))
            err = ""
        except Exception as e:                                      # noqa: BLE001
            viols, err = None, f"{type(e).__name__}: {e}"
        ms = int((time.time() - t) * 1000)
        for r in shc:
            name = r["validator_file"].split("#")[-1]
            rec = dict(r, actual="", conforms="", focus_node="", result_path="", message="", status="completed", error_message="", elapsed_ms=ms)
            if viols is None:
                rec.update(status="error", error_message=err)
            else:
                v = viols.get(name, [])
                ok = not v
                rec["actual"] = str(ok).lower()
                rec["conforms"] = str(ok == (r["expected"] == "true")).lower()
                if v:
                    rec["focus_node"] = json.dumps([x[0] for x in v[:5]], ensure_ascii=False)
                    rec["result_path"] = json.dumps(sorted({x[1] for x in v})[:5], ensure_ascii=False)
                    rec["message"] = f"위반 {len(v)}건: " + "; ".join(sorted({x[2] for x in v})[:3])
            res.append(rec)
            print(r["check_id"], rec["actual"], rec["message"][:80], flush=True)
    order = {r["check_id"]: i for i, r in enumerate(rows)}
    res.sort(key=lambda x: order[x["check_id"]])
    os.makedirs(out_dir, exist_ok=True)
    cols = ["check_id", "item_group", "description", "method", "validator_file", "expected", "actual", "conforms",
            "focus_node", "result_path", "message", "elapsed_ms", "status", "error_message"]
    with open(os.path.join(out_dir, "validation_result_da2.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(res)
    n = len(res)
    ok = sum(r["conforms"] == "true" for r in res)
    print(f"적합 {ok}/{n} = {int(ok / n * 10000) / 100 if n else 0:.2f}%")
    return res


if __name__ == "__main__":
    if sys.argv[1] == "freeze":
        freeze()
    elif sys.argv[1] == "run":
        run(sys.argv[2], only=set(sys.argv[3:]) or None)
