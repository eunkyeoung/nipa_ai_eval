# [DA 2-1] CQ 50건 — 혼합 기준(2026-11-12 사용자 결정, 변경내역 91절)
# 1안(전부 조 단위, 88~90절)은 같은 조의 어느 항인지 가리지 못해 변별력이 없음 → 혼합 기준:
#   정답 항(또는 그 하위 호·목)에 온톨로지의 고유 관계(위임·예외·제한 등)가 있으면 항 단위, 없으면 조 단위.
#   목록 = spec_data/da2/da2_cq_hang_rel.json (tools/da2_cq_hangrel.py 가 동결 온톨로지에서 뽑음). 정답·결과에 똑같이 적용.
# 정답: 작성자 원 정답(항 단위, 법령 원문 근거, 84·89절)에 위 기준을 적용. 시스템 결과를 옮기지 않음.
# 질의: 질문 개념으로 출발해 관계가 닿는 가장 작은 단위(항·조·별표)를 냄. 결과의 '고유 관계 없는 항'은 채점 때 조로 올림
#   (tools/da2_cq.py run: 시험셋 zip 의 hang_rel.json 으로 정답과 같은 규칙. SPARQL 로 하면(to_mix) 너무 느려 파이썬으로)
# 교체 10건(사용자 지시, 8건 + 혼합 기준 시험에서 같은 경우로 확인된 005·036) → 11-14 005·010 은 원래 질문으로 복원(아래 RESTORED, 변경내역 110절): 같은 조의 여러 항에 모두 관계가 있어 온톨로지로 정답 항을 고를 수 없던 문항
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
    # 11-16(변경내역 114절): 불일치 4건을 원래 질문과 같은 주제의 새 질문으로(사용자). 정답은 시행령·조례 원문의 인용으로 확인
    # 11-17(122절): 작성자 검토 의견으로 036·037·046 질문 문구 수정(051은 작성자 ok, 그대로). 질의·정답 키는 그대로
    "CQ-036": (B.T_RC, "아산시", "아산시에서 시장이 결합건축을 할 수 없다고 인정하는 지역의 근거 조문은?",
               ["건축법.77의15.3", "아산시건축조례.39의3"],
               "아산시 건축 조례 제39조의3('법 제77조의15제3항에 따라 결합건축을 할 수 없는 지역은 … 시장이 인정하는 지역'). "
               "건축법 제77조의15 제3항(조례로 정하는 지역 안에서는 결합건축 불가). 원래 질문(2개 대지 간 거리)과 같은 결합건축 조"),
    "CQ-037": (B.T_RC, "음성군", "음성군에서 군수가 건축협정을 체결할 수 있다고 인정하여 공고한 구역의 근거 조문은?",
               ["건축법.77의4.1", "음성군건축조례.35의2"],
               "음성군 건축 조례 제35조의2('법 제77조의4제1항제5호에 따라 군수가 조례로 정하는 구역은 …'). "
               "건축법 제77조의4 제1항 제5호(작성자 정답 '제1항제5호', 11-17 — 채점은 항까지 비교하므로 '제1항'과 같음). 원래 질문(건축협정 체결 가능 지역)과 같은 조"),
    "CQ-046": (B.T_RC, "공통", "도로점용허가를 받아 도로를 점용할 수 있는 공작물·물건, 그 밖의 시설의 종류를 정한 근거 조문은 무엇인가?",
               ["도로법.61.3", "도로법시행령.55"],
               "도로법 시행령 제55조('법 제61조제3항에 따라 도로점용허가를 받아 도로를 점용할 수 있는 공작물·물건, 그 밖의 시설의 종류는 …'). "
               "원래 질문(도로 점용 허가)과 같은 도로법 제61조"),
    "CQ-051": (B.T_HI, "공통", "접도구역에서 예외적으로 허용되는 행위를 정한 시행령 조항과 그 최상위 근거가 되는 도로법 조항은 무엇인가?",
               ["도로법.40.3", "도로법시행령.39.3"],
               "도로법 시행령 제39조 제3항('법 제40조제3항 각 호 외의 부분 단서에서 \"대통령령으로 정하는 행위\"란 …'). "
               "원래 질문(고속국도 접도구역 이격거리의 최상위 근거)과 같은 도로법 제40조"),
}

