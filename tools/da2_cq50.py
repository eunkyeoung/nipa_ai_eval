# [DA 2-1] CQ 50건(「CQ 질문답변 작성」 50건 0.1v, 2026-11-12 수신) → 정답 집합 + SPARQL
# 정답: 작성자 답변에서 '질문이 묻는 대상을 직접 정하는 조문'(항까지, 호·목 제외). 원칙·용어 설명, 다른 법령 참고, 질문 대상(하위 규정)은 뺌.
#       시스템 결과와 무관하게 답변·법령 원문으로 정함(변경내역 84절에 문항별 근거).
# 질의: 질문에 나온 개념(법령·조 제목 낱말·용도지역·지자체)과 온톨로지 연결(implementedBy·relatedArticle·restrictedBy 등)만 씀.
#       정답 조문 번호·항 번호를 질의에 적지 않음. LIMIT 없음.
# 수신본 그대로 둔 것(의뢰기업 결정): CQ-015 미교체, 마지막 행 CQ-016 중복(→ 'CQ-016-2'로 번호만 구분), 지자체 빈 칸은 '공통'.
import sys
sys.path.insert(0, ".")
from tools.da2_cq_recv import (G, art, hang, has_ho, UNION, sel, national, ans_def_of, impl_art_of_def,
                               hang_with_delegated_ho, upper_of, T_RC, T_EX, T_CF, T_HI)
from tools.da2_cq_sparql import PREFIX

BLD, BLDR, BLDK = "건축법", "건축법 시행령", "건축법 시행규칙"
LP, LPR = "국토의 계획 및 이용에 관한 법률", "국토의 계획 및 이용에 관한 법률 시행령"
YS, YC, GS, GSP, AS_P, IC, ICP = ("음성군 건축 조례", "영천시 건축 조례", "경산시 건축 조례", "경산시 도시계획 조례",
                                   "아산시 도시계획 조례", "인천광역시 건축 조례", "인천광역시 도시계획 조례")
GB, GBR = "개발제한구역의 지정 및 관리에 관한 특별조치법", "개발제한구역의 지정 및 관리에 관한 특별조치법 시행령"


def Z(label, v):
    """용도지역·구역 노드(라벨)"""
    return G(f'?{v} a aip:Zone . ?{v} rdfs:label "{label}" .')


def is_hang(v):
    return G(f'?{v} aip:unitType ?ut_{v} . FILTER(STR(?ut_{v}) = "항")')


def law_has(node, kw, v):
    """node 가 속한 법령 이름에 kw 포함"""
    return G(f'?{v} (aip:hasArticle|aip:hasAppendix) ?{node} . ?{v} aip:lawName ?{v}_n . FILTER(CONTAINS(?{v}_n, "{kw}"))')


def parent_hang(unit, v):
    """호(unit)를 가진 항 v"""
    return G(f"?{v} aip:hasUnit ?{unit} .") + " " + is_hang(v)


def upper_hang_of_art(a, v):
    """조 a(또는 그 하위 단위)를 구체화하도록 위임한 상위 항 v"""
    return G(f"?{v}_x aip:implementedBy ?{a} . ?{v} aip:hasUnit* ?{v}_x .") + " " + is_hang(v)


def own_and_appendix(law_name, kw):
    """조례의 조(제목 kw) + 그 조에 딸린 별표"""
    return sel(UNION(art(law_name, kw, "ans"),
                     " ".join([art(law_name, kw, "t"), G("?ans aip:relatedArticle ?t . ?ans a aip:Appendix .")])))


def chain_from_ordinance(ord_law, kw):
    """조례 조(제목 kw)를 구체화하도록 위임한 시행령 항 + 그 시행령 조를 위임한 법률 항 (위계 추적)"""
    return sel(UNION(
        " ".join([art(ord_law, kw, "c"), G("?up aip:implementedBy ?c ."), G("?ans aip:hasUnit* ?up ."), is_hang("ans")]),
        " ".join([art(ord_law, kw, "c"), G("?up aip:implementedBy ?c ."), G("?h aip:hasUnit* ?up . ?ra aip:hasUnit ?h . ?ra a aip:Article ."),
                  upper_hang_of_art("ra", "ans")])))


def zone_delegation_conflict(zone, city):
    """용도지역(zone)을 제한하는 city 조례 조문 중, 국가 법령 별표 항목의 위임(implementedBy)을 받은 조문 = 우선 적용 조문"""
    return sel(" ".join([Z(zone, "z"), G("?z aip:restrictedBy ?ans ."), law_has("ans", city, "L"),
                         G("?app a aip:Appendix . ?app aip:hasUnit ?nu . ?nu aip:implementedBy ?ans ."), national("app", "an")]))


