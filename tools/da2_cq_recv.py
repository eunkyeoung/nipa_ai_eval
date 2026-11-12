# [DA 2-1] 수신 CQ(「CQ 질문답변 작성」 20261029 엑셀) → 정답 집합 + SPARQL
# 규칙: 엑셀 '질문답변 작성' 2행 — 답변 = 정답 조문 전부를 '법령명.조.항'(호·목 제외)으로. zip DA2-1 기준 — CQ 1건=질의 1건=정답 1건=판정 1건,
#       집합 완전 일치, 질의에 정답값 직접 기입·LIMIT 금지.
# 정답 집합: 답변 문장에서 '근거로 든' 조문 전부. 대비·참고로만 언급한 조문은 넣지 않고 비고에 적음(작성자 확인용).
# CQ-014·015: 엑셀에 답변이 없어 QA가 법령 원문(건축법 제46조, 같은 법 시행령 제3조의3)으로 작성.
import sys
sys.path.insert(0, ".")
import re
from tools.da2_cq_sparql import PREFIX, BLD, BLDR

LPRULE = "국토의 계획 및 이용에 관한 법률 시행규칙"
YS_BLD = "음성군 건축 조례"
ROAD = "도로법"

T_RC = "Regulation-Constraint (규제 조건 도출)"
T_EX = "Exception (예외·단서 적용)"
T_CF = "Conflict (법령·조례 충돌 해소)"
T_HI = "위계 추적 (법·시행령·시행규칙·조례 위임)"


