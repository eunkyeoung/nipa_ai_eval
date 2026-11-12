# [DA 2-3] 온톨로지 기반 데이터 매핑 정확도 — 매핑 검증 표본·정답 작성(build), 실행(run)
# 규격서 6.1.3: 1건 = 원천 데이터 필드 1개 → 온톨로지 요소 1개.
#   클래스·속성·관계·자료형 4개 검증 단위를 모두 만족할 때만 정확 매핑. 정확도 = 정확 매핑 / 전체 × 100
#
# 원천: ① 법령 수집 DB(116:17801, law_collect) — API로 실제 레코드를 읽어 표본 값으로 고정(조회만)
#       ② 토지특성 공간 DB 명세(ipss_spatial_db.html)에 실린 실제 표본 행 — sido, land_use_zone_code
# 정답(expected_mapping): 원천 필드의 뜻과 **스키마 설계 문서**(domain·range·rdfs:comment)로 정함. 인스턴스 데이터를 보고 정하지 않음.
# 실제(actual_mapping): 동결한 ttl을 읽어 대상 노드에서 구함
#   class    = 대상 노드의 rdf:type (aip·skos 어휘)
#   property = 대상 노드에 기대 속성의 리터럴 값이 있으면 그 속성 (규격서 검증 단위는 구조 — 값 일치는 판정에 넣지 않음)
#   (보조) value_match = 그 값이 원천 값과 같은지 (스키마 설명에 적힌 변환만). 원천 최신본과 온톨로지 판 차이 등을 드러냄
#   relation = 상위 원천 레코드의 노드 → 대상 노드 를 잇는 속성 (상위 레코드가 없으면 "")
#   datatype = 그 값의 자료형
#
#   python tools/da2_mapping.py build <URL엑셀> <ipss_spatial_db.html>  → spec_data/da2/aips_da2_mapping_testset.csv
#   python tools/da2_mapping.py run <out_dir>                         → <out_dir>/validation_result_da2.csv
import csv, json, os, re, sys, time

DA2 = "spec_data/da2"
TTL = {k: f"{DA2}/landcheck_{k}_v1.0.ttl" for k in ("schema", "instance")}
OUT = f"{DA2}/aips_da2_mapping_testset.csv"
ZONE_SAMPLE = False                                                       # 용도지역 코드 표본 넣지 않음 (11-12, 50건 기준)
AIP = "https://onto.go.kr/aip/"
SKOS = "http://www.w3.org/2004/02/skos/core#"
XSD = "http://www.w3.org/2001/XMLSchema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

# 스키마 설명(rdfs:comment)에 적힌 값 변환 — 정답 작성과 실제 값 비교에 같이 씀
HIER = {"법률": 1, "대통령령": 2, "총리령": 3, "부령": 3, "고시": 4, "훈령": 4, "조례": 5, "규칙": 6}   # hierarchy 설명: 법률1·시행령2·시행규칙3·고시훈령4·조례5·자치규칙6


def hier_of(gubun):
    g = str(gubun)
    if g.endswith("부령"):
        return 3
    for k, v in HIER.items():
        if g == k or g.endswith(k):
            return v
    return None


def norm_value(field, v):
    """원천 값 → 온톨로지 값 형식 (스키마 설명에 적힌 규칙만)"""
    v = str(v).strip()
    if field == "gubun":
        return str(hier_of(v))
    if field in ("effective_date",) and re.fullmatch(r"\d{8}", v):              # effectiveFrom range xsd:date
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    if field == "article_no":                                                  # articleNo 설명: 가지조 '32-2'
        return v.replace("의", "-")
    if field == "byp_no":                                                      # appendixNo 설명: canonical '2의2'
        return v
    return v


def same(field, src, val):
    a, b = norm_value(field, src), str(val).strip()
    if field == "byp_title":                                                   # 별표 제목: 원천 제목(앞뒤 구두점 제거)이 대상 값 안에 그대로 들어 있음
        a = re.sub(r"^[\s.·,]+|[\s.·,]+$", "", a)
        return re.sub(r"\s", "", a) in re.sub(r"\s", "", b)
    return re.sub(r"\s", "", a) == re.sub(r"\s", "", b)


