# [DA 2-1] CQ별 SPARQL — CQ 문장의 뜻대로 작성 (규격서: 정답 확정 후 작성, 질의에 정답값 직접 기입·LIMIT 금지)
# 결과 변수는 ?ans 하나: 조문·항·별표 노드(IRI) 또는 값(리터럴). QA는 노드의 rdfs:label("건축법 제44조제1항", "국토계획법시행령 별표17")을
# 법령ID.조.항 으로 정규화해 정답 집합과 비교한다 (호·목 제외, 수치는 소수점 셋째자리 반올림).
# 추론 의존 CQ도 OWL Reasoner 없이 실행 가능한 관계(implementedBy·restrictedBy·Exemption 등)로 작성 — Reasoner 적용은 실행 단계에서.
PREFIX = """PREFIX aip: <https://onto.go.kr/aip/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""

BLD, BLDR = "건축법", "건축법 시행령"
LP, LPR = "국토의 계획 및 이용에 관한 법률", "국토의 계획 및 이용에 관한 법률 시행령"
GB, GBR = "개발제한구역의 지정 및 관리에 관한 특별조치법", "개발제한구역의 지정 및 관리에 관한 특별조치법 시행령"
FARM, FARMR = "농지법", "농지법 시행령"
MT, MTR = "산지관리법", "산지관리법 시행령"
RIV = "하천법"
HANOKR = "한옥 등 건축자산의 진흥에 관한 법률 시행령"
YS_BLD, YS_PLAN, AS_PLAN = "음성군 건축 조례", "음성군 군계획 조례", "아산시 도시계획 조례"

# 법령 ?law 에 속한 조문·항·호·별표 노드 ?ans (조문/별표 아래 하위 단위까지)
IN_LAW = "?{law} (aip:hasArticle|aip:hasAppendix)/aip:hasUnit* ?{node} ."


def law(name, var="law"):
    return f'?{var} aip:lawName "{name}" .'


def zone(label, var="z"):
    return f'?{var} a aip:Zone ; skos:prefLabel ?{var}l . FILTER(STR(?{var}l) = "{label}")'


def zone_restricted_in(zone_label, law_name, kind=None):
    """지역·지구 zone_label 의 행위제한 근거(restrictedBy) 중 law_name 에 속한 것 (kind: Appendix/Article 한정)"""
    k = f"?ans a aip:{kind} ." if kind else ""
    return f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g1 {{ {zone(zone_label)} ?z aip:restrictedBy ?ans . }}
  GRAPH ?g2 {{ {law(law_name)} }} GRAPH ?g3 {{ {IN_LAW.format(law='law', node='ans')} {k} }} }}"""


def article_by_title(law_name, title_part, units=False, extra=""):
    """법령의 조 제목에 title_part 가 들어간 조문 (units=True면 그 조의 항)"""
    tail = "?a aip:hasUnit ?ans . ?ans aip:unitType ?ut . FILTER(STR(?ut) = \"항\")" if units else "BIND(?a AS ?ans)"
    return f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g {{ {law(law_name)} ?law aip:hasArticle ?a . ?a aip:articleTitle ?t .
  FILTER(CONTAINS(STR(?t), "{title_part}")) {tail} {extra} }} }}"""


def upper_of(law_name, article_no, upper_hier=None):
    """조례·시행령 조문(law_name 제article_no조)을 implementedBy 로 가리키는 상위 조문·항 (upper_hier: 상위 법령 위계)"""
    h = f"GRAPH ?g4 {{ ?ul aip:hierarchy {upper_hier} . }} GRAPH ?g5 {{ {IN_LAW.format(law='ul', node='ans')} }}" if upper_hier else ""
    return f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g1 {{ {law(law_name)} ?law aip:hasArticle ?a . ?a aip:articleNo ?n . FILTER(STR(?n) = "{article_no}") }}
  GRAPH ?g2 {{ ?ans aip:implementedBy ?t . }} GRAPH ?g3 {{ ?a aip:hasUnit* ?t . }} {h} }}"""


def limit_value(at_prefix, quantity=None):
    q = f'?l aip:limitQuantity ?q . FILTER(CONTAINS(STR(?q), "{quantity}"))' if quantity else ""
    return f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g {{ ?l a aip:LimitStatement ; aip:limitAt ?at ; aip:limitValue ?ans .
  FILTER(STRSTARTS(STR(?at), "{at_prefix}")) {q} }} }}"""


def exemption_article(zone_label):
    """zone_label 에서 성립하는 적용 배제(Exemption)를 정한 조문"""
    return f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g1 {{ ?x a aip:Exemption ; aip:inZone ?z ; aip:exemptsArticle ?ans . }}
  GRAPH ?g2 {{ {zone(zone_label)} }} }}"""