# (CQ ID, 유형, 지자체, 질문, 정답 집합, 비고)
CQS = [
    ("CQ-001", T_EX, "공통", "대지가 도로에 2m 미만으로 접한 경우에도 건축이 허용되는 단서 조항은 무엇인가?",
     ["건축법.44.1", "건축법시행령.28.1", "건축법시행령.28.2"], "답변 근거: 법 제44조제1항 단서, 영 제28조제1항(공지), 제28조제2항(대규모 건축물 접도기준)"),
    ("CQ-002", T_RC, "공통", "도시지역 및 지구단위계획구역 밖의 지역에서 대지의 접도기준이 적용되지 않는 행정구역 조건은?\n(면,리 단위 지역에서 대지가 도로에 접하지 않아도 건축이 허용되는 근거 조항은 무엇인가?)",
     ["건축법.3.2"], "답변 근거: 법 제3조제2항. 제44조는 '적용하지 아니하는 조문'으로 언급돼 정답에 넣지 않음 — 작성자 확인"),
    ("CQ-003", T_EX, "공통", "계획관리지역에서 제1종근린생활시설 중 휴게음식점을 설치할 수 있는 단서 조항은 무엇인가?",
     ["국토계획법시행령.71.1", "국토계획법시행령.별표20", "국토계획법시행규칙.별표2"], "답변 근거: 영 제71조제1항제19호, [별표 20], 시행규칙 [별표 2]"),
    ("CQ-004", T_CF, "음성군", "한옥건축시 대지안의 공지 기준은?",
     ["건축법.58", "건축법시행령.80의2", "건축법시행령.별표2", "음성군건축조례.30", "음성군건축조례.별표3"],
     "답변 근거: 법 제58조, 영 제80조의2·[별표 2], 음성군 건축 조례 제30조·[별표 3](한옥 처마선 1m·외벽선 2m)"),
    ("CQ-005", T_HI, "음성군", "음성군 건축 조례에서 천막과 유사한 구조의 창고용 가설건축물을 정한 상위 법령은 무엇인가?",
     ["건축법.20.3", "건축법시행령.15.5"], "답변 근거: 법 제20조제3항 → 영 제15조제5항제16호. 조례 제21조제2항제1호는 질문 대상(하위)이라 정답에 넣지 않음"),
    ("CQ-006", T_RC, "공통", "건축법상 건축물 대지가 접해야 하는 도로의 인정 요건은 무엇인가?",
     ["건축법.2.1", "건축법시행령.3의3", "건축법.44.1"], "답변 근거: 법 제2조제1항제11호, 영 제3조의3, 법 제44조제1항"),
    ("CQ-007", T_EX, "공통", "이해관계인의 동의 없이 사실상의 통로를 건축법상 도로로 지정할 수 있는 경우는?",
     ["건축법.45.1", "건축법.2.1"], "답변 근거: 법 제45조제1항 단서, 법 제2조제1항제11호(도로 요건 별도 확인)"),
    ("CQ-008", T_RC, "공통", "건축법에서 정하는 일반적인 도로의 최소 너비는 얼마인가?",
     ["건축법.2.1", "건축법시행령.3의3"], "답변 근거: 법 제2조제1항제11호(4미터), 영 제3조의3. 엑셀 규칙(답변=조문)에 따라 값(4)이 아닌 조문으로 정답 — 작성자 확인"),
    ("CQ-009", T_RC, "공통", "지목이 도로가 아닌 현황도로를 건축법상 도로로 인정할 수 있는 조건은?",
     ["건축법.2.1", "건축법.45.1"], "답변 근거: 법 제2조제1항제11호, 제45조제1항. 영 제3조의3은 근거 칸에만 있고 답변에 명시가 없어 넣지 않음 — 작성자 확인"),
    ("CQ-010", T_RC, "공통", "사유지에 건축법상 도로가 지정된 경우 그 도로에 접한 대지에서 건축할 수 있는가?",
     ["건축법.2.1", "건축법.44.1"], "답변 근거: 법 제2조제1항제11호, 제44조제1항. 제45조제1항은 근거 칸에만 있어 넣지 않음 — 작성자 확인"),
    ("CQ-011", T_RC, "공통", "지목이 도로가 아닌 사유지나 국유지를 건축법상 도로로 인정받기 위한 요건은?",
     ["건축법.2.1", "건축법.45.1"], "답변 근거: 법 제2조제1항제11호, 제45조제1항. 도로법 제61조는 '다른 절차'로 대비 언급돼 넣지 않음 — 작성자 확인"),
    ("CQ-012", T_RC, "공통", "건축물의 용도별 및 면적에 따른 도로의 폭은?",
     ["건축법.2.1", "건축법시행령.3의3", "건축법시행령.28.2"], "답변 근거: 법 제2조제1항제11호, 영 제3조의3, 영 제28조제2항"),
    ("CQ-013", T_RC, "공통", "도시지역 밖의 동·읍이 아닌 지역에서 건축법상 대지 접도기준이 적용되는가?\n(도시지역외에서의 건축행위에 필요항 도로의 조건은?)",
     ["건축법.3.2"], "답변 근거: 법 제3조제2항. CQ-002와 정답이 같음(중복) — 작성자 확인"),
    ("CQ-014", T_RC, "공통", "대지에 접한 통과 도로가 도로폭 미달일 경우 도로폭 확보 기준은?",
     ["건축법.2.1", "건축법.46.1"], "엑셀 답변 없음 → QA 작성: 법 제46조제1항 단서(소요 너비 미달 시 중심선에서 1/2 후퇴한 선이 건축선), 소요 너비는 법 제2조제1항제11호"),
    ("CQ-015", T_RC, "공통", "대지에 접한 막다른 도로가 도로폭 미달일 경우 도로폭 확보 기준은?",
     ["건축법.46.1", "건축법시행령.3의3"], "엑셀 답변 없음 → QA 작성: 영 제3조의3제2호(막다른 도로 길이별 너비), 법 제46조제1항(후퇴)"),
]

# ── 질의 조각 ──────────────────────────────────────────────────────────────
# 모든 패턴은 GRAPH ?gN 으로 감쌈(법령마다 그래프가 따로 있음). 결과 변수는 ?ans 하나(조·항·별표 노드).
_n = [0]


def G(body):
    """패턴 묶음. 시험 대상은 동결한 ttl 파일(그래프 구분 없이 하나로 내보냄)이라 GRAPH 로 감싸지 않음
    (Fuseki 원본에 직접 돌릴 때만 FUSEKI_GRAPHS=True — 법령마다 이름 있는 그래프가 따로 있음)"""
    _n[0] += 1
    return f"GRAPH ?g{_n[0]} {{ {body} }}" if FUSEKI_GRAPHS else f"{{ {body} }}"


