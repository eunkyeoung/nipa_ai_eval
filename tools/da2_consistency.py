# [DA 2-4] 논리적 일관성·무결성 — OWL Reasoner(Pellet) + 계층 순환(SPARQL) + OOPS! 함정 검사
# 규격서 6.1.4:
#   오류 건수 = unsatisfiable class 수 + 계층 순환 검출 수
#   일관성 점수 = inconsistent면 0, consistent면 100 − 오류 건수 × 5 (최저 0)
#   함정 점수 = 100 − (Critical 유형 수 × 10 + Important 유형 수 × 5 + Minor 유형 수 × 1) (최저 0)
#     계수 단위 = 검출된 함정 유형(pitfall) 수. 영향 요소 수(hasNumberAffectedElements)는 세지 않음
#   논리적 일관성·무결성(%) = (일관성 점수 + 함정 점수) / 2
# 대상: 동결한 스키마 파일(landcheck_schema_v1.0.ttl). OOPS!에는 스키마 파일만 보냄(인스턴스 데이터 전송 없음, 2026-11-11 사용자 허용)
# Reasoner: owlready2 내장 Pellet 2.3.1 (Java 25 필요). HermiT는 스키마의 xsd:date를 지원하지 않아(OWL 2 datatype map 밖) 쓰지 않음
#
#   python tools/da2_consistency.py run <out_dir>   → <out_dir>/validation_result_da2.csv, oops_response.xml
import csv, glob, json, os, shutil, sys, time