SPARQL = {
    # ---- Regulation-Constraint (규제 조건 도출)
    "CQ-006": f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g {{ {law(BLD)} ?law aip:hasArticle ?a . ?a aip:articleTitle ?t . FILTER(STR(?t) = "정의")
  ?a aip:hasUnit ?ans . ?ans aip:hasUnit ?ho . ?ho aip:unitLabel ?lb . FILTER(CONTAINS(STR(?lb), "도로")) }} }}""",
    "CQ-008": limit_value("건축법.제2조", "너비"),
    "CQ-002": limit_value("건축법.제44조", "길이"),
    "CQ-003": article_by_title(BLD, "대지와 도로의 관계", units=True, extra="?ans aip:restrictionKind ?rk ."),
    "CQ-009": f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g1 {{ {law(LP)} ?law aip:hasArticle ?a . ?a aip:articleTitle ?t . FILTER(CONTAINS(STR(?t), "용도지역"))
  ?a aip:hasUnit ?ans . ?ans aip:delegatesTo ?d . }} GRAPH ?g2 {{ ?dl aip:hierarchy 2 . {IN_LAW.format(law='dl', node='d')} ?d aip:articleTitle|^aip:hasUnit/aip:articleTitle ?dt .
  FILTER(CONTAINS(STR(?dt), "용도지역안에서의 건축제한")) }} }}""",
    "CQ-010": f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g1 {{ {law(LP)} ?law aip:hasArticle ?a . ?a aip:articleTitle ?t . FILTER(CONTAINS(STR(?t), "용도지구"))
  ?a aip:hasUnit ?ans . ?ans aip:implementedBy ?d . }} GRAPH ?g2 {{ ?dl aip:hierarchy 5 . {IN_LAW.format(law='dl', node='d')} }} }}""",
    "CQ-011": zone_restricted_in("자연녹지지역", LPR, "Appendix"),
    "CQ-012": zone_restricted_in("보전녹지지역", LPR, "Appendix"),
    "CQ-013": zone_restricted_in("생산녹지지역", LPR, "Appendix"),
    "CQ-014": zone_restricted_in("계획관리지역", LPR, "Appendix"),
    "CQ-015": zone_restricted_in("농림지역", LPR, "Appendix"),
    "CQ-016": zone_restricted_in("자연환경보전지역", LPR, "Appendix"),
    "CQ-017": zone_restricted_in("제1종전용주거지역", LPR, "Appendix"),
    "CQ-018": zone_restricted_in("제1종일반주거지역", LPR, "Appendix"),
    "CQ-019": zone_restricted_in("개발제한구역", GB),
    "CQ-020": zone_restricted_in("농업진흥구역", FARM),
    "CQ-021": zone_restricted_in("임업용산지", MT),
    "CQ-022": zone_restricted_in("공익용산지", MT),
    "CQ-023": f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g1 {{ ?z a aip:Zone ; aip:zoneCategory ?c . FILTER(STR(?c) = "도시계획시설") ?z aip:restrictedBy ?ans . }}
  GRAPH ?g2 {{ {law(LP)} }} GRAPH ?g3 {{ {IN_LAW.format(law='law', node='ans')} }} }}""",
    "CQ-024": zone_restricted_in("하천구역", RIV),
    # ---- Exception (예외·단서 적용)
    "CQ-001": article_by_title(BLD, "대지와 도로의 관계", units=True, extra="?ans aip:hasUnit ?ho ."),
    "CQ-025": exemption_article("농림지역"),
    "CQ-026": zone_restricted_in("자연취락지구", LPR, "Article"),
    "CQ-027": zone_restricted_in("농업진흥구역", FARMR),
    "CQ-028": zone_restricted_in("개발제한구역", GBR, "Appendix"),
    "CQ-029": zone_restricted_in("임업용산지", MTR),
    "CQ-030": zone_restricted_in("공익용산지", MTR),
    "CQ-031": article_by_title(LP, "도시·군계획시설부지", units=True, extra="?ans aip:hasUnit ?ho . ?ho aip:unitLabel ?lb . FILTER(CONTAINS(STR(?lb), \"가설\"))"),
    "CQ-032": f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g {{ {law(LP)} ?law aip:hasArticle ?a . ?a aip:articleTitle ?t . FILTER(CONTAINS(STR(?t), "지구단위계획"))
  ?a aip:hasUnit ?ans . ?ans aip:relaxesTarget|^aip:relaxesTarget ?r . }} }}""",
    "CQ-033": upper_of(BLD, "44").replace("?ans aip:implementedBy ?t", "?t aip:implementedBy ?ans").replace("?a aip:hasUnit* ?t", "?a aip:hasUnit* ?t"),
    "CQ-034": article_by_title(BLDR, "도로의 구조와 너비"),
    "CQ-035": article_by_title(FARM, "농지의 전용허가", units=True, extra="?ans aip:hasUnit ?ho ."),
    "CQ-036": f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g1 {{ {zone("성장관리계획구역")} ?z aip:restrictedBy ?ans . }}
  GRAPH ?g2 {{ {law(LP)} }} GRAPH ?g3 {{ {IN_LAW.format(law='law', node='ans')} }} }}""",
    # ---- Conflict (법령·조례 충돌 해소)
    "CQ-004": f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g1 {{ {law(HANOKR)} {IN_LAW.format(law='law', node='ans')} ?ans a aip:Appendix . }}
  GRAPH ?g2 {{ {law(YS_BLD, 'yl')} ?yl aip:hasArticle ?ya . ?ya aip:articleTitle ?yt . FILTER(CONTAINS(STR(?yt), "대지안의 공지")) }}
  GRAPH ?g3 {{ ?ans aip:excludesArticle|aip:exemptsArticle|aip:relaxesTarget ?ya . }} }}""",
    "CQ-037": article_by_title(LP, "둘 이상의 용도지역", units=True),
    "CQ-038": article_by_title(LP, "둘 이상의 용도지역", units=True, extra="?ans aip:hasUnit*/aip:unitLabel ?lb . FILTER(CONTAINS(STR(?lb), \"녹지지역\"))"),
    "CQ-039": f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g1 {{ ?x a aip:Exemption ; aip:exemptsArticle ?ans ; aip:inZone ?z . }}
  GRAPH ?g2 {{ {zone("공익용산지")} }} GRAPH ?g3 {{ {law(MT)} {IN_LAW.format(law='law', node='ans')} }} }}""",
    "CQ-040": f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g1 {{ {zone("지구단위계획구역")} ?z aip:restrictedBy ?ans . }}
  GRAPH ?g2 {{ {law(LP)} }} GRAPH ?g3 {{ {IN_LAW.format(law='law', node='ans')} }} }}""",
    "CQ-041": exemption_article("자연환경보전지역"),
    "CQ-042": article_by_title(LPR, "건축제한의 예외"),
    "CQ-043": article_by_title(BLD, "적용 제외", units=True, extra="?ans aip:hasUnit ?ho ."),
    "CQ-044": article_by_title(BLD, "적용 제외", units=True, extra="?ans aip:excludesArticle ?ex ."),
    "CQ-045": exemption_article("농업진흥구역"),
    # ---- 위계 추적 (법·시행령·시행규칙·조례 위임)
    "CQ-005": upper_of(YS_BLD, "21", upper_hier=2),
    "CQ-007": f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g1 {{ {law(GBR)} ?law aip:hasAppendix ?ap . ?ap aip:appendixNo ?no . FILTER(STR(?no) = "1")
  ?ap aip:relatedArticle ?ans . }} }}""",
    "CQ-046": f"""SELECT DISTINCT ?ans WHERE {{ GRAPH ?g1 {{ {law(LPR)} ?law aip:hasAppendix ?ap . ?ap aip:appendixNo ?no . FILTER(STR(?no) = "17")
  ?ap aip:relatedArticle ?r . }} GRAPH ?g2 {{ ?ans aip:hasUnit* ?r . ?ans aip:unitType ?ut . FILTER(STR(?ut) = "항") }} }}""",
    "CQ-047": upper_of(LPR, "71", upper_hier=1),
    "CQ-048": upper_of(BLDR, "28", upper_hier=1),
    "CQ-049": upper_of(YS_PLAN, "35", upper_hier=2),
    "CQ-050": upper_of(AS_PLAN, "27", upper_hier=2),
}


def query(cid):
    return PREFIX + SPARQL[cid]


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from tools.da2_cq_draft import CQS
    ids = {c[0] for c in CQS}
    print("CQ", len(ids), "SPARQL", len(SPARQL), "빠진 것", sorted(ids - set(SPARQL)), "남는 것", sorted(set(SPARQL) - ids))
    import re
    bad = [c for c, q in SPARQL.items() if re.search(r"bLIMITs+d", q, re.I)]
    print("LIMIT 사용:", bad)