FUSEKI_GRAPHS = False


def art(law_name, kw, var):
    """법령 law_name 에서 조 제목(공백 무시)에 kw 가 들어간 조"""
    return G(f'?L_{var} aip:lawName "{law_name}" . ?L_{var} aip:hasArticle ?{var} . ?{var} aip:articleTitle ?t_{var} . '
             f'FILTER(CONTAINS(REPLACE(STR(?t_{var}), " ", ""), "{kw.replace(" ", "")}"))')


def hang(a, var):
    """조 a 의 항"""
    return G(f'?{a} aip:hasUnit ?{var} . ?{var} aip:unitType ?ut_{var} . FILTER(STR(?ut_{var}) = "항")')


def has_ho(h, ho):
    return G(f"?{h} aip:hasUnit ?{ho} .")


def UNION(*bl):
    return " UNION ".join("{ " + b + " }" for b in bl)


def sel(body):
    return f"SELECT DISTINCT ?ans WHERE {{ {body} }}"


def national(node, v):
    """node 가 국가 법령(법·시행령·시행규칙, hierarchy 1~3)에 속함 — 질문이 '공통'(특정 지자체 아님)일 때"""
    return G(f"?{v}_L (aip:hasArticle|aip:hasAppendix) ?{node} . ?{v}_L aip:hierarchy ?{v}_h . FILTER(?{v}_h <= 3)")


def appx_about(zone_kw, fac_kw, var):
    """국가 법령 별표 중 제목에 zone_kw 가 있고, 제목이나 요약에 fac_kw 가 있는 별표"""
    return " ".join([G(f"?{var} a aip:Appendix ."), G(f'?{var} aip:appendixTitle ?{var}_t . FILTER(CONTAINS(STR(?{var}_t), "{zone_kw}"))'),
                     f"OPTIONAL {{ {G(f'?{var} aip:articleSummary ?{var}_s .')} }}",
                     f'FILTER(CONTAINS(STR(?{var}_t), "{fac_kw}") || CONTAINS(STR(COALESCE(?{var}_s, "")), "{fac_kw}"))',
                     national(var, var + "n")])


# 공통 개념 블록 (질문에 나온 개념으로만 찾음)
def ans_def_of(kw, impl_kw):
    """건축법 '정의' 조에서, 제목에 impl_kw 가 든 하위 법령 조로 구체화되는 호를 가진 항 (예: 도로의 정의)"""
    return " ".join([art(BLD, kw, "d"), hang("d", "ans"), has_ho("ans", "dh"), G("?dh aip:implementedBy ?di ."),
                     G(f'?di aip:articleTitle ?dit . FILTER(CONTAINS(STR(?dit), "{impl_kw}"))')])


def impl_art_of_def(kw, impl_kw):
    """위 정의 호를 구체화하는 하위 법령 조"""
    return " ".join([art(BLD, kw, "e"), G("?e aip:hasUnit+ ?eh ."), G("?eh aip:implementedBy ?ans ."),
                     G(f'?ans aip:articleTitle ?eat . FILTER(CONTAINS(STR(?eat), "{impl_kw}"))'), national("ans", "en")])


def hang_with_delegated_ho(law_name, kw, impl_type=None):
    """조(제목 kw)의 항 중 하위 법령으로 위임된 호(예외·단서 열거)를 가진 항"""
    extra = "" if impl_type is None else " " + G(f'?hl_ans ^aip:hasArticle/aip:lawName ?hln . FILTER(CONTAINS(?hln, "{impl_type}"))')
    return " ".join([art(law_name, kw, "h"), hang("h", "ans"), has_ho("ans", "hh"), G("?hh aip:implementedBy ?hl_ans .")]) + extra