# 정답 매핑 (원천 필드 → 클래스·속성·관계·자료형) — 스키마 domain·range·설명 기준
# skos:prefLabel 은 스키마 밖 외부 어휘라 SKOS 표준 정의(언어 태그 리터럴 = rdf:langString)를 따름
EXPECT = {
    "law.law_id":         dict(cls="aip:Law", prop="aip:lawId", rel="", dt="xsd:string"),
    "law.law_name":       dict(cls="aip:Law", prop="aip:lawName", rel="", dt="xsd:string"),
    "law.gubun":          dict(cls="aip:Law", prop="aip:hierarchy", rel="", dt="xsd:integer"),
    "law.effective_date": dict(cls="aip:Law", prop="aip:effectiveFrom", rel="", dt="xsd:date"),
    "article.article_no": dict(cls="aip:Article", prop="aip:articleNo", rel="aip:hasArticle", dt="xsd:string"),
    "article.title":      dict(cls="aip:Article", prop="aip:articleTitle", rel="aip:hasArticle", dt="xsd:string"),
    "byp.byp_no":         dict(cls="aip:Appendix", prop="aip:appendixNo", rel="aip:hasAppendix", dt="xsd:string"),
    "byp.byp_title":      dict(cls="aip:Appendix", prop="aip:appendixTitle", rel="aip:hasAppendix", dt="xsd:string"),
    "sido.ctpr_cd":       dict(cls="aip:Sido", prop="skos:notation", rel="", dt="xsd:string"),
    "sido.ctpr_nm":       dict(cls="aip:Sido", prop="skos:prefLabel", rel="", dt="rdf:langString"),
    "zone.yongdo_code":   dict(cls="aip:Zone", prop="skos:notation", rel="", dt="xsd:string"),
    "zone.yongdo_name":   dict(cls="aip:Zone", prop="skos:prefLabel", rel="", dt="rdf:langString"),
}

# 표본 레코드 — 시험 대상 지자체(음성군 등)와 성과지표 시험에 쓰인 법령에서 고름
LAWS = ["건축법", "건축법 시행령", "국토의 계획 및 이용에 관한 법률 시행령", "국토의 계획 및 이용에 관한 법률 시행규칙",
        "음성군 건축 조례", "산지관리법"]
ARTICLES = [("건축법", "44"), ("건축법", "2"), ("건축법 시행령", "80의2"), ("국토의 계획 및 이용에 관한 법률 시행령", "71"),
            ("음성군 건축 조례", "30")]
BYPS = [("건축법 시행령", "2"), ("국토의 계획 및 이용에 관한 법률 시행령", "20"), ("음성군 건축 조례", "3")]


def _pfx(c):
    return c.replace(AIP, "aip:").replace(SKOS, "skos:").replace(XSD, "xsd:").replace(RDF, "rdf:")


