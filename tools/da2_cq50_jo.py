# [DA 2-1] CQ 50건 — 1안: 조 단위 질문·정답 (2026-11-12 사용자 결정, 변경내역 88절)
# 배경: 온톨로지의 위임·예외 연결(implementedBy 등)이 조 단위로 붙어 있고 항 노드에는 본문·제목이 없음
#       → 질문 개념만으로는 정답 항만 골라낼 수 없음(같은 조의 다른 항이 함께 나옴, 84·87절)
# 1안: 질문이 묻는 단위를 '조'로 맞추고(질문 문구는 그대로, 11-12 사용자: [조 단위] 표시 안 함), 정답은 기존 정답(작성자 답변·법령 원문 근거)을 조로 올림.
#      정답은 시스템 결과를 옮기지 않음. 별표는 그대로 '법령명.별표N'.
# 질의: 기존 질의(tools/da2_cq50.py) 결과 노드를 그 노드가 속한 조로 올림(조·별표는 그대로). 정답값·LIMIT 없음.
# 따로 고친 것(질문 개념으로 범위를 좁힘, 정답 조문 번호는 질의에 넣지 않음):
#   CQ-005·021  위임 상위 조문을 질문 개념('가설건축물'·'건폐율')이 제목에 있는 조로 한정
#   CQ-021      질문을 '용도지역(계획관리지역) 안에서의 건폐율' 규정으로 명확히 (조례에 건폐율 조가 6개)
#   CQ-022      조 제목이 질문의 '부설주차장 설치'와 같은 조 (제목에 '부설주차장'이 든 조가 4개)
#   CQ-036      QA 질의 오류 수정: '정의' 조를 제목 포함으로 찾아 '건축협정의 체결'·'조정의 효력'까지 걸림 → 제목이 '정의'인 조
#   CQ-036      결합건축 대상지 조를 구체화한 조는 국가 법령만(질문 지자체 '공통'; 조례 위임 항 제외)
#   CQ-040      개발제한구역 시행령 별표 중 제목에 질문의 '건축'이 있는 별표(별표1)만, 그 별표로 위임한 조는 개발제한구역을 제한하는 조만
#   CQ-030      온톨로지에 인천광역시 건축 조례 제18조가 없음 → 질문을 연장 횟수를 정한 별표로 좁힘(정답 별표5)
#   CQ-015      빠짐(53건본 반영, 아래 NEW·DROP)
import sys
sys.path.insert(0, ".")
from tools import da2_cq50 as B
from tools.da2_cq_recv import G, sel, UNION, art

jo = lambda k: ".".join(k.split(".")[:2])


def art_eq(law_name, title, var):
    """법령 law_name 에서 조 제목(공백 무시)이 title 과 같은 조"""
    return G(f'?L_{var} aip:lawName "{law_name}" . ?L_{var} aip:hasArticle ?{var} . ?{var} aip:articleTitle ?t_{var} . '
             f'FILTER(REPLACE(STR(?t_{var}), " ", "") = "{title.replace(" ", "")}")')


def title_has(v, kw):
    return G(f'?{v} aip:articleTitle ?tt_{v} . FILTER(CONTAINS(REPLACE(STR(?tt_{v}), " ", ""), "{kw.replace(" ", "")}"))')


def chain_titled(start, up_kw):
    """조례 조(start 패턴, 변수 ?c)를 구체화하도록 위임한 상위 조 + 그 상위 조를 다시 위임한 조. 상위 조는 제목에 up_kw"""
    hop1 = " ".join([start, G("?c aip:hasUnit* ?ct . ?up aip:implementedBy ?ct . ?a1 aip:hasUnit* ?up . ?a1 a aip:Article ."), title_has("a1", up_kw)])
    return sel(UNION(hop1.replace("?a1", "?ans").replace("?tt_a1", "?tt_ans"),
                     " ".join([hop1, G("?a1 aip:hasUnit* ?rt . ?x aip:implementedBy ?rt . ?ans aip:hasUnit* ?x . ?ans a aip:Article ."),
                               title_has("ans", up_kw)])))