def GONGJI():
    """'대지 안의 공지' 조(?t): 건축법 조, 그 조를 구체화한 같은 주제 하위 조, 음성군 건축 조례 조"""
    return [art(BLD, "대지안의공지", "t"),
            " ".join([art(BLD, "대지안의공지", "u"), G("?u aip:implementedBy ?t ."),
                      G('?t aip:articleTitle ?at . FILTER(CONTAINS(REPLACE(STR(?at), " ", ""), "대지안의공지"))')]),
            art("음성군 건축 조례", "대지안의공지", "t")]


# ── CQ별 질의 ─────────────────────────────────────────────────────────────
SPARQL = {
    # 접도 조항(대지와 도로의 관계)에서 예외 호를 둔 항 + 그 예외 호를 구체화한 하위 법령 조의 항
    "CQ-001": sel(UNION(hang_with_delegated_ho(BLD, "대지와 도로의 관계"),
                        " ".join([art(BLD, "대지와 도로의 관계", "x"), G("?x aip:hasUnit/aip:hasUnit ?xh ."),
                                  G("?xh aip:implementedBy ?xa ."), hang("xa", "ans")]))),
    # 접도 조항을 적용 제외하는 조항
    "CQ-002": sel(" ".join([art(BLD, "대지와 도로의 관계", "x"), G("?ans aip:excludesArticle ?x .")])),
    # 질문의 용도지역(계획관리지역)·시설(휴게음식점)을 다룬 별표 + 그 별표의 근거 조문(항)
    "CQ-003": sel(UNION(
        appx_about("계획관리지역", "휴게음식점", "ans"),
        " ".join([appx_about("계획관리지역", "휴게음식점", "p"), G("?p aip:relatedArticle ?ra ."),
                  G('?ans aip:hasUnit* ?ra . ?ans aip:unitType ?hut . FILTER(STR(?hut) = "항")')]))),
    # 대지 안의 공지: 건축법 조, 그 조를 구체화한 하위 조(같은 주제), 음성군 건축 조례의 같은 주제 조, 그 조들에 딸린 별표
    "CQ-004": sel(UNION(*[blk.replace("?t ", "?ans ").replace("?t .", "?ans .") for blk in GONGJI()],
                        *[" ".join([blk, G("?ans aip:relatedArticle ?t ."), G("?ans a aip:Appendix .")]) for blk in GONGJI()])),
    # 음성군 건축 조례의 가설건축물 조를 위임한 상위 법령 조항(가장 구체적인 위임 단위의 항)
    "CQ-005": sel(" ".join([art("음성군 건축 조례", "가설건축물", "c"), G("?up aip:implementedBy ?c ."),
                            G('?h aip:hasUnit* ?up . ?h aip:unitType ?hut . FILTER(STR(?hut) = "항") BIND(?h AS ?ans)')])),
    # 대지가 접해야 하는 도로: 도로 정의(항) + 도로 구조·너비 하위 조 + 접도 의무 항
    "CQ-006": sel(UNION(ans_def_of("정의", "도로"), impl_art_of_def("정의", "도로"),
                        hang_with_delegated_ho(BLD, "대지와 도로의 관계"))),
    # 동의 없이 도로 지정: 도로 지정 조의 항 중 조례로 위임된 호(동의 생략 사유)를 둔 항 + 도로 정의
    "CQ-007": sel(UNION(hang_with_delegated_ho(BLD, "도로의 지정", "조례"), ans_def_of("정의", "도로"))),
    # 도로 최소 너비: 도로 정의 + 너비를 정한 하위 조
    "CQ-008": sel(UNION(ans_def_of("정의", "너비"), impl_art_of_def("정의", "너비"))),
    # 현황도로의 도로 인정: 도로 정의 + 도로 지정(조례 위임 호)
    "CQ-009": sel(UNION(ans_def_of("정의", "도로"), hang_with_delegated_ho(BLD, "도로의 지정", "조례"))),
    # 사유지 도로에 접한 대지 건축: 도로 정의 + 접도 의무 항
    "CQ-010": sel(UNION(ans_def_of("정의", "도로"), hang_with_delegated_ho(BLD, "대지와 도로의 관계"))),
    # 사유지·국유지의 도로 인정 요건: 도로 정의 + 도로 지정(조례 위임 호)
    "CQ-011": sel(UNION(ans_def_of("정의", "도로"), hang_with_delegated_ho(BLD, "도로의 지정", "조례"))),
    # 용도·면적별 도로 폭: 도로 정의 + 너비 하위 조 + 접도 하위 조 중 조례로 다시 위임된 항(규모별 기준)
    "CQ-012": sel(UNION(ans_def_of("정의", "너비"), impl_art_of_def("정의", "너비"),
                        " ".join([art(BLDR, "대지와 도로의 관계", "r"), hang("r", "ans"), G("?ans aip:implementedBy ?rc ."),
                                  G('?rc ^aip:hasArticle/aip:lawName ?rcn . FILTER(CONTAINS(?rcn, "조례"))')]))),
    # CQ-002와 같은 뜻(도시지역 밖 동·읍 아닌 지역의 접도기준 적용 제외)
    "CQ-013": sel(" ".join([art(BLD, "대지와 도로의 관계", "x"), G("?ans aip:excludesArticle ?x .")])),
    # 통과 도로 폭 미달 → 건축선 후퇴: 건축선 지정 조의 항 + 도로 정의(소요 너비)
    "CQ-014": sel(UNION(" ".join([art(BLD, "건축선의 지정", "b"), hang("b", "ans")]), ans_def_of("정의", "도로"))),
    # 막다른 도로 폭 미달: 건축선 지정 조의 항 + 도로 너비 하위 조
    "CQ-015": sel(UNION(" ".join([art(BLD, "건축선의 지정", "b"), hang("b", "ans")]), impl_art_of_def("정의", "너비"))),
}


