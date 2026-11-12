# [DA 2-1] CQ 50건 — 혼합 기준(2026-11-12 사용자 결정, 변경내역 91절)
# 1안(전부 조 단위, 88~90절)은 같은 조의 어느 항인지 가리지 못해 변별력이 없음 → 혼합 기준:
#   정답 항(또는 그 하위 호·목)에 온톨로지의 고유 관계(위임·예외·제한 등)가 있으면 항 단위, 없으면 조 단위.
#   목록 = spec_data/da2/da2_cq_hang_rel.json (tools/da2_cq_hangrel.py 가 동결 온톨로지에서 뽑음). 정답·결과에 똑같이 적용.
# 정답: 작성자 원 정답(항 단위, 법령 원문 근거, 84·89절)에 위 기준을 적용. 시스템 결과를 옮기지 않음.
# 질의: 질문 개념으로 출발해 관계가 닿는 가장 작은 단위(항·조·별표)를 냄. 결과의 '고유 관계 없는 항'은 채점 때 조로 올림
#   (tools/da2_cq.py run: 시험셋 zip 의 hang_rel.json 으로 정답과 같은 규칙. SPARQL 로 하면(to_mix) 너무 느려 파이썬으로)
# 교체 10건(사용자 지시, 8건 + 혼합 기준 시험에서 같은 경우로 확인된 005·036): 같은 조의 여러 항에 모두 관계가 있어 온톨로지로 정답 항을 고를 수 없던 문항
#   → 온톨로지가 항까지 구분하는 다른 질문으로 바꿈(정답은 법령 원문으로 확인). 아래 REPL.
import sys
sys.path.insert(0, ".")
from tools import da2_cq50 as B, da2_cq50_jo as J
from tools.da2_cq_recv import G, sel, UNION, art, hang
from tools.da2_cq50_jo import art_eq, title_has
from tools.da2_cq_hangrel import STRUCT, load as _load_rel

REL = _load_rel()
jo = lambda k: ".".join(k.split(".")[:2])


def mix(k):
    """비교 키 1개에 혼합 기준 적용: 고유 관계 없는 항 → 조"""
    return jo(k) if (k.count(".") == 2 and k not in REL) else k


def to_mix(q):
    """질의 결과(?ans)가 고유 관계 없는 항(또는 그 하위 호·목)이면 그 조로 올림. 조·별표·관계 있는 항은 그대로"""
    return (f"SELECT DISTINCT ?r WHERE {{ {{ {q} }} OPTIONAL {{ ?h_m aip:hasUnit* ?ans . ?h_m aip:unitType ?ut_m . "
            f"FILTER(STR(?ut_m) = \"항\") ?a_m aip:hasUnit ?h_m . ?a_m a aip:Article . "
            f"FILTER NOT EXISTS {{ ?h_m aip:hasUnit* ?d_m . {{ ?d_m ?p_m ?x_m . FILTER(isIRI(?x_m)) }} UNION {{ ?x_m ?p_m ?d_m . }} "
            f"FILTER(?p_m NOT IN ({STRUCT})) }} }} BIND(COALESCE(?a_m, ?ans) AS ?r) }}")


def hang_of(v, out="ans"):
    """노드 v 가 속한 항(v 자신이 항이면 v)"""
    return G(f"?{out} aip:hasUnit* ?{v} .") + " " + B.is_hang(out)


def chain_hang(start, kw, up2_jo=False):
    """조례 조(start, ?c)를 구체화하도록 위임한 상위 항(그 조 제목에 kw) + 그 상위 조를 다시 위임한 항(조 제목에 kw)"""
    hop1 = " ".join([start, G("?c aip:hasUnit* ?ct . ?up aip:implementedBy ?ct . ?a1 aip:hasUnit+ ?up . ?a1 a aip:Article ."), title_has("a1", kw)])
    return sel(UNION(" ".join([hop1, hang_of("up")]),
                     " ".join([hop1, G("?a1 aip:hasUnit* ?rt . ?x aip:implementedBy ?rt . ?a2 aip:hasUnit+ ?x . ?a2 a aip:Article ."),
                               title_has("a2", kw), (G("?a2 aip:hasUnit* ?ans . FILTER(?ans = ?a2)") if up2_jo else hang_of("x"))])))