DA2 = "spec_data/da2"
SCHEMA = f"{DA2}/landcheck_schema_v1.0.ttl"
INSTANCE = f"{DA2}/landcheck_instance_v1.0.ttl"
OOPS_URL = "https://oops.linkeddata.es/rest"
CYCLE_PROPS = ["rdfs:subClassOf", "rdfs:subPropertyOf", "skos:broader"]
PFX = """PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""


def java_exe():
    j = shutil.which("java")
    if j:
        return j
    c = sorted(glob.glob(os.path.expanduser("~/tools/jdk-25*jre*/bin/java.exe")))   # PC(Windows): 사용자 폴더에 풀어 둔 JRE
    return c[0] if c else "java"


def reasoner(out_dir):
    import rdflib
    import owlready2 as o
    o.JAVA_EXE = java_exe()
    nt = os.path.join(out_dir, "_schema.nt")
    g = rdflib.Graph()
    g.parse(SCHEMA)
    g.serialize(nt, format="nt", encoding="utf-8")
    w = o.World()
    onto = w.get_ontology("file://" + os.path.abspath(nt).replace("\\", "/")).load()
    t = time.time()
    try:
        with onto:
            o.sync_reasoner_pellet(w, infer_property_values=False, infer_data_property_values=False, debug=0)
        unsat = sorted(c.iri for c in w.inconsistent_classes() if c is not o.Nothing)
        return dict(consistent=True, unsat=unsat, ms=int((time.time() - t) * 1000), error="")
    except o.OwlReadyInconsistentOntologyError:
        return dict(consistent=False, unsat=[], ms=int((time.time() - t) * 1000), error="")
    finally:
        os.remove(nt)


def cycles():
    import pyoxigraph as ox
    st = ox.Store()
    for f in (SCHEMA, INSTANCE):                                     # skos:broader 개념 계층은 인스턴스(구역)에 있음
        st.bulk_load(path=f, format=ox.RdfFormat.TURTLE)
    out = {}
    for p in CYCLE_PROPS:
        q = PFX + f"SELECT DISTINCT ?x WHERE {{ ?x {p} ?y . ?y {p}+ ?x }}"
        out[p] = sorted(str(b["x"].value) for b in st.query(q))
    return out


def oops(out_dir):
    import rdflib
    import requests
    g = rdflib.Graph()
    g.parse(SCHEMA)
    body = ('<?xml version="1.0" encoding="UTF-8"?><OOPSRequest><OntologyURI></OntologyURI>'
            '<OntologyContent><![CDATA[' + g.serialize(format="xml") + ']]></OntologyContent>'
            '<Pitfalls></Pitfalls><OutputFormat>RDF/XML</OutputFormat></OOPSRequest>')
    r = requests.post(OOPS_URL, data=body.encode("utf-8"), headers={"Content-Type": "application/xml", "User-Agent": "qa-client"}, timeout=300)
    r.raise_for_status()
    with open(os.path.join(out_dir, "oops_response.xml"), "w", encoding="utf-8") as f:   # 원문 응답 보관(증거)
        f.write(r.text)
    O = rdflib.Namespace("http://oops.linkeddata.es/def#")
    rg = rdflib.Graph()
    rg.parse(data=r.text, format="xml")
    pits = []
    for s in set(rg.subjects(O.hasCode, None)):
        code, lvl = str(rg.value(s, O.hasCode)), str(rg.value(s, O.hasImportanceLevel) or "")
        if lvl not in ("Critical", "Important", "Minor"):                      # SUGGESTION·경고는 함정 유형이 아님
            continue
        pits.append(dict(code=code, name=str(rg.value(s, O.hasName)), level=lvl,
                         affected=int(str(rg.value(s, O.hasNumberAffectedElements) or 0)),
                         elements=sorted(str(x) for x in rg.objects(s, O.hasAffectedElement))))
    return sorted(pits, key=lambda x: x["code"])


def run(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    t = time.time()
    try:
        rz = reasoner(out_dir)
    except Exception as e:                                             # noqa: BLE001
        rz = dict(consistent=None, unsat=[], ms=int((time.time() - t) * 1000), error=f"{type(e).__name__}: {str(e)[:500]}")
    rows.append(dict(check_id="CON-01", category="OWL Reasoner", item="온톨로지 일관성 (Pellet 2.3.1)", level="",
                     detected="" if rz["consistent"] is None else str(not rz["consistent"]).lower(), count="" if rz["consistent"] is None else int(not rz["consistent"]),
                     elements="", elapsed_ms=rz["ms"], status="error" if rz["error"] else "completed", error_message=rz["error"]))
    rows.append(dict(check_id="CON-02", category="OWL Reasoner", item="unsatisfiable class", level="",
                     detected="" if rz["error"] else str(bool(rz["unsat"])).lower(), count="" if rz["error"] else len(rz["unsat"]),
                     elements=json.dumps(rz["unsat"][:20], ensure_ascii=False), elapsed_ms=rz["ms"],
                     status="error" if rz["error"] else "completed", error_message=rz["error"]))
    t = time.time()
    try:
        cy, cerr = cycles(), ""
    except Exception as e:                                             # noqa: BLE001
        cy, cerr = {}, f"{type(e).__name__}: {e}"
    for i, p in enumerate(CYCLE_PROPS, 1):
        v = cy.get(p, [])
        rows.append(dict(check_id=f"CYC-{i:02d}", category="계층 순환(SPARQL)", item=f"{p} 순환", level="",
                         detected="" if cerr else str(bool(v)).lower(), count="" if cerr else len(v), elements=json.dumps(v[:20], ensure_ascii=False),
                         elapsed_ms=int((time.time() - t) * 1000), status="error" if cerr else "completed", error_message=cerr))
    t = time.time()
    try:
        pits, oerr = oops(out_dir), ""
    except Exception as e:                                             # noqa: BLE001
        pits, oerr = None, f"{type(e).__name__}: {str(e)[:500]}"
    if pits is None:
        rows.append(dict(check_id="OOPS", category="OOPS!", item="함정 검사", level="", detected="", count="", elements="",
                         elapsed_ms=int((time.time() - t) * 1000), status="error", error_message=oerr))
    for p in pits or []:
        rows.append(dict(check_id=f"OOPS-{p['code']}", category="OOPS!", item=f"{p['code']} {p['name']}", level=p["level"],
                         detected="true", count=1, elements=json.dumps([e.split("/")[-1] for e in p["elements"]][:20], ensure_ascii=False),
                         affected=p["affected"], elapsed_ms=int((time.time() - t) * 1000), status="completed", error_message=""))
    # 점수
    err = None if rz["consistent"] is None or cerr else len(rz["unsat"]) + sum(len(v) for v in cy.values())
    cons = None if err is None else (0 if not rz["consistent"] else max(0, 100 - err * 5))
    lv = {k: sum(p["level"] == k for p in (pits or [])) for k in ("Critical", "Important", "Minor")}
    pit = None if pits is None else max(0, 100 - (lv["Critical"] * 10 + lv["Important"] * 5 + lv["Minor"] * 1))
    total = None if cons is None or pit is None else (cons + pit) / 2
    summary = dict(consistent=rz["consistent"], unsatisfiable=len(rz["unsat"]), cycles={k: len(v) for k, v in cy.items()},
                   error_count=err, consistency_score=cons, pitfall_types=lv, pitfall_score=pit,
                   value=None if total is None else int(total * 100) / 100,
                   tools=dict(reasoner="Pellet 2.3.1 (owlready2 0.51)", oops=OOPS_URL))
    cols = ["check_id", "category", "item", "level", "detected", "count", "affected", "elements", "elapsed_ms", "status", "error_message"]
    with open(os.path.join(out_dir, "validation_result_da2.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(out_dir, "summary_da2_4.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(sys.argv[2])