def query(cid):
    return PREFIX + "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n" + SPARQL[cid]


# ── 결과 정규화: 노드 라벨 → '법령명.조.항' (호·목 제외, 별표는 '법령명.별표N') ─────────────
ALIAS = [("국토의계획및이용에관한법률", "국토계획법")]
RX = re.compile(r"^(\S+)\s+(?:제(\d+)조(?:의(\d+))?(?:제(\d+)항)?|별표(\d+(?:의\d+)?))")


def norm(label):
    m = RX.match(str(label).strip())
    if not m:
        return str(label)
    law_ = m.group(1)
    for a, b in ALIAS:
        law_ = law_.replace(a, b)
    if m.group(5):
        return f"{law_}.별표{m.group(5)}"
    jo = m.group(2) + (f"의{m.group(3)}" if m.group(3) else "")
    return f"{law_}.{jo}" + (f".{m.group(4)}" if m.group(4) else "")


# ── 11-12 교체 5건 (의뢰기업 결정, 변경내역 79절) ────────────────────────────────
# 원래 002·005·013·014·015는 정답 조문이 온톨로지에 그 단위로 연결되지 않아 질의로 정답을 얻을 수 없었음 → 다른 질문으로 교체
# 정답은 조례 원문의 위임 표기("영 제28조제2항에 따라" 등)·별표 제목("(제71조제1항제16호관련)")으로 정함
YS = "음성군 건축 조례"


def upper_of(law_name, kw):
    """law_name 의 조(제목 kw)를 구체화하도록 위임한 상위 법령 조항(항)"""
    return sel(" ".join([art(law_name, kw, "c"), G("?up aip:implementedBy ?c ."),
                         G('?ans aip:hasUnit* ?up . ?ans aip:unitType ?hut . FILTER(STR(?hut) = "항")')]))


def appx_zone(zone, kw):
    """국가 법령 별표 중 제목에 구역명과 kw 가 함께 있는 별표 + 그 별표의 근거 조문(항)"""
    blk = lambda v: " ".join([G(f"?{v} a aip:Appendix ."),
                              G(f'?{v} aip:appendixTitle ?{v}_t . FILTER(CONTAINS(STR(?{v}_t), "{zone}") && CONTAINS(STR(?{v}_t), "{kw}"))'),
                              national(v, v + "n")])
    return sel(UNION(blk("ans"), " ".join([blk("p"), G("?p aip:relatedArticle ?ra ."),
                                           G('?ans aip:hasUnit* ?ra . ?ans aip:unitType ?hut . FILTER(STR(?hut) = "항")')])))