def build(url_xlsx, spec_html):
    sys.path.insert(0, ".")
    from tools.law_text import LawText, B
    L = LawText(url_xlsx)
    rows = []

    def add(src_table, field, src_value, target, parent, key, note=""):
        e = EXPECT[f"{src_table}.{field}"]
        rows.append(dict(mapping_id=f"MAP-{len(rows) + 1:03d}", source_field=f"{src_table}.{field}", source_key=key,
                         source_value=src_value, target_element=target, parent_element=parent,
                         expected_mapping=json.dumps({"class": e["cls"], "property": e["prop"], "relation": e["rel"], "datatype": e["dt"]},
                                                     ensure_ascii=False), note=note))
    import pyoxigraph as ox
    st = ox.Store()
    st.bulk_load(path=TTL["instance"], format=ox.RdfFormat.TURTLE)

    def law_node(name):                                                  # 원천 레코드의 법령 = 온톨로지에서 같은 법령명을 가진 법령 노드
        r = list(st.query(f'SELECT ?l ?id WHERE {{ ?l <{AIP}lawName> "{name}" . OPTIONAL {{ ?l <{AIP}lawId> ?id }} }}'))
        return (r[0]["l"].value, r[0]["id"].value if r[0]["id"] else "") if len(r) == 1 else ("", "")
    meta, onto_id = {}, {}
    for name in LAWS:
        d = L.s.get(f"{B}/api/documents/{L.docs[L.find(name)]}", timeout=60).json()
        meta[name] = d
        node, onto_id[name] = law_node(d["law_name"])
        for f in ("law_id", "law_name", "gubun", "effective_date"):
            add("law", f, d[f], node, "", f"law_collect.documents doc_id={d['doc_id']}")
    for name, jo in ARTICLES:
        d = meta[name]
        a = next(x for x in L.arts(name) if not str(x.get("is_byp")) == "True" and
                 re.sub(r"\s", "", str(x.get("article_no_full") or "")) == f"제{jo.split('의')[0]}조" + (f"의{jo.split('의')[1]}" if "의" in jo else ""))
        no = str(a["article_no_main"]) + (f"의{a['article_no_branch']}" if a.get("article_no_branch") not in (None, "", "None") else "")
        lid = onto_id[name]
        node = f"{AIP}Art_{lid}_{no.replace('의', '-')}"
        add("article", "article_no", no, node, f"{AIP}Law_{lid}", f"law_collect.articles article_id={a['article_id']}")
        add("article", "title", a["title"], node, f"{AIP}Law_{lid}", f"law_collect.articles article_id={a['article_id']}")
    for name, no in BYPS:
        d = meta[name]
        a = next(x for x in L.arts(name) if str(x.get("is_byp")) == "True" and
                 re.match(rf"^\[?별표\s*{no}\]", str(x.get("article_no_full") or "").strip()))
        lid = onto_id[name]
        node = f"{AIP}Appendix_{lid}_{no}"
        add("byp", "byp_no", no, node, f"{AIP}Law_{lid}", f"law_collect.articles article_id={a['article_id']}")
        add("byp", "byp_title", a["title"], node, f"{AIP}Law_{lid}", f"law_collect.articles article_id={a['article_id']}")
    t = open(spec_html, encoding="utf-8").read()

    def sample(tbl):
        ms = [m.end() for m in re.finditer(r'"%s"\s*:\s*' % tbl, t)]
        for k in ms:                                                        # 첫째는 열 정의, 둘째가 표본 행
            try:
                arr, _ = json.JSONDecoder().raw_decode(t[k:])
            except ValueError:
                continue
            if (isinstance(arr, list) and arr and isinstance(arr[0], list) and len(arr[0]) > 2
                    and not str(arr[0][1]).startswith(("character", "integer", "smallint", "bigint", "numeric", "date", "geometry"))):
                return arr
        return []
    for r in sample("sido"):
        node = f"{AIP}Sido_{r[1]}"
        add("sido", "ctpr_cd", r[1], node, "", f"ipss.sido gid={r[0]}")
        add("sido", "ctpr_nm", r[2], node, "", f"ipss.sido gid={r[0]}")
    # 11-12 규격서 v.14: 매핑 검증 50건 — 용도지역 코드 표본(명세 5행 중 사용 여부를 확인할 수 있는 1행뿐, 근거가 가장 약함)은 넣지 않음
    #   (법령 24 + 조문 10 + 별표 6 + 시도 10 = 50). 아래는 다시 넣을 때를 위해 남겨 둠 (ZONE_SAMPLE=True)
    hist_stop = {r[0] for r in sample("land_use_zone_code_hist") if "사용중지" in str(r[3])}
    for r in (sample("land_use_zone_code") if ZONE_SAMPLE else []):
        if r[0] in hist_stop:                                               # 사용중지 코드는 표본에서 제외 (원천 변경이력 기준)
            continue
        node = f"{AIP}Zone_{r[0]}"
        add("zone", "yongdo_code", r[0], node, "", f"ipss.land_use_zone_code yongdo_code={r[0]}")
        add("zone", "yongdo_name", r[3], node, "", f"ipss.land_use_zone_code yongdo_code={r[0]}")
    cols = ["mapping_id", "source_field", "source_key", "source_value", "target_element", "parent_element", "expected_mapping", "note"]
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(OUT, len(rows), "건")