ROWS = {r[0]: r for r in B.ROWS}
NEWW = {r[0]: r for r in J.NEW}
# 원번호 기준 정답(항 단위, 법령 원문). 새 4건은 작성자 답변의 항으로(89절의 조 단위 정답 대신)
SRC = {o: list(r[4]) for o, r in ROWS.items()}
SRC.update({"CQ-013": ["건축법.47.1", "건축법.47.2"], "CQ-038": ["건축법.77의4.2"],
            "CQ-052": ["영천시도시계획조례.62.1"], "CQ-053": ["건축법.11.4"], "CQ-030": ["인천광역시건축조례.별표5"]})
QTEXT = {o: J.Q.get(o, r[3]) for o, r in ROWS.items()}
QTEXT.update({o: r[3] for o, r in NEWW.items()})

# ── 교체 8건(원번호 → 새 문항). 정답은 법령 원문(법령 수집·적재 관리자)으로 확인 ──
REPL = {
    "CQ-010": (B.T_EX, "공통", "「산업입지 및 개발에 관한 법률」에 따른 농공단지 안에서 건축물의 용도·종류 및 규모 등의 제한에 관하여 "
               "「국토의 계획 및 이용에 관한 법률」의 용도지역 건축제한 대신 다른 법률을 따르도록 한 예외 조항은 무엇인가?",
               ["국토계획법.76.5"], "국토계획법 제76조제5항 제2호(농공단지는 산업입지법에 따름). 제1항~제4항은 원칙이라 뺌"),
    "CQ-027": (B.T_EX, "공통", "국립공원 등 자연공원에 대하여 다른 법률의 규정을 적용하지 아니하도록 정한 「자연공원법」의 예외 조항은 무엇인가?",
               ["자연공원법.70.1"], "자연공원법 제70조제1항(국토계획법 제76조제1항, 도로법 접도구역 규정 적용 배제)"),
    "CQ-020": (B.T_HI, "공통", "「국토의 계획 및 이용에 관한 법률 시행령」 제51조(개발행위허가의 대상)의 위임 근거가 되는 상위 법률 조항은 무엇인가?",
               ["국토계획법.56.1"], "국토계획법 제56조제1항('대통령령으로 정하는 행위'). 제2항·제4항은 다른 시행령 조(변경·경미한 행위)로 위임"),
    "CQ-051": (B.T_HI, "공통", "「국토의 계획 및 이용에 관한 법률 시행령」 제30조(용도지역의 세분)의 위임 근거가 되는 상위 법률 조항은 무엇인가?",
               ["국토계획법.36.2"], "국토계획법 제36조제2항('대통령령으로 정하는 바에 따라 … 다시 세분'). 제1항은 용도지역 지정"),
    "CQ-028": (B.T_CF, "인천 계양구", "인천광역시 복합개발진흥지구 안에서 건축할 수 있는 건축물을 「국토의 계획 및 이용에 관한 법률 시행령」과 "
               "「인천광역시 도시계획 조례」가 다르게 정한 경우 어느 조항을 따르는가?",
               ["인천광역시도시계획조례.61.1"], "인천광역시 도시계획 조례 제61조제1항(영 제79조제1항 위임). 제2항은 산업·유통개발진흥지구의 공장"),
    "CQ-037": (B.T_RC, "공통", "임업용산지에서 산지전용 또는 산지일시사용을 할 수 있는 행위를 제한하는 근거 조항은 무엇인가?",
               ["산지관리법.12.1"], "산지관리법 제12조제1항(임업용산지). 제2항·제3항은 공익용산지"),
    "CQ-045": (B.T_RC, "경산시", "경산시 제1종전용주거지역에서 건축물을 건축할 때 정북방향 인접대지경계선으로부터 띄어야 하는 거리를 정한 조항은 무엇인가?",
               ["경산시건축조례.27.1"], "경산시 건축 조례 제27조제1항(정북방향 이격거리). 제2항은 인동간격, 제3항은 부속건축물 예외"),
    "CQ-005": (B.T_HI, "공통", "「건축법 시행령」 제18조(설계도서의 작성)의 위임 근거가 되는 상위 법률 조항은 무엇인가?",
               ["건축법.23.1"], "건축법 제23조제1항 제3호('대통령령으로 정하는 건축물의 건축등'). 영 제18조 본문이 '법 제23조제1항제3호'를 명시"),
    "CQ-036": (B.T_RC, "아산시", "아산시 제1종전용주거지역에서 건축선 및 인접대지경계선으로부터 건축물의 각 부분까지 띄어야 하는 거리를 정한 조항은 무엇인가?",
               ["아산시건축조례.37.1", "아산시건축조례.별표4"], "아산시 건축 조례 제37조제1항('별표 4와 같다')과 [별표 4]. 제2항은 적용 제외, 제3항·제4항은 전통시장"),
    "CQ-046": (B.T_RC, "경산시", "경산시 일반주거지역에서 방재지구의 재해저감대책에 맞게 재해예방시설을 설치하는 건축물의 용적률 기준을 정한 조항은 무엇인가?",
               ["경산시도시계획조례.84.3"], "경산시 도시계획 조례 제84조제3항(해당 용도지역 용적률의 120% 이하). 제1항은 용도지역별 용적률"),
}