def to_jo(q):
    """결과 노드(?ans)를 그 노드가 속한 조로 올림. 조·별표는 그대로"""
    return (f"SELECT DISTINCT ?jo WHERE {{ {{ {q} }} OPTIONAL {{ ?a_jo aip:hasUnit+ ?ans . ?a_jo a aip:Article . }} "
            f"BIND(COALESCE(?a_jo, ?ans) AS ?jo) }}")


Q = {r[0]: r[3] for r in B.ROWS}
Q["CQ-021"] = "「경산시 도시계획 조례」에서 용도지역(계획관리지역) 안에서의 건폐율을 정한 규정의 위임 근거가 되는 상위 법령(조문)은 무엇인가?"
Q["CQ-030"] = "인천광역시의 대지에 컨테이너 임시창고(가설건축물)를 설치하는 경우 존치기간 연장 횟수를 인천광역시 건축 조례에서 정한 기준(별표)은 무엇인가?"

GOLD = {r[0]: sorted({jo(x) for x in r[4]}) for r in B.ROWS}
GOLD["CQ-030"] = ["인천광역시건축조례.별표5"]

NOTE = {r[0]: r[5] for r in B.ROWS}
NOTE["CQ-021"] += " / 1안: 질문을 '용도지역 안에서의 건폐율' 규정으로 명확히"
NOTE["CQ-030"] = ("1안: 온톨로지에 인천광역시 건축 조례 제18조가 없어 질문을 별표로 좁힘. "
                  "[별표 5] '가설건축물의 종류별 존치기간 연장 횟수(제18조제5항 관련)'")

SPARQL = {cid: to_jo(q) for cid, q in B.SPARQL.items()}
SPARQL["CQ-005"] = to_jo(chain_titled(art(B.YS, "가설건축물", "c"), "가설건축물"))
SPARQL["CQ-021"] = to_jo(chain_titled(art_eq(B.GSP, "용도지역 안에서의 건폐율", "c"), "건폐율"))
SPARQL["CQ-022"] = to_jo(sel(art_eq("음성군 주차장 조례", "부설주차장 설치", "ans")))
SPARQL["CQ-036"] = to_jo(sel(UNION(
    " ".join([art(B.BLD, "결합건축 대상지", "ans")]),
    " ".join([art(B.BLD, "결합건축 대상지", "x"), G("?x aip:hasUnit+ ?h . ?h aip:implementedBy ?ans . ?ans a aip:Article ."),
              B.national("ans", "n")]),                                       # 질문이 '공통' → 국가 법령만
    art_eq(B.BLD, "정의", "ans"))))
SPARQL["CQ-040"] = to_jo(sel(UNION(
    " ".join([B.Z("개발제한구역", "z"), G("?z aip:restrictedBy ?ans . ?ans a aip:Appendix ."), B.law_has("ans", B.GBR, "L"),
              G('?ans aip:appendixTitle ?at . FILTER(CONTAINS(STR(?at), "건축"))')]),
    " ".join([B.Z("개발제한구역", "z"), G("?z aip:restrictedBy ?p . ?p a aip:Appendix ."), B.law_has("p", B.GBR, "L"),
              G('?p aip:appendixTitle ?pt . FILTER(CONTAINS(STR(?pt), "건축"))'),
              G("?x aip:implementedBy ?p . ?ans aip:hasUnit* ?x . ?ans a aip:Article . ?z aip:restrictedBy ?ans .")]),
    " ".join([B.Z("개발제한구역", "z"), G("?z aip:restrictedBy ?p . ?p a aip:Appendix ."), B.law_has("p", B.GBR, "L"),
              G('?p aip:appendixTitle ?pt . FILTER(CONTAINS(STR(?pt), "건축"))'), G("?p aip:relatedArticle ?ans .")]),
    art("인천광역시 계양구 개발제한구역 내 건축행위 등 허가에 관한 조례", "자격", "ans"))))

