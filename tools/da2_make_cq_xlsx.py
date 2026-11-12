# [DA 2-1] CQ 초안 엑셀 생성: tools/da2_cq_draft.py 의 CQ를 법령 원문으로 확인해 spec_data/da2/DA2-1_CQ_정답_초안_*.xlsx 로 저장
# 실행: python tools/da2_make_cq_xlsx.py  (계정은 URL 현황 엑셀에서 읽음 — 경로는 스크립트 안 LawText 인자)
import sys, re
sys.path.insert(0, ".")
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from collections import Counter
from tools.law_text import LawText
from tools.da2_cq_draft import CQS, RC, EX, CF, HI

lt = LawText("C:/Users/user/.claude/uploads/63693724-401f-4d98-baea-2de91264930f/8a9c25ac-___________URL_______0921_V0.2.xlsx")
norm = lambda s: re.sub(r"\s", "", s or "")
rows = []
for cid, q, t, inf, gold, refs, note in sorted(CQS, key=lambda c: c[0]):
    checks, ok_all = [], True
    for law, jo, kw in refs:
        txt = lt.article(law, jo)
        ok = txt is not None and norm(kw) in norm(txt)
        ok_all &= ok
        i = norm(txt or "").find(norm(kw))
        checks.append(f"{law} {'제' + jo + '조' if not jo.startswith('별표') else jo} — '{kw}' {'확인' if ok else '미확인'}")
    rows.append([cid, q, t, inf, "; ".join(gold), "\n".join(checks), "원문 확인" if ok_all else "확인 필요", note])

wb = openpyxl.Workbook()
g = wb.active
g.title = "안내"
for r in [["[DA 2-1] CQ 질의 응답 정확도 — CQ 목록·정답셋 초안", ""], ["", ""],
          ["작성", "QA 담당(Claude). 정답은 법령 수집·적재 관리자(116:17801)의 조문 원문으로 확인. 온톨로지(Fuseki) 조회 결과는 정답 작성에 쓰지 않음"],
          ["구성", "CQ 50건 — 규제 조건 도출 20 / 예외·단서 적용 13 / 법령·조례 충돌 해소 10 / 위계 추적 7 (규격서 v1.51 12쪽 구성)"],
          ["ODP란", "Ontology Design Pattern — 온톨로지를 설계할 때 반복해 쓰는 표준 모델링 틀. 인허가 규제를 표현하는 핵심 틀로 아래 3가지를 쓰고, CQ는 여기에 위계 추적을 더해 4가지로 나눔"],
          ["Regulation-Constraint (규제 조건 도출)", "어떤 대상·지역에 어떤 규제(조건·한도)가 적용되는지. 예: 대지는 도로에 2m 이상 접해야 함, 자연녹지지역 건축 가능 건축물(별표17)"],
          ["Exception (예외·단서 적용)", "원칙 규제에 붙은 예외·단서·적용 배제. 예: 접도 2m 원칙이지만 출입에 지장이 없거나 농막이면 예외(건축법 제44조 제1항 단서)"],
          ["Conflict (법령·조례 충돌 해소)", "여러 법령·조례가 함께 걸려 기준이 다를 때 무엇을 우선하는지. 예: 한옥은 조례의 대지안의 공지 기준보다 한옥법 특례가 우선, 두 용도지역에 걸친 대지(국토계획법 제84조)"],
          ["위계 추적 (법·시행령·시행규칙·조례 위임)", "법 → 시행령 → 시행규칙 → 조례로 이어지는 위임 관계를 따라감. 예: 음성군 군계획 조례 제35조 ← 국토계획법 시행령 제71조 ← 법 제76조"],
          ["자세한 설명", "별첨 'ODP 설명' 시트 — ODP 유형별 뜻·인허가 예·CQ 예, 그리고 지금 온톨로지(Fuseki)에서 각 ODP를 표현하는 요소"],
          ["규격서 예시", "CQ-001, CQ-004, CQ-005, CQ-006, CQ-008 의 문장·유형·추론 의존을 그대로 사용"],
          ["정답 표기", "조문: 법령ID.조.항 (호·목 제외), 가지조문 법령ID.조의가지.항, 별표 법령ID.별표번호 / 수치: 숫자만(단위는 비고)"],
          ["판정(규격서)", "CQ별 SPARQL 결과 바인딩 집합이 정답과 집합 일치(순서 무시, 중복 제거)일 때만 정답. 부분 일치는 오답"],
          ["다음 단계", "정답 확정 후 CQ별 SPARQL 질의 작성 (질의에 정답값을 직접 넣거나 LIMIT으로 결과 수를 맞추지 않음)"]]:
    g.append(r)
g["A1"].font = Font(bold=True, size=13)
g.column_dimensions["A"].width = 38
g.column_dimensions["B"].width = 120
for r in g.iter_rows(min_row=3):
    r[0].font = Font(bold=True)
    r[1].alignment = Alignment(wrap_text=True)

ws = wb.create_sheet("CQ 목록·정답")
hdr = ["CQ ID", "CQ (역량 질문)", "ODP 유형", "추론 의존", "정답 (기대 결과 바인딩 집합)", "근거 조문 원문 확인", "확인 결과", "비고"]
ws.append(hdr)
thin = Side(style="thin", color="BFBFBF")
for r in rows:
    ws.append(r)
    for c in ws[ws.max_row]:
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.border = Border(top=thin, bottom=thin, left=thin, right=thin)
    if r[6] != "원문 확인":
        ws.cell(ws.max_row, 7).fill = PatternFill("solid", fgColor="FFFF00")