def appx_basis(law_name, kw):
    """law_name 의 별표(제목 kw)의 근거 조문"""
    return sel(" ".join([G(f'?L aip:lawName "{law_name}" . ?L aip:hasAppendix ?p . ?p aip:appendixTitle ?pt . '
                           f'FILTER(CONTAINS(REPLACE(STR(?pt), " ", ""), "{kw.replace(" ", "")}"))'),
                         G("?p aip:relatedArticle ?ans .")]))


REPLACED = {
    "CQ-002": ((T_RC, "음성군", "음성군 건축 조례에서 이해관계인의 동의 없이 도로로 지정할 수 있는 경우를 정하도록 한 상위 법령 조항은 무엇인가?",
                ["건축법.45.1"], "11-12 교체. 근거: 음성군 건축 조례 제28조 \"법 제45조제1항제2호에 따라\""),
               upper_of(YS, "도로의 지정")),
    "CQ-005": ((T_HI, "음성군", "음성군 건축 조례의 '대지와 도로의 관계' 조항은 상위 법령의 어느 조항에 따라 정한 것인가?",
                ["건축법시행령.28.2"], "11-12 교체. 근거: 음성군 건축 조례 제27조의2 \"영 제28조제2항에 따라\""),
               upper_of(YS, "대지와 도로의 관계")),
    "CQ-013": ((T_RC, "공통", "자연녹지지역 안에서 건축할 수 있는 건축물을 정한 별표와 그 근거 조항은 무엇인가?",
                ["국토계획법시행령.71.1", "국토계획법시행령.별표17"], "11-12 교체. 근거: [별표 17] 제목 \"(제71조제1항제16호관련)\""),
               appx_zone("자연녹지지역", "건축할 수 있는")),
    "CQ-014": ((T_RC, "공통", "생산관리지역 안에서 건축할 수 있는 건축물을 정한 별표와 그 근거 조항은 무엇인가?",
                ["국토계획법시행령.71.1", "국토계획법시행령.별표19"], "11-12 교체. 근거: [별표 19] 제목 \"(제71조제1항제18호관련)\""),
               appx_zone("생산관리지역", "건축할 수 있는")),
    "CQ-015": ((T_RC, "음성군", "음성군 건축 조례의 대지안의 공지 기준 별표는 어느 조문에 근거하는가?",
                ["음성군건축조례.30"], "11-12 교체. 근거: [별표 3] 제목 \"(제30조 관련)\""),
               appx_basis(YS, "대지안의 공지")),
}
CQS = [(cid, *REPLACED[cid][0]) if cid in REPLACED else row for row in CQS for cid in [row[0]]]
SPARQL.update({cid: q for cid, (_, q) in REPLACED.items()})


if __name__ == "__main__":                                  # 정답셋 CSV + 질의 파일(.rq) 저장
    import csv, os
    out = "spec_data/da2/recv"
    os.makedirs(os.path.join(out, "sparql"), exist_ok=True)
    with open(os.path.join(out, "da2_cq_answer_recv.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["CQ ID", "CQ", "ODP 유형", "지자체", "정답(expected_bindings)", "SPARQL 파일", "비고"])
        for cid, typ, area, cq, gold, note in CQS:
            fn = f"{cid}.rq"
            with open(os.path.join(out, "sparql", fn), "w", encoding="utf-8") as g:
                g.write(f"# {cid} {cq.splitlines()[0]}\n# 결과 ?ans 노드의 라벨을 '법령명.조.항'(호 제외)으로 정규화해 정답과 집합 비교\n" + query(cid) + "\n")
            w.writerow([cid, cq, typ, area, ";".join(gold), f"sparql/{fn}", note])
    print("저장", out, len(CQS))