# 원래 질문 복원(11-14): 교체(91절) 대신 작성자 원 질문·원 정답(항 단위, 법령 원문). QA 보완 패치로 온톨로지가 항까지 구분
RESTORED = {"CQ-005": "법 제20조 제3항, 영 제15조 제5항(제16호) — 원문 영 제15조 ⑤ '법 제20조제3항에서 …'. QA 보완 패치(위임 항 단위)로 판정",
            "CQ-010": "법 제16조 제1항 단서, 영 제12조 제2항(경미한 변경) — 원문 영 제12조 ② '법 제16조제1항 단서에서 …'. QA 보완 패치(단서 위임)로 판정",
            # 11-16(변경내역 114절, 사용자): 원래 질문으로 복원
            "CQ-020": "영천시 건축 조례 제22조 ② '영 제15조제5항제16호' → 영 제15조 ⑤ '법 제20조제3항에서' — 보완 패치(조례 '같은 법 시행령(이하 \"영\")' 약칭 인식)로 항 단위 연결",
            "CQ-027": "국토계획법 제84조 제1항·제3항이 모두 시행령 제94조로 위임('가중평균'은 항 본문에만 있음) — 불일치 예상, 의뢰기업 요청대로 원래 질문 유지",
            "CQ-028": "국토계획법 제75조의3 제2항 — 위임받는 조례 조 제목 '건폐율'(제1항·제3항은 '용적률')로 구분",
            "CQ-045": "건축법 제60조 제2항 → 인천광역시 건축 조례 제30조 제1항(보완 패치 항 단위 위임), 조례 제30조 제1항은 전용주거지역이 제한받는 항"}