def zone_appendix_chain(zone, city):
    """용도지역 건축제한: 국가 별표 + 별표 근거 항 + 별표 위임을 받은 city 조례 조 (+ city 조례 별표)"""
    app = " ".join([Z(zone, "z"), G("?z aip:restrictedBy ?app . ?app a aip:Appendix ."), national("app", "an"),
                    G(f'?app aip:appendixTitle ?app_t . FILTER(CONTAINS(REPLACE(STR(?app_t), " ", ""), "{zone}"))')])
    return sel(UNION(
        app.replace("?app", "?ans"),
        " ".join([app, G("?app aip:relatedArticle ?ho ."), parent_hang("ho", "ans")]),
        " ".join([app, G("?app aip:hasUnit ?nu . ?nu aip:implementedBy ?ans ."), law_has("ans", city, "L")]),
        " ".join([Z(zone, "z2"), G("?z2 aip:restrictedBy ?ans . ?ans a aip:Appendix ."), law_has("ans", city, "L2")])))


ROWS = [  # (CQ ID, 유형, 지자체, 질문, 정답, 정답 근거)
    ("CQ-001", T_EX, "공통", "대지가 도로에 2m 미만으로 접한 경우에도 건축이 허용되는 단서 조항은 무엇인가?",
     ["건축법.44.1", "건축법시행령.28.1"], "법 제44조제1항 단서(제2호 공지), 영 제28조제1항(공지의 범위). 농지법(농막)은 인용 법령이라 뺌"),
    ("CQ-002", T_RC, "공통", "(연면적 3천 제곱미터) 공장의 경우 접해야 하는 도로 기준의 근거조문은?",
     ["건축법.44.2", "건축법시행령.28.2"], "법 제44조제2항(대지와 도로 관계 위임), 영 제28조제2항(공장 3천㎡ 6m·4m). 제44조제1항은 일반 원칙(2m)이라 뺌"),
    ("CQ-003", T_EX, "공통", "계획관리지역에서 제1종근린생활시설 중 휴게음식점을 설치할 수 있는 단서 조항은 무엇인가?",
     ["국토계획법시행령.71.1", "국토계획법시행령.별표20", "국토계획법시행규칙.별표2"], "영 제71조제1항제19호·[별표 20], 시행규칙 [별표 2](단서). 법 제76조는 위임 원칙 설명이라 뺌"),
    ("CQ-004", T_CF, "음성군", "음성군에서 한옥 건축시 건축선 및 인접 대지경계선으로부터 띄어야 하는 거리가 건축법과 다를 경우 우선 적용되는 기준은?",
     ["음성군건축조례.30", "음성군건축조례.별표3"], "답변 결론: 음성군 건축 조례 제30조·[별표 3]으로 정한 거리"),
    ("CQ-005", T_HI, "음성군", "음성군 건축 조례에서 천막과 유사한 구조의 창고용 가설건축물을 정한 상위 법령은 무엇인가?",
     ["건축법.20.3", "건축법시행령.15.5"], "답변 결론: 법 제20조제3항, 영 제15조제5항(제16호)"),
    ("CQ-006", T_RC, "공통", "건축법상 건축물 대지가 접해야 하는 도로의 인정 요건은 무엇인가?",
     ["건축법.2.1", "건축법시행령.3의3"], "도로의 정의 법 제2조제1항제11호, 구조·너비 영 제3조의3. 제44조·영 제28조는 접도 기준(인정 요건 아님)이라 뺌"),
    ("CQ-007", T_EX, "공통", "이해관계인의 동의 없이 사실상의 통로를 건축법상 도로로 지정할 수 있는 경우는?",
     ["건축법.45.1"], "법 제45조제1항 단서 제1호·제2호. 제2조제1항제11호나목은 제45조 본문 안의 인용이라 뺌"),
    ("CQ-008", T_RC, "공통", "건축법에서 정하는 일반적인 도로의 최소 너비는 얼마인가?",
     ["건축법.2.1"], "일반 도로 너비 4m = 법 제2조제1항제11호. 영 제3조의3은 '다만'(지형·막다른 도로) 예외라 뺌"),
    ("CQ-009", T_EX, "공통", "특별건축구역에 건축하는 건축물에 대해 적용 받지 않는 예외규정은?",
     ["건축법.73.1"], "법 제73조제1항(적용하지 아니할 수 있는 규정). 제70조·영 제106조는 대상 건축물 요건, 주택법은 열거된 기준이라 뺌"),
    ("CQ-010", T_EX, "공통", "건축허가를 받은 건축물에 대해 허가를 변경하는 경우 허가권자의 허가를 받지 않는 예외조항은?",
     ["건축법.16.1", "건축법시행령.12.2"], "법 제16조제1항 단서, 영 제12조제2항(경미한 변경)"),
    ("CQ-011", T_RC, "공통", "건축허가를 받은 건축물의 착공을 제한하는 근거조문은 무엇인가요?",
     ["건축법.18.1", "건축법.18.2"], "법 제18조제1항·제2항. 국가유산기본법 제3조는 인용 법령 용어 정의라 뺌(작성자 안내함)"),
    ("CQ-012", T_RC, "공통", "대지의 측량에서 발생하는 오차를 허용하는 근거조문은?",
     ["건축법.26", "건축법시행규칙.20", "건축법시행규칙.별표5"], "법 제26조(허용 오차) → 시행규칙 제20조·[별표 5](허용 범위). 공간정보법은 제외 대상 인용이라 뺌"),
    ("CQ-014", T_HI, "공통", "경산시에서 결합건축을 할 수 없는 지역의 근거가 되는 상위 법령은?",
     ["건축법.77의15.3"], "답변 결론: 경산시 건축 조례 제27조의3의 상위 근거 = 법 제77조의15제3항"),
    ("CQ-015", T_HI, "공통", "국토교통부 훈령인 「지구단위계획수립지침」의 위임 근거가 되는 상위 법령(조문)은 무엇인가?",
     ["국토계획법.49", "국토계획법.50", "국토계획법.51", "국토계획법.52"],
     "답변 결론(제49조~제52조). 교체 요청 중(훈령이 온톨로지에 없어 질의 출발점이 없음). 지자체 빈 칸 → '공통'"),
    ("CQ-016", T_CF, "인천 계양구", "인천광역시 계양구 제3종일반주거지역에서 데이터센터를 건축하려고 하는 경우 「국토의 계획 및 이용에 관한 법률」에서는 건축할 수 있는데 인천광역시 계양구에서 건축할 수 없는 경우 우선 적용되는 조문은 무엇인가?",
     ["인천광역시도시계획조례.35"], "답변 결론: 인천광역시 도시계획 조례 제35조. 법 제76조·영 제71조·건축법 시행령 [별표 1]은 원칙·용어 설명이라 뺌"),
    ("CQ-017", T_RC, "경산시", "경산시 자연녹지지역에 (바닥면적 합계 350제곱미터인) 휴게음식점을 신축할 때 적용되는 근거 조문은?",
     ["국토계획법시행령.71.1", "국토계획법시행령.별표17", "경산시도시계획조례.45"],
     "영 제71조제1항제16호·[별표 17], 경산시 도시계획 조례 제45조. 법 제76조제1항은 위임 원칙(CQ-003과 같은 기준)이라 뺌"),
    ("CQ-018", T_EX, "공통", "건축 대지가 도로에는 접하지 않지만 주변에 공원이 있어 통행에 지장이 없는 경우 건축이 허용되는 단서 조항은 무엇인가?",
     ["건축법.44.1", "건축법시행령.28.1"], "법 제44조제1항 단서 제2호, 영 제28조제1항(공원 등 공지)"),
    ("CQ-019", T_EX, "영천시", "영천시에서 연면적 합계 2천500제곱미터인 작물재배사를 신축하려는 대지가 너비 4미터 도로에만 접하는 경우 예외 조항은?",
     ["건축법시행령.28.2", "영천시건축조례.31"], "영 제28조제2항 괄호(건축조례로 정하는 규모 제외), 영천시 건축 조례 제31조. 법 제44조제2항은 위임 원칙이라 뺌"),
    ("CQ-020", T_HI, "영천시", "「영천시 건축 조례」 제22조제2항에서 공장부지 안의 소규모 폐기물저장시설을 축조신고 대상 가설건축물로 정한 규정의 위임 근거가 되는 상위 법령(조문)은 무엇인가?",
     ["건축법.20.3", "건축법시행령.15.5"], "답변 결론: 법 제20조제3항 → 영 제15조제5항(제16호)"),
    ("CQ-021", T_HI, "경산시", "「경산시 도시계획 조례」에서 계획관리지역의 건폐율을 정한 규정의 위임 근거가 되는 상위 법령(조문)은 무엇인가?",
     ["국토계획법.77.1", "국토계획법.77.2", "국토계획법시행령.84.1"], "답변 결론: 법 제77조제1항·제2항, 영 제84조제1항"),
    ("CQ-022", T_EX, "음성군", "충북 음성군 계획관리지역에 단독주택 신축 시 부설주차장 설치가 예외가 되는지?",
     ["음성군주차장조례.15.1"], "음성군 주차장 조례 제15조제1항(제2호 단독주택 예외). 주차장법 제19조제1항은 설치 원칙이라 뺌"),
    ("CQ-023", T_EX, "공통", "토지를 절·성토 시 개발행위허가를 받지 않아도 되는 예외 높이를 규정한 조문은?",
     ["국토계획법.56.4", "국토계획법시행령.53"], "법 제56조제4항제3호, 영 제53조제3호가목(50cm). 제56조제1항은 허가 원칙이라 뺌"),
    ("CQ-024", T_RC, "공통", "계획관리지역에서 건축허가를 받지 않고 건축신고로 가능한 건축규모는?",
     ["건축법.14.1", "건축법시행령.11.1", "건축법시행령.11.3"], "법 제14조제1항제2호·제5호, 영 제11조제1항(제외 구역)·제3항(100㎡). 제11조제1항은 허가 원칙이라 뺌"),
    ("CQ-025", T_EX, "음성군", "음성군에서 농촌주택 표준설계도서에 따라 건축하는 경우 건축허가가 아닌 건축신고로 가능한지?",
     ["건축법.14.1", "건축법시행령.11.3", "음성군건축조례.20"],
     "위임 연결: 음성군 건축 조례 제20조 ← 영 제11조제3항제3호 ← 법 제14조제1항제5호. 법 제11조제1항(원칙)·제23조제4항(용어), 표준설계도서 규칙(온톨로지 없음)은 뺌(작성자 안내함)"),
    ("CQ-026", T_EX, "공통", "하수처리구역 밖의 지역에서 건축물 신축 시 오수처리시설이 아닌 정화조 설치만으로 가능한 단서 조건은?",
     ["하수도법시행령.24.2", "하수도법시행령.24.4"], "영 제24조제2항제1호나목(2㎥ 이하 정화조), 제24조제4항(특별대책지역 등 단서). 법 제34조는 설치 원칙이라 뺌"),
    ("CQ-027", T_EX, "공통", "대지가 둘 이상의 용도지역에 걸치는 경우 건폐율 및 용적률 산정 시 가중평균값을 적용하는 단서 조건은?",
     ["국토계획법.84.1", "국토계획법시행령.94"], "법 제84조제1항, 영 제94조(330㎡). 제77조·제78조는 원칙이라 뺌"),
    ("CQ-028", T_CF, "공통", "성장관리계획구역에서 건폐율을 국토의 계획 및 이용에 관한 법률의 기준보다 완화하여 적용할 수 있는 근거 조문은?",
     ["국토계획법.75의3.2"], "법 제75조의3제2항제1호. 제77조제1항은 원칙이라 뺌"),
    ("CQ-029", T_CF, "경산시", "경산시 주거지역에서 (연면적 10제곱미터) 부속건축물은 정북방향 일조높이 제한 이격거리 이내에 설치할 수 있는지?",
     ["경산시건축조례.27.3"], "답변의 '다만': 경산시 건축 조례 제27조제3항. 법 제61조·영 제86조는 원칙, 영 제2조는 용어, 민법 제242조는 별개 고려(작성자 안내함)라 뺌"),
    ("CQ-030", T_CF, "인천 계양구", "인천광역시의 대지에 컨테이너 임시창고를 설치하고자 하는데 연장 횟수 제한이 있는지?",
     ["인천광역시건축조례.18.5", "인천광역시건축조례.별표5"], "인천광역시 건축 조례 제18조제5항·[별표 5]. 법 제20조·영 제15조는 원칙이라 뺌"),
    ("CQ-031", T_HI, "인천 계양구", "인천광역시 건축 조례로 맞벽건축 세부기준을 위임한 상위 법령은?",
     ["건축법.59.1", "건축법.59.2", "건축법시행령.81.1", "건축법시행령.81.4"], "답변 결론: 영 제81조제1항제3호·제4항, 법 제59조제1항제1호·제2항"),
    ("CQ-032", T_RC, "아산시", "아산시 자연녹지지역에 숙박시설 신축이 가능한 근거조문은?",
     ["국토계획법시행령.71.1", "국토계획법시행령.별표17", "아산시도시계획조례.27", "아산시도시계획조례.별표16"],
     "영 제71조제1항제16호·[별표 17], 아산시 도시계획 조례 제27조·[별표 16]. 법 제76조(위임 원칙), 건축법 시행령 [별표 1](용도), 관광진흥법(지정 근거)은 뺌"),
    ("CQ-033", T_RC, "공통", "건축허가를 받고 공사에 착수하지 않는 경우 해당 허가를 취소하는 착수 기한을 규정한 조문은 무엇인가?",
     ["건축법.11.7"], "법 제11조제7항제1호"),
    ("CQ-034", T_RC, "공통", "대지면적 산정에서 제외되는 부분을 규정한 조문은 무엇인가?",
     ["건축법시행령.119.1"], "영 제119조제1항제1호 단서. 법 제84조는 위임 원칙, 법 제46조·국토계획법 제47조는 본문 안 인용이라 뺌"),
    ("CQ-035", T_RC, "공통", "농업진흥구역에서 설치가능한 농업인 주택을 규정한 조문은 무엇인가?",
     ["농지법.32.1", "농지법시행령.29.4"], "답변 결론: 농지법 제32조제1항제3호, 같은 법 시행령 제29조제4항제1호"),
    ("CQ-036", T_RC, "공통", "2개 대지간의 결합건축 시 대지간의 거리를 규정한 조문은 무엇인가?",
     ["건축법.2.1", "건축법.77의15.1", "건축법시행령.111.1"], "답변 결론: 법 제2조제1항제8호의2, 제77조의15제1항, 영 제111조제1항"),
    ("CQ-037", T_RC, "공통", "건축협정 체결이 가능한 지역 또는 구역은 어떻게 되는지?",
     ["건축법.77의4.1"], "법 제77조의4제1항 각 호. 제6항은 조례 제정 절차라 뺌"),
    ("CQ-039", T_RC, "공통", "개발제한구역에서 기존 단독주택을 증축할 때 건축신고건으로 허용되는 규모의 근거 조문은?",
     ["개발제한구역법.12.3", "개발제한구역법시행령.19"], "답변 결론: 법 제12조제3항, 영 제19조제1호"),
    ("CQ-040", T_RC, "인천 계양구", "인천광역시 계양구 개발제한구역에서 작물 재배사를 건축할 수 있는 자격 조건을 규정한 조문은 무엇인가?",
     ["개발제한구역법.12.1", "개발제한구역법시행령.13.1", "개발제한구역법시행령.별표1", "인천광역시계양구개발제한구역내건축행위등허가에관한조례.2"],
     "답변 결론: 법 제12조제1항제1호, 영 제13조제1항·[별표 1] 제5호, 계양구 조례 제2조"),
    ("CQ-041", T_RC, "공통", "숙박시설에 대해 주거환경 등을 고려해 건축허가를 하지 않을 수 있는 근거 조문은?",
     ["건축법.11.4"], "법 제11조제4항 단서 제1호. 제11조제1항은 허가 원칙이라 뺌"),
    ("CQ-042", T_RC, "공통", "건축허가를 신청할 때 대지의 소유권을 확보하지 않고 그 대지를 사용할 수 있는 권원을 확보하면 되는 근거 조항은?",
     ["건축법.11.11"], "법 제11조제11항제1호. 시행규칙 제6조는 제출 서류 절차라 뺌"),
    ("CQ-043", T_RC, "공통", "지목이 임야인 토지에 건축물을 건축하기 위해 산지전용허가를 받으려는 경우 대체산림자원조성비 부과의 근거 조문은?",
     ["산지관리법.19.1"], "산지관리법 제19조제1항제1호. 제14조(허가)·제2조(정의)·제19조제2항·제5항(납부 시기·감면)은 뺌"),
    ("CQ-044", T_EX, "공통", "지목이 임야인 토지가 산지에서 제외되는 경우의 근거 조문은?",
     ["산지관리법.2", "산지관리법시행령.2"], "법 제2조제1호 단서, 영 제2조제6호. 제14조·제15조·제39조·제42조는 영 제2조 본문 안 인용이라 뺌"),
    ("CQ-045", T_RC, "인천 계양구", "인천광역시 계양구 전용주거지역에서 건축 시 가로구역별 건축물의 최고높이를 정한 근거 조문은?",
     ["건축법.60.2", "인천광역시건축조례.30.1"], "답변 결론: 법 제60조제2항, 인천광역시 건축 조례 제30조제1항제1호"),
    ("CQ-046", T_RC, "공통", "도로를 점용하려는 경우 도로관리청의 허가를 받아야 하는 근거 조문은?",
     ["도로법.61.1"], "도로법 제61조제1항. 제61조제3항·영 제55조는 점용 대상 종류라 뺌"),
    ("CQ-047", T_CF, "인천 계양구", "인천광역시 도시계획 조례에서 준주거지역의 건폐율과 국토의 계획 및 이용에 관한 법률 시행령에서 정하고 있는 건폐율이 다른 경우 어느 조문을 따르는지?",
     ["인천광역시도시계획조례.64.1"], "위임 범위 안에서 정한 인천광역시 도시계획 조례 제64조제1항제6호(60%)"),
    ("CQ-048", T_CF, "인천 계양구", "인천광역시 도시계획 조례에서 생산관리지역 안의 다세대주택(공동주택)의 층수제한과 국토의 계획 및 이용에 관한 법률 시행령의 층수 제한이 다른 경우 어느 조문을 따르는지?",
     ["인천광역시도시계획조례.48"], "위임에 따라 정한 인천광역시 도시계획 조례 제48조제1호(3층 이하)"),
    ("CQ-049", T_CF, "인천 계양구", "국토의 계획 및 이용에 관한 법률 시행령에 따라 생산녹지지역 안에서 건축할 수 있는 건축물을 인천광역시 도시계획 조례가 옥외에 설치하는 골프연습장을 제한 하는 경우 어느 조문을 따라야 하는지?",
     ["인천광역시도시계획조례.45.2"], "답변: 인천광역시 도시계획 조례 제45조제2항제2호가 적용"),
    ("CQ-050", T_CF, "인천 계양구", "인천광역시 주거지역에 건축하는 도시형 생활주택 중 아파트형 주택의 인접 대지경계선으로부터의 이격거리를 「건축법 시행령」과 「인천광역시 건축 조례」가 다르게 정한 경우 어느 조문을 따르는지?",
     ["인천광역시건축조례.27", "인천광역시건축조례.별표2"], "답변: 인천광역시 건축 조례 제27조·[별표 2] 기준 적용"),
    ("CQ-051", T_HI, "공통", "고속국도 인접 대지에서 건축하는 경우 도로경계선으로부터의 이격거리를 제한하는 최상위 근거법령은?",
     ["도로법.40.1", "도로법.40.3"], "답변 결론: 도로법 제40조제1항·제3항"),
    ("CQ-016-2", T_CF, "인천 계양구", "인천광역시 계양구 제3종일반주거지역에서 데이터센터를 건축하려고 하는 경우 「국토의 계획 및 이용에 관한 법률」에서는 건축할 수 있는데 인천광역시 계양구에서 건축할 수 없는 경우 우선 적용되는 조문은 무엇인가?",
     ["인천광역시도시계획조례.35"], "수신본 마지막 행(CQ-016과 같은 문항, 의뢰기업 결정으로 그대로 사용, 81절). 번호만 구분"),
]