for c in ws[1]:
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor="1F4E78")
    c.alignment = Alignment(wrap_text=True, vertical="center")
for k, w in zip("ABCDEFGH", [9, 55, 30, 9, 36, 60, 10, 45]):
    ws.column_dimensions[k].width = w
ws.freeze_panes = "B2"
ws.auto_filter.ref = ws.dimensions

# ---- 별첨: ODP 설명 (유형별 뜻·예시 + 지금 온톨로지에서의 표현)
o = wb.create_sheet("ODP 설명")
hdr_fill = PatternFill("solid", fgColor="1F4E78")
o.append(["[별첨] ODP(Ontology Design Pattern) 유형 설명"])
o["A1"].font = Font(bold=True, size=13)
o.append([])
o.append(["1. ODP 유형별 뜻과 예시"])
o["A3"].font = Font(bold=True, size=11)
o.append(["ODP 유형", "뜻", "쉽게 말하면", "인허가 예", "이 유형의 CQ 예"])
for c in o[o.max_row]:
    c.font = Font(bold=True, color="FFFFFF"); c.fill = hdr_fill
for r in [
    [RC, "어떤 대상·지역에 어떤 규제(조건·한도)가 적용되는지를 표현", "\"여기서는 이렇게 해야 한다 / 하면 안 된다\"",
     "자연녹지지역에서 단독주택은 4층 이하, 대지는 도로에 2m 이상 접해야 함", "건축법상 도로 최소 너비는? → 4m (CQ-008)"],
    [EX, "원칙 규제에 붙은 예외·단서·적용 배제를 표현", "\"원칙은 이렇지만, 다만 ~이면 예외\"",
     "접도 2m 원칙이지만 출입에 지장이 없거나 농막이면 예외 (건축법 제44조 제1항 단서)", "2m 미만 접도여도 허용되는 단서 조항은? → 건축법.44.1 (CQ-001)"],
    [CF, "여러 법령·조례가 동시에 걸려 서로 다를 때 무엇을 우선하는지 표현", "\"둘이 부딪치면 이쪽을 따른다\"",
     "한옥은 조례의 대지안의 공지 기준보다 한옥법 특례가 우선. 대지가 두 용도지역에 걸치면 국토계획법 제84조 기준",
     "음성군에서 한옥의 대지안의 공지 기준은? → 한옥법 시행령 별표2 (CQ-004)"],
    [HI, "법 → 시행령 → 시행규칙 → 조례로 이어지는 위임 관계를 따라감", "\"이 조례 조항은 어느 상위법에서 나왔나\"",
     "음성군 군계획 조례 제35조 ← 국토계획법 시행령 제71조 ← 법 제76조", "조례 제35조의 위임 근거 시행령 조문은? → 국토계획법시행령.71.1 (CQ-049)"],
]:
    o.append(r)
o.append([])
o.append(["2. 지금 온톨로지(Fuseki nipa, 4.2.0-grounding)에서 각 ODP를 표현하는 요소"])
o.cell(o.max_row, 1).font = Font(bold=True, size=11)
o.append(["ODP 유형", "온톨로지 요소", "상태", "비고", ""])
for c in o[o.max_row][:4]:
    c.font = Font(bold=True, color="FFFFFF"); c.fill = hdr_fill
for r in [
    [RC, "LimitStatement(층수·높이 한도), LimitCondition(한도 조건), Zone의 restrictedBy·restrictionBasis(행위제한 근거)", "있음", ""],
    [EX, "Exemption(적용 배제), exemptsZone·exemptedIn·relaxesTarget", "있음", ""],
    [CF, "전용 클래스는 없음. replacesLimit(갈음), refinesLimit(상위 한도 따름), isSpecialLaw(특별법) 같은 관계로 일부만 표현", "부분적",
     "Conflict 유형 CQ는 SPARQL로 답하기 어려운 문항이 있을 수 있음 — SPARQL 작성 때 문항별 확인"],
    [HI, "parentLaw, delegatesTo, hierarchy", "있음", ""],
]:
    o.append(r)
for row in o.iter_rows(min_row=4):
    for c in row:
        c.alignment = Alignment(wrap_text=True, vertical="top")
for k, w in zip("ABCDE", [40, 45, 30, 55, 50]):
    o.column_dimensions[k].width = w

s = wb.create_sheet("유형별 건수")
s.append(["ODP 유형", "건수", "추론 의존", "추론 미의존"])
for t in [RC, EX, CF, HI]:
    rr = [r for r in rows if r[2] == t]
    s.append([t, len(rr), sum(r[3] == "의존" for r in rr), sum(r[3] == "미의존" for r in rr)])
s.append(["계", len(rows), sum(r[3] == "의존" for r in rows), sum(r[3] == "미의존" for r in rows)])
for c in s[1]:
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor="1F4E78")
s.column_dimensions["A"].width = 44
out = "spec_data/da2/DA2-1_CQ_정답_초안_20261111.xlsx"
wb.save(out)
print(out, Counter(r[6] for r in rows), Counter(r[2] for r in rows))