LP, LPR = B.LP, B.LPR
RAW = dict(B.SPARQL)
RAW.update({
    # 원래 질문 복원(11-16, 변경내역 114절)
    # 020: 질문이 가리킨 「영천시 건축 조례」 제22조제2항 → 그 항을 구체화하도록 위임한 시행령 항 → 그 시행령 항을 위임한 법률 항
    "CQ-020": sel(UNION(
        " ".join([G('?h rdfs:label "영천시건축조례 제22조제2항" . ?h aip:hasUnit* ?ht . ?s aip:implementedBy ?ht .'), hang_of("s", "ans")]),
        " ".join([G('?h rdfs:label "영천시건축조례 제22조제2항" . ?h aip:hasUnit* ?ht . ?s aip:implementedBy ?ht .'), hang_of("s", "sh"),
                  G("?up aip:implementedBy ?sh ."), hang_of("up")]))),
    "CQ-027": None,                                                        # 원래 질의 그대로(불일치 예상)
    # 028: 원래 질의 + 질문의 '건폐율'(위임받는 조례 조의 제목)
    "CQ-028": B.SPARQL["CQ-028"].rstrip()[:-1] + " " + title_has("c", "건폐율") + " }",
    # 045: 인천 건축 조례 '가로구역별' 조 중 전용주거지역이 제한받는 항 + 그 항을 (항 단위로) 위임한 법률 항
    "CQ-045": sel(UNION(
        " ".join([art("인천광역시 건축 조례", "가로구역별", "c"), hang("c", "ans"), G('?ans aip:hasUnit ?ho . ?z a aip:Zone . ?z rdfs:label "전용주거지역" . ?zz skos:broader* ?z . ?zz aip:restrictedBy ?ho .')]),
        " ".join([art("인천광역시 건축 조례", "가로구역별", "c"), hang("c", "h"), G('?h aip:hasUnit ?ho . ?z a aip:Zone . ?z rdfs:label "전용주거지역" . ?zz skos:broader* ?z . ?zz aip:restrictedBy ?ho .'),
                  G("?up aip:implementedBy ?h ."), hang_of("up")]))),
    # 원래 질문과 같은 주제의 새 질문 4건(11-16)
    # 036: 아산 조례 '결합건축' 조 + 그 조를 위임한 건축법 항
    "CQ-036": sel(UNION(art("아산시 건축 조례", "결합건축", "ans"),
                        " ".join([art("아산시 건축 조례", "결합건축", "c"), G("?up aip:implementedBy ?c ."), hang_of("up")]))),
    # 037: 음성 조례 '건축협정' 조 + 그 조를 위임한 건축법 항
    "CQ-037": sel(UNION(art("음성군 건축 조례", "건축협정", "ans"),
                        " ".join([art("음성군 건축 조례", "건축협정", "c"), G("?up aip:implementedBy ?c ."), hang_of("up")]))),
    # 046: 도로법 '점용 허가' 조의 항 중 시행령 '공작물' 조로 위임한 항 + 그 시행령 조
    "CQ-046": sel(UNION(" ".join([art("도로법", "점용허가", "a"), hang("a", "ans"), G("?ans aip:implementedBy ?c ."), title_has("c", "공작물")]),
                        " ".join([art("도로법", "점용허가", "a"), hang("a", "h"), G("?h aip:implementedBy ?ans ."), title_has("ans", "공작물")]))),
    # 051: 접도구역이 제한받는 '허용되는 행위' 조를 구체화하도록 위임한 시행령 항 + 그 항을 위임한 도로법 항
    "CQ-051": sel(UNION(
        " ".join([B.Z("접도구역", "z"), G("?z aip:restrictedBy ?r ."), title_has("r", "허용되는행위"), G("?r aip:hasUnit* ?rt . ?s aip:implementedBy ?rt ."), hang_of("s", "ans")]),
        " ".join([B.Z("접도구역", "z"), G("?z aip:restrictedBy ?r ."), title_has("r", "허용되는행위"), G("?r aip:hasUnit* ?rt . ?s aip:implementedBy ?rt ."), hang_of("s", "sh"),
                  G("?up aip:implementedBy ?sh ."), hang_of("up")]))),
    # 원래 질문 복원 2건(11-14 의뢰기업 요청, 변경내역 110절) — QA 보완 패치(위임 항 단위·단서 위임)로 항까지 구분
    # 005: 음성군 조례 '가설건축물' 조를 구체화하도록 위임한 시행령 항 + 그 시행령 항을 (항 단위로) 위임한 법률 항 (위계: 법률 → 시행령 → 조례)
    "CQ-005": sel(UNION(
        " ".join([art(B.YS, "가설건축물", "c"), G("?c aip:hasUnit* ?ct . ?s aip:implementedBy ?ct . ?sa aip:hasUnit+ ?s . ?sa a aip:Article ."),
                  B.law_has("sa", "시행령", "Ls"), hang_of("s")]),
        " ".join([art(B.YS, "가설건축물", "c"), G("?c aip:hasUnit* ?ct . ?s aip:implementedBy ?ct . ?sa aip:hasUnit+ ?s . ?sa a aip:Article ."),
                  B.law_has("sa", "시행령", "Ls"), hang_of("s", "sh"), G("?up aip:implementedBy ?sh ."), hang_of("up")]))),
    # 010: 건축법 '허가와 신고사항의 변경' 조의 항 중 단서(예외)가 하위 법령으로 구체화된 항 + 그 단서를 구체화한 항
    "CQ-010": sel(UNION(
        " ".join([art(B.BLD, "허가와 신고사항의 변경", "v"), hang("v", "ans"), G("?ans aip:provisoImplementedBy ?x .")]),
        " ".join([art(B.BLD, "허가와 신고사항의 변경", "v"), hang("v", "h"), G("?h aip:provisoImplementedBy ?x ."), hang_of("x")]))),
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
        if o in RESTORED:
            note = f"[원번호 {o} 원래 질문 복원] " + RESTORED[o]
    CQS.append((cid, typ, area, q, sorted({mix(x) for x in gold}), note))
    SPARQL[o] = J.SPARQL[o] if RAW.get(o) is None else RAW[o]          # 결과의 항→조 올림은 채점 때 파이썬으로(da2_cq.run, hang_rel.json)


def query(cid):
    return B.PREFIX + "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n" + SPARQL[ORIG[cid]]