LP, LPR = B.LP, B.LPR
RAW = dict(B.SPARQL)
RAW.update({
    # 교체 8건
    "CQ-010": sel(" ".join([B.Z("농공단지", "z"), G("?e aip:inZone ?z ; aip:exemptsArticle ?u ."), hang_of("u")])),
    "CQ-027": sel(" ".join([B.Z("국립공원", "z"), G("?e aip:inZone ?z ; aip:exemptsArticle ?u ."), hang_of("u"),
                            G("?ans_a aip:hasUnit ?ans ."), B.law_has("ans_a", "자연공원법", "L")])),
    "CQ-020": sel(" ".join([art(LPR, "개발행위허가의 대상", "c"), G("?x aip:implementedBy ?c ."), hang_of("x"),
                            G("?ans_a aip:hasUnit ?ans ."), B.law_has("ans_a", LP, "L")])),
    "CQ-051": sel(" ".join([art(LPR, "용도지역의 세분", "c"), G("?x aip:implementedBy ?c ."), hang_of("x"),
                            G("?ans_a aip:hasUnit ?ans ."), B.law_has("ans_a", LP, "L")])),
    "CQ-028": sel(" ".join([B.Z("복합개발진흥지구", "z"), G("?z aip:restrictedBy ?ans ."), G("?ans_a aip:hasUnit* ?ans ."),
                            B.law_has("ans_a", "인천광역시", "L"), title_has("ans_a", "건축제한")])),
    "CQ-037": sel(" ".join([B.Z("임업용산지", "z"), G("?z aip:restrictedBy ?ans ."), B.is_hang("ans")])),
    "CQ-045": sel(" ".join([B.Z("제1종전용주거지역", "z"), G("?z aip:restrictedBy ?ans ."), G("?ans_a aip:hasUnit ?ans ."),
                            B.law_has("ans_a", "경산시", "L"), title_has("ans_a", "일조")])),
    "CQ-046": sel(" ".join([B.Z("일반주거지역", "z"), G("?z aip:restrictedBy ?ans ."), G("?ans_a aip:hasUnit ?ans ."),
                            B.law_has("ans_a", "경산시", "L"), title_has("ans_a", "용적률")])),
    "CQ-005": sel(" ".join([art(B.BLDR, "설계도서의 작성", "c"), G("?x aip:implementedBy ?c ."), hang_of("x"),
                            G("?ans_a aip:hasUnit ?ans ."), B.law_has("ans_a", B.BLD, "L")])),
    "CQ-036": sel(UNION(" ".join([B.Z("제1종전용주거지역", "z"), G("?z aip:restrictedBy ?ans ."), G("?ans_a aip:hasUnit ?ans ."),
                                  B.law_has("ans_a", "아산시", "L"), title_has("ans_a", "공지")]),
                        " ".join([B.Z("제1종전용주거지역", "z"), G("?z aip:restrictedBy ?h ."), G("?ha aip:hasUnit ?h ."),
                                  B.law_has("ha", "아산시", "L"), title_has("ha", "공지"),
                                  G("?ans aip:relatedArticle ?ha . ?ans a aip:Appendix .")]))),
    # ① 정답 조가 조로 올라간 문항: 질의가 그 조를 항으로 펼치지 않고 조를 냄(관계가 조에 닿음)
    "CQ-001": sel(UNION(B.hang_with_delegated_ho(B.BLD, "대지와 도로의 관계"),
                        " ".join([art(B.BLD, "대지와 도로의 관계", "x"), G("?x aip:hasUnit/aip:hasUnit ?xh . ?xh aip:implementedBy ?ans . ?ans a aip:Article .")]))),
    "CQ-026": sel(art("하수도법 시행령", "개인하수처리시설의 설치", "ans")),
    "CQ-029": sel(art(B.GS, "일조", "ans")),
    "CQ-034": sel(" ".join([art(B.BLDR, "면적 등의 산정방법", "x"), hang("x", "ans"), G("?ans aip:hasUnit* ?d . ?d aip:implementedBy ?y .")])),
    "CQ-035": sel(UNION(" ".join([B.Z("농업진흥구역", "z"), G("?z aip:restrictedBy ?ans ."), B.is_hang("ans")]),
                        art("농지법 시행령", "농업진흥구역에서 할 수 있는 행위", "ans"))),
    "CQ-038": sel(art(B.BLD, "건축협정의 체결", "ans")),
    "CQ-040": sel(UNION(
        " ".join([B.Z("개발제한구역", "z"), G("?z aip:restrictedBy ?ans . ?ans a aip:Appendix ."), B.law_has("ans", B.GBR, "L"),
                  G('?ans aip:appendixTitle ?at . FILTER(CONTAINS(STR(?at), "건축"))')]),
        " ".join([B.Z("개발제한구역", "z"), G("?z aip:restrictedBy ?p . ?p a aip:Appendix ."), B.law_has("p", B.GBR, "L"),
                  G('?p aip:appendixTitle ?pt . FILTER(CONTAINS(STR(?pt), "건축"))'),
                  G("?x aip:implementedBy ?p . ?xa aip:hasUnit+ ?x . ?xa a aip:Article . ?z aip:restrictedBy ?xa ."), hang_of("x")]),
        " ".join([B.Z("개발제한구역", "z"), G("?z aip:restrictedBy ?p . ?p a aip:Appendix ."), B.law_has("p", B.GBR, "L"),
                  G('?p aip:appendixTitle ?pt . FILTER(CONTAINS(STR(?pt), "건축"))'), G("?p aip:relatedArticle ?ans .")]),
        art("인천광역시 계양구 개발제한구역 내 건축행위 등 허가에 관한 조례", "자격", "ans"))),
    # 따로 만든 질의(88·89절)를 항까지 내도록
    "CQ-021": chain_hang(art_eq(B.GSP, "용도지역 안에서의 건폐율", "c"), "건폐율", up2_jo=True),
    "CQ-022": sel(" ".join([art_eq("음성군 주차장 조례", "부설주차장 설치", "p"), hang("p", "ans")])),
    "CQ-030": None,                                                        # 1안 질의 그대로(별표)
    "CQ-013": sel(" ".join([art(B.BLD, "건축선", "a"), title_has("a", "제한"), hang("a", "ans")])),
    "CQ-052": sel(" ".join([B.Z("계획관리지역", "z"), G("?z aip:restrictedBy ?u ."), hang_of("u"), G("?a aip:hasUnit ?ans ."),
                            B.law_has("a", "영천시", "L"), title_has("a", "용적률"), G("?nu aip:implementedBy ?a .")])),
    "CQ-053": sel(" ".join([art(B.BLDR, "상습 침수 우려지역", "c"), G("?x aip:implementedBy ?c ."), hang_of("x"), B.national("ans_a", "n"),
                            G("?ans_a aip:hasUnit ?ans .")])),
})

_ORDER = [J.ORIG[c] for c, *_ in J.CQS]                                      # 89절 50건 순서(원번호)
CQS, ORIG, SPARQL = [], {}, {}
for i, o in enumerate(_ORDER, 1):
    cid = f"CQ-{i:03d}"
    ORIG[cid] = o
    if o in REPL:
        typ, area, q, gold, note = REPL[o]
        note = f"[원번호 {o} 교체] " + note
    else:
        r = ROWS.get(o) or NEWW[o]
        typ, area, q, gold, note = r[1], r[2], QTEXT[o], SRC[o], f"[원번호 {o}] " + (J.NOTE.get(o) or r[5])
    CQS.append((cid, typ, area, q, sorted({mix(x) for x in gold}), note))
    SPARQL[o] = J.SPARQL[o] if RAW.get(o) is None else RAW[o]          # 결과의 항→조 올림은 채점 때 파이썬으로(da2_cq.run, hang_rel.json)


def query(cid):
    return B.PREFIX + "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n" + SPARQL[ORIG[cid]]