def run(out_dir, testset=OUT, only=None):
    import pyoxigraph as ox
    st = ox.Store()
    t0 = time.time()
    for k in ("schema", "instance"):
        st.bulk_load(path=TTL[k], format=ox.RdfFormat.TURTLE)
    print(f"적재 {len(st):,} 트리플 ({time.time() - t0:.1f}s)", flush=True)
    rows = list(csv.DictReader(open(testset, encoding="utf-8-sig")))
    if only:
        rows = [r for r in rows if r["mapping_id"] in only]
    res = []
    for r in rows:
        t = time.time()
        exp = json.loads(r["expected_mapping"])
        field = r["source_field"].split(".", 1)[1]
        tgt = ox.NamedNode(r["target_element"])
        rec = dict(r, actual_mapping="", class_match="", property_match="", relation_match="", datatype_match="", exact_match="",
                   value_match="", actual_value="", status="completed", error_message="")
        try:
            types = sorted(_pfx(q.object.value) for q in st.quads_for_pattern(tgt, ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"), None)
                           if q.object.value.startswith((AIP, SKOS)))
            lits = [(_pfx(q.predicate.value), q.object) for q in st.quads_for_pattern(tgt, None, None) if isinstance(q.object, ox.Literal)]
            vals = [o for p, o in lits if p == exp["property"]]
            rel = ""
            if r["parent_element"]:
                rels = sorted(_pfx(q.predicate.value) for q in st.quads_for_pattern(ox.NamedNode(r["parent_element"]), None, tgt))
                rel = rels[0] if len(rels) == 1 else (json.dumps(rels, ensure_ascii=False) if rels else "(연결 없음)")
            act = {"class": types[0] if len(types) == 1 else (types or "(노드 없음)"),
                   "property": exp["property"] if vals else ("(노드 없음)" if not types else "(기대 속성 없음)"),
                   "relation": rel,
                   "datatype": _pfx(vals[0].datatype.value) if vals else ""}
            m = dict(class_match=exp["class"] in types, property_match=bool(vals),
                     relation_match=(rel == exp["relation"]), datatype_match=bool(vals) and act["datatype"] == exp["datatype"])
            rec["value_match"] = str(any(same(field, r["source_value"], o.value) for o in vals)).lower() if vals else ""
            rec["actual_value"] = "; ".join(o.value for o in vals)[:200]
            m["exact_match"] = all(m.values())
            rec.update({k: str(v).lower() for k, v in m.items()})
            rec["actual_mapping"] = json.dumps(act, ensure_ascii=False)
        except Exception as e:                                             # noqa: BLE001
            rec.update(status="error", error_message=f"{type(e).__name__}: {e}")
        rec["elapsed_ms"] = int((time.time() - t) * 1000)
        res.append(rec)
        print(r["mapping_id"], r["source_field"], r["source_value"][:20], rec["exact_match"],
              "" if rec["exact_match"] == "true" else rec["actual_mapping"], flush=True)
    os.makedirs(out_dir, exist_ok=True)
    cols = ["mapping_id", "source_field", "source_key", "source_value", "target_element", "expected_mapping", "actual_mapping",
            "class_match", "property_match", "relation_match", "datatype_match", "exact_match", "value_match", "actual_value",
            "elapsed_ms", "status", "error_message"]
    with open(os.path.join(out_dir, "validation_result_da2.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(res)
    n = len(res)
    ok = sum(x["exact_match"] == "true" for x in res)
    for u in ("class_match", "property_match", "relation_match", "datatype_match"):
        print(u, sum(x[u] == "true" for x in res), "/", n)
    print(f"정확 매핑 {ok}/{n} = {int(ok / n * 10000) / 100 if n else 0:.2f}%")
    print("(보조) 값 일치", sum(x["value_match"] == "true" for x in res), "/", sum(x["value_match"] != "" for x in res))
    return res


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "run":
        run(sys.argv[2], only=set(sys.argv[3:]) or None)