# ── 작성자 53건본(11-12 22시, spec_data/da2/DA2-1_CQ_질문답변작성_53건_20261112.xlsx) 반영 (변경내역 89절) ──
# 새 문항 4건(013·038·052·053), 중복 CQ-016-2 삭제. 50건으로 맞추려고 사용자 지시로 3건 뺌:
#   CQ-015(미수정·훈령이 온톨로지에 없음, 053이 같은 위계 추적 유형으로 대체), CQ-018(CQ-001과 정답 같음), CQ-041(053과 같은 건축법 제11조제4항)
NEW = [
    ("CQ-013", B.T_RC, "공통", "건축물이 건축선의 수직면을 넘어서는 안되는 근거조문은?", ["건축법.47"],
     "답변: 건축법 제47조제1항·제2항(건축선에 따른 건축제한)"),
    ("CQ-038", B.T_EX, "공통", "두 토지 간 건축협정 체결 시 두 토지를 1인이 소유해도 협정 체결이 가능한 단서 조건은?", ["건축법.77의4"],
     "답변: 건축법 제77조의4제2항(1인 소유 시 건축협정). 제1항 각 호는 가능 지역(같은 조)"),
    ("CQ-052", B.T_CF, "영천시", "영천시 계획관리지역에서 성장관리계획을 수립한 지역의 용적률을 영천시 도시계획 조례와 국토의 계획 및 이용에 관한 법률과 "
     "다르게 정하고 있는 경우 어느 조문을 따라야 하는가?", ["영천시도시계획조례.62"],
     "답변 결론: 영천시 도시계획 조례 제62조제1항제19호(성장관리계획 수립 지역 125% 이하). 법 제78조·제75조의3, 영 제85조는 위임 근거라 뺌. "
     "질문 칸의 괄호 속 다른 표현(법 제78조제1항 명시)은 쓰지 않음"),
    ("CQ-053", B.T_HI, "경산시", "상습 침수 우려지역인 방재지구에서 건축위원회의 심의를 거쳐 건축허가를 하지 않을 수 있는 근거가 되는 상위 법령은?",
     ["건축법.11"], "답변 결론: 건축법 제11조제4항제2호. 영 제9조의2(상습 침수 우려지역)는 질문의 출발점이라 뺌"),
]
DROP = {"CQ-015", "CQ-016-2", "CQ-018", "CQ-041"}
SPARQL["CQ-013"] = to_jo(sel(" ".join([art(B.BLD, "건축선", "ans"), title_has("ans", "제한")])))
SPARQL["CQ-038"] = to_jo(sel(art(B.BLD, "건축협정의 체결", "ans")))
SPARQL["CQ-052"] = to_jo(sel(" ".join([B.Z("계획관리지역", "z"), G("?z aip:restrictedBy ?ans ."), B.law_has("ans", "영천시", "L"),
                                       title_has("ans", "용적률"), G("?nu aip:implementedBy ?ans .")])))
SPARQL["CQ-053"] = to_jo(sel(" ".join([art(B.BLDR, "상습 침수 우려지역", "c"),
                                       G("?x aip:implementedBy ?c . ?ans aip:hasUnit* ?x . ?ans a aip:Article ."), B.national("ans", "n")])))

_ALL = sorted([(cid, typ, area, Q[cid], GOLD[cid], NOTE[cid]) for cid, typ, area, _, _, _ in B.ROWS if cid not in DROP]
              + [(cid, typ, area, q, gold, note) for cid, typ, area, q, gold, note in NEW])
# 번호 다시 매김(사용자, 11-12): CQ-001 ~ CQ-050. 작성자 파일 번호는 근거 칸 앞에 '[원번호 CQ-0xx]'로 남김
ORIG = {f"CQ-{i:03d}": r[0] for i, r in enumerate(_ALL, 1)}
CQS = [(f"CQ-{i:03d}", typ, area, q, gold, f"[원번호 {cid}] {note}") for i, (cid, typ, area, q, gold, note) in enumerate(_ALL, 1)]


def query(cid):
    """새 번호(CQ-001~050) → 원번호의 질의"""
    return B.PREFIX + "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n" + SPARQL[ORIG[cid]]