_SETBACK = lambda: (hang_with_delegated_ho(BLD, "대지와 도로의 관계"),                     # 접도 항(예외 호가 하위 법령으로 구체화)
                    " ".join([art(BLD, "대지와 도로의 관계", "x"), G("?x aip:hasUnit/aip:hasUnit ?xh ."),
                              G("?xh aip:implementedBy ?xa ."), hang("xa", "ans")]))                # 그 예외 호를 구체화한 하위 조의 항

SPARQL = {
    "CQ-001": sel(UNION(*_SETBACK())),
    # 접도 조의 항 중 하위 법령에 직접 위임한 항 + 그 하위 조의 항 중 조례로 다시 위임된 항(규모별 기준)
    "CQ-002": sel(UNION(" ".join([art(BLD, "대지와 도로의 관계", "x"), hang("x", "ans"), G("?ans aip:implementedBy ?xa ."), G("?xa a aip:Article .")]),
                        " ".join([art(BLD, "대지와 도로의 관계", "x"), hang("x", "h"), G("?h aip:implementedBy ?xa . ?xa a aip:Article ."),
                                  hang("xa", "ans"), G("?ans aip:implementedBy ?c ."), law_has("c", "조례", "cl")]))),
    "CQ-003": None,  # 아래에서 기존 질의(da2_cq_recv CQ-003) 재사용
    "CQ-004": own_and_appendix(YS, "대지안의 공지"),
    "CQ-005": chain_from_ordinance(YS, "가설건축물"),
    "CQ-006": sel(UNION(ans_def_of("정의", "도로"), impl_art_of_def("정의", "도로"))),
    "CQ-007": sel(hang_with_delegated_ho(BLD, "도로의 지정", "조례")),
    "CQ-008": sel(ans_def_of("정의", "너비")),
    # 특별건축구역 특례: '적용 특례' 조의 항 중 적용 제외 규정을 열거(하위 법령으로 구체화)한 항
    "CQ-009": sel(" ".join([art(BLD, "적용 특례", "s"), hang("s", "ans"), has_ho("ans", "ho"), G("?ho aip:implementedBy ?y .")])),
    "CQ-010": sel(UNION(" ".join([art(BLD, "허가와 신고사항의 변경", "v"), hang("v", "ans"), G("?ans aip:implementedBy ?va . ?va a aip:Article .")]),
                        " ".join([art(BLD, "허가와 신고사항의 변경", "v"), hang("v", "h"), G("?h aip:implementedBy ?va ."),
                                  art(BLDR, "변경", "va"), hang("va", "ans")]))),
    "CQ-011": sel(" ".join([art(BLD, "건축허가 제한", "r"), hang("r", "ans")])),
    "CQ-012": sel(UNION(art(BLD, "허용 오차", "ans"),
                        " ".join([art(BLD, "허용 오차", "a"), G("?a aip:implementedBy ?ans .")]),
                        " ".join([art(BLD, "허용 오차", "a"), G("?a aip:implementedBy ?r . ?ans aip:relatedArticle ?r . ?ans a aip:Appendix .")]))),
    "CQ-014": upper_of(GS, "결합건축"),
    # 훈령 노드에서 출발해 위임 상위 조문을 찾음 — 시험 대상 온톨로지에 훈령이 없어 결과 없음
    "CQ-015": sel(" ".join([G('?L aip:lawName "지구단위계획수립지침" .'), G("?ans aip:delegatesTo ?L .")])),
    "CQ-016": zone_delegation_conflict("제3종일반주거지역", "인천광역시"),
    "CQ-017": zone_appendix_chain("자연녹지지역", "경산시"),
    "CQ-018": sel(UNION(*_SETBACK())),
    "CQ-019": sel(UNION(art(YC, "대지와 도로의 관계", "ans"),
                        " ".join([art(YC, "대지와 도로의 관계", "c"), G("?ans aip:implementedBy ?c ."), is_hang("ans")]))),
    "CQ-020": chain_from_ordinance(YC, "가설건축물"),
    "CQ-021": chain_from_ordinance(GSP, "건폐율"),
    "CQ-022": sel(" ".join([art("음성군 주차장 조례", "부설주차장", "p"), hang("p", "ans")])),
    "CQ-023": sel(UNION(" ".join([art(LP, "개발행위의 허가", "x"), hang("x", "ans"), has_ho("ans", "ho"), G("?ho aip:implementedBy ?y ."),
                                  G('?y aip:articleTitle ?yt . FILTER(CONTAINS(STR(?yt), "경미한"))')]),
                        " ".join([art(LP, "개발행위의 허가", "x"), G("?x aip:hasUnit/aip:hasUnit ?ho ."), G("?ho aip:implementedBy ?ans ."),
                                  G('?ans aip:articleTitle ?yt . FILTER(CONTAINS(STR(?yt), "경미한"))')]))),
    # 건축신고 조의 항(호가 시행령으로 구체화) + 그 시행령 조의 항
    "CQ-024": sel(UNION(" ".join([art(BLD, "건축신고", "b"), hang("b", "ans"), has_ho("ans", "ho"), G("?ho aip:implementedBy ?ra ."),
                                  art(BLDR, "건축신고", "ra")]),
                        " ".join([art(BLD, "건축신고", "b"), G("?b aip:hasUnit/aip:hasUnit ?ho ."), G("?ho aip:implementedBy ?ra ."),
                                  art(BLDR, "건축신고", "ra"), hang("ra", "ans")]))),
    # 건축신고 조 → 시행령 항 → 음성군 조례(표준설계도서) 위임 연결
    "CQ-025": sel(UNION(
        " ".join([art(BLD, "건축신고", "b"), hang("b", "ans"), has_ho("ans", "ho"), G("?ho aip:implementedBy ?ra ."), hang("ra", "rh"),
                  G("?rh aip:hasUnit ?rho . ?rho aip:implementedBy ?c ."), art(YS, "표준설계도서", "c")]),
        " ".join([art(BLD, "건축신고", "b"), G("?b aip:hasUnit/aip:hasUnit ?ho ."), G("?ho aip:implementedBy ?ra ."), hang("ra", "ans"),
                  G("?ans aip:hasUnit ?rho . ?rho aip:implementedBy ?c ."), art(YS, "표준설계도서", "c")]),
        art(YS, "표준설계도서", "ans"))),
    "CQ-026": sel(" ".join([art("하수도법 시행령", "개인하수처리시설의 설치", "x"), hang("x", "ans")])),
    "CQ-027": sel(UNION(" ".join([art(LP, "둘 이상의", "x"), hang("x", "ans"), G("?ans aip:implementedBy ?y .")]),
                        " ".join([art(LP, "둘 이상의", "x"), hang("x", "h"), G("?h aip:implementedBy ?ans .")]))),
    # 성장관리계획 조의 항 중 조례로 위임(건폐율 완화 비율)한 항
    "CQ-028": sel(" ".join([art(LP, "성장관리계획", "x"), hang("x", "ans"), G("?ans aip:implementedBy ?c ."), law_has("c", "도시계획 조례", "cl")])),
    "CQ-029": sel(" ".join([art(GS, "일조", "x"), hang("x", "ans")])),
    "CQ-030": sel(UNION(art(IC, "가설건축물", "ans"),
                        " ".join([G('?L aip:lawName "인천광역시 건축 조례" . ?L aip:hasAppendix ?ans .'),
                                  G("?u aip:implementedBy ?ans ."), art(BLDR, "가설건축물", "ga"), G("?ga aip:hasUnit+ ?u .")]))),
    # 인천 조례 맞벽 조를 구체화하도록 위임한 시행령 항 + 그 시행령 조를 위임한 법률 조의 항
    "CQ-031": sel(UNION(
        " ".join([art(IC, "맞벽", "c"), G("?up aip:implementedBy ?c ."), G("?ans aip:hasUnit* ?up ."), is_hang("ans")]),
        " ".join([art(IC, "맞벽", "c"), G("?up aip:implementedBy ?c ."), G("?h aip:hasUnit* ?up . ?ra aip:hasUnit ?h . ?ra a aip:Article ."),
                  G("?bx aip:implementedBy ?ra . ?ba aip:hasUnit+ ?bx . ?ba a aip:Article ."), hang("ba", "ans")]))),
    "CQ-032": zone_appendix_chain("자연녹지지역", "아산시"),
    # 건축허가 조의 항 중 착공(공사 착수) 관련 하위 규정으로 구체화된 항
    "CQ-033": sel(" ".join([art(BLD, "건축허가", "x"), hang("x", "ans"), G("?ans aip:implementedBy ?y ."),
                            G('?y aip:articleTitle ?yt . FILTER(CONTAINS(STR(?yt), "착공"))')])),
    "CQ-034": sel(" ".join([art(BLDR, "면적 등의 산정방법", "x"), hang("x", "ans")])),
    "CQ-035": sel(UNION(" ".join([Z("농업진흥구역", "z"), G("?z aip:restrictedBy ?ans ."), is_hang("ans")]),
                        " ".join([art("농지법 시행령", "농업진흥구역에서 할 수 있는 행위", "x"), hang("x", "ans")]))),
    "CQ-036": sel(UNION(" ".join([art(BLD, "결합건축 대상지", "x"), hang("x", "ans"), G("?ans aip:implementedBy ?y .")]),
                        " ".join([art(BLD, "결합건축 대상지", "x"), hang("x", "h"), G("?h aip:implementedBy ?y ."), hang("y", "ans")]),
                        " ".join([art(BLD, "정의", "d"), hang("d", "ans")]))),
    "CQ-037": sel(" ".join([art(BLD, "건축협정의 체결", "x"), hang("x", "ans"), has_ho("ans", "ho"), G("?ho aip:implementedBy ?c ."),
                            law_has("c", "조례", "cl")])),
    # 개발제한구역을 제한하는 '신고' 조 + 그 조를 구체화하도록 위임한 법률 항
    "CQ-039": sel(UNION(" ".join([Z("개발제한구역", "z"), G("?z aip:restrictedBy ?ans ."), G('?ans aip:articleTitle ?t . FILTER(CONTAINS(STR(?t), "신고"))')]),
                        " ".join([Z("개발제한구역", "z"), G("?z aip:restrictedBy ?y ."), G('?y aip:articleTitle ?t . FILTER(CONTAINS(STR(?t), "신고"))'),
                                  G("?ans aip:implementedBy ?y ."), is_hang("ans")]))),
    "CQ-040": sel(UNION(" ".join([Z("개발제한구역", "z"), G("?z aip:restrictedBy ?ans . ?ans a aip:Appendix ."), law_has("ans", GBR, "L")]),
                        " ".join([Z("개발제한구역", "z"), G("?z aip:restrictedBy ?p . ?p a aip:Appendix ."), law_has("p", GBR, "L"),
                                  G("?ans aip:implementedBy ?p ."), is_hang("ans")]),
                        " ".join([Z("개발제한구역", "z"), G("?z aip:restrictedBy ?p . ?p a aip:Appendix ."), law_has("p", GBR, "L"),
                                  G("?p aip:relatedArticle ?r ."), hang("r", "ans")]),
                        art("인천광역시 계양구 개발제한구역 내 건축행위 등 허가에 관한 조례", "자격", "ans"))),
    "CQ-041": sel(" ".join([art(BLD, "건축허가", "x"), hang("x", "ans"), G("?ans aip:mustComplyWith ?m .")])),
    "CQ-042": sel(" ".join([art(BLD, "건축허가", "x"), hang("x", "ans"), has_ho("ans", "ho"), G("?ho aip:delegatesTo ?d .")])),
    "CQ-043": sel(" ".join([art("산지관리법", "대체산림자원조성비", "x"), hang("x", "ans"), G("?ans aip:implementedBy ?ap . ?ap a aip:Appendix .")])),
    # 산지 정의 조 + 그 정의 호를 구체화한 '산지에서 제외' 시행령 조
    "CQ-044": sel(UNION(" ".join([art("산지관리법", "정의", "ans"), has_ho("ans", "ho"), G("?ho aip:implementedBy ?y ."),
                                  G('?y aip:articleTitle ?yt . FILTER(CONTAINS(STR(?yt), "산지에서 제외"))')]),
                        " ".join([art("산지관리법", "정의", "d"), has_ho("d", "ho"), G("?ho aip:implementedBy ?ans ."),
                                  G('?ans aip:articleTitle ?yt . FILTER(CONTAINS(STR(?yt), "산지에서 제외"))')]))),
    "CQ-045": sel(UNION(" ".join([art(IC, "가로구역별", "c"), G("?ans aip:implementedBy ?c ."), is_hang("ans")]),
                        " ".join([art(IC, "가로구역별", "c"), hang("c", "ans"), has_ho("ans", "ho"), Z("전용주거지역", "z"),
                                  G("?zz skos:broader* ?z . ?zz aip:restrictedBy ?ho .")]))),
    "CQ-046": sel(" ".join([art("도로법", "도로의 점용 허가", "x"), hang("x", "ans"), G("?ans aip:implementedBy ?y .")])),
    "CQ-047": sel(" ".join([Z("준주거지역", "z"), G("?z aip:restrictedBy ?u ."), parent_hang("u", "ans"), G("?a aip:hasUnit ?ans ."),
                            law_has("a", "인천광역시", "L"), G("?nh aip:implementedBy ?a ."),
                            G('?a aip:articleTitle ?at . FILTER(CONTAINS(STR(?at), "건폐율"))')])),
    "CQ-048": zone_delegation_conflict("생산관리지역", "인천광역시"),
    "CQ-049": zone_delegation_conflict("생산녹지지역", "인천광역시"),
    "CQ-050": own_and_appendix(IC, "대지안의 공지"),
    "CQ-051": sel(" ".join([art("도로법", "접도구역", "x"), hang("x", "ans"), G("?ans aip:implementedBy ?y ."),
                            G('?y aip:articleTitle ?yt . FILTER(CONTAINS(STR(?yt), "접도구역"))')])),
}
SPARQL["CQ-016-2"] = SPARQL["CQ-016"]


def _fill():
    from tools.da2_cq_recv import SPARQL as OLD
    SPARQL["CQ-003"] = OLD["CQ-003"]


_fill()
CQS = [(cid, typ, area, q, gold, note) for cid, typ, area, q, gold, note in ROWS]


def query(cid):
    return PREFIX + "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n" + SPARQL[cid]
