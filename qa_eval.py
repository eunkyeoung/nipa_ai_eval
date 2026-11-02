"""
AI 사전판단 인허가 QA 채점 스크립트
-----------------------------------
입력
  gold.xlsx  : case_id | 주소 | 시설물 | 정답판정 | 근거  (지금 정답셋 그대로: 근거 텍스트 안에 [법령 제N조] 포함)
               (선택) 필수근거 컬럼: "개발제한구역법 제12조" 처럼 판정에 꼭 필요한 근거만 ; 로 구분
  pred.xlsx  : case_id | AI판정 | AI근거   (시스템 응답 원문을 그대로 붙여도 됨)
출력
  qa_result.xlsx : 케이스별 채점 + 요약 시트
사용: python qa_eval.py gold.xlsx dashboard/pred.xlsx   → dashboard/qa_result.xlsx
"""
import os
import re
import sys
import pandas as pd

# ---------- 1. 정규화 ----------
# 약칭 → 정식 법령명 (앞부분 일치로 치환하므로 '국계법 시행령'도 자동 처리)
# 정답셋·AI 출력에서 새 약칭이 보이면 여기에 추가하세요.
LAW_ALIAS = {
    "국계법": "국토의 계획 및 이용에 관한 법률",
    "국토계획법": "국토의 계획 및 이용에 관한 법률",
    "개발제한구역법": "개발제한구역의 지정 및 관리에 관한 특별조치법",
    "산집법": "산업집적활성화 및 공장설립에 관한 법률",
    "산업집적법": "산업집적활성화 및 공장설립에 관한 법률",
    "산업입지법": "산업입지 및 개발에 관한 법률",
    "가축분뇨법": "가축분뇨의 관리 및 이용에 관한 법률",
    "문화유산법": "문화유산의 보존 및 활용에 관한 법률",
}
_ALIAS = sorted(((re.sub(r"\s", "", k), re.sub(r"\s", "", v)) for k, v in LAW_ALIAS.items()),
                key=lambda kv: -len(kv[0]))
# 인용 단위: 제N조(의M) 또는 별표 N(의M)
TOKEN_RE = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?|별표\s*(\d+)(?:\s*의\s*(\d+))?")
# 법령명: …법 / …법률 / …조례 / …규칙 / …령 (+ 시행령·시행규칙)
LAWNAME_RE = re.compile(r"[가-힣A-Za-z0-9·\s]*?(?:법률|법|조례|규칙|령)(?:\s*시행령|\s*시행규칙)?(?=[^가-힣]|$)")
# 순서 중요: 긴/부정 표현을 먼저 검사 (impossible ⊃ possible, 조건부 가능 ⊃ 가능)
# 시스템 4종 판정: possible(허가 가능) / conditional(조건부 가능) / impossible(허가 불가) / needs_review(검토 필요)
VERDICT_MAP = {"되묻기": "되묻기", "응답없음": "응답없음", "오류": "오류",
               "needs_review": "검토필요", "검토": "검토필요", "확인필요": "검토필요",
               "impossible": "불가", "conditional": "조건부", "possible": "가능",
               "조건부": "조건부", "불허": "불가", "불가": "불가", "허가": "가능",
               "가능": "가능", "허용": "가능"}

def norm_law(name: str) -> str:
    """띄어쓰기·괄호 차이 무시 (예: '도시계획 조례' == '도시계획조례'), 약칭은 정식명으로"""
    key = re.sub(r"[\s\[\]()「」]", "", name)
    for abbr, full in _ALIAS:
        if key.startswith(abbr) and not key.startswith(full):
            return full + key[len(abbr):]
    return key


def _law_before(between: str, prev: str) -> str:
    """조문 앞 텍스트에서 법령명을 찾음. 없으면(예: '제33조 별표 8') 직전 법령을 이어 씀."""
    between = between.replace("같은 법", "같은법")
    between = re.sub(r"제\s*\d+\s*(?:항|호|목)|(?<![가-힣])[가-힣]목", " ", between)   # 항·호·목 제거
    name = None
    for seg in reversed(re.split(r"[·—,()\-]", between)):                       # 가장 가까운 구간부터
        names = [m.group(0).strip() for m in LAWNAME_RE.finditer(seg) if m.group(0).strip()]
        if names:
            name = names[-1]
            break
    if not name:
        # '· 영 제6조' 처럼 '영'만 적힌 경우 = 직전 법률의 시행령
        if prev and any(re.fullmatch(r"\s*영\s*", seg) for seg in re.split(r"[·—,()\-]", between)[-1:]):
            return re.sub(r"(시행령|시행규칙)$", "", prev) + "시행령"
        return prev
    if name in ("시행령", "시행규칙"):                                  # '· 시행령 제6조'
        name = "같은법" + name
    if name.startswith("같은법") and prev:
        base = re.sub(r"(시행령|시행규칙)$", "", prev)
        return re.sub(r"\s", "", base + name[len("같은법"):])
    return norm_law(name)


def extract_citations(text: str) -> set:
    """텍스트에서 (법령명, '12' / '7의2' / '별표8') 집합 추출. [...] 괄호 안을 우선 사용."""
    if not isinstance(text, str):
        return set()
    chunks = re.findall(r"\[([^\]]+)\]", text) or [text]
    out, prev = set(), ""
    for ch in chunks:
        pos = 0
        for m in TOKEN_RE.finditer(ch):
            law = _law_before(ch[pos:m.start()], prev)
            pos = m.end()
            if not law:
                continue
            jo, ui, bt, bui = m.groups()
            art = (f"{jo}의{ui}" if ui else jo) if jo else (f"별표{bt}의{bui}" if bui else f"별표{bt}")
            out.add((law, art))
            prev = law
    return out


def norm_verdict(v) -> str:
    v = re.sub(r"\s", "", str(v))
    for k, val in VERDICT_MAP.items():
        if k in v:
            return val
    return "판정불명"


# ---------- 2. 채점 ----------
def fmt(c):
    law, art = c
    return f"{law} {art}" if art.startswith("별표") else f"{law} 제{art.replace('의', '조의', 1)}" if "의" in art else f"{law} 제{art}조"

def score_case(gold_cites, pred_cites, must_cites):
    gold_laws = {l for l, _ in gold_cites}
    pred_laws = {l for l, _ in pred_cites}
    tp = len(gold_cites & pred_cites)                       # 법령+조문 완전일치
    law_tp = len(gold_laws & pred_laws)                     # 법령명만 일치
    p = tp / len(pred_cites) if pred_cites else 0.0
    r = tp / len(gold_cites) if gold_cites else 1.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    must = must_cites or gold_cites
    must_hit = bool(must & pred_cites)                      # 핵심근거 1개 이상 인용
    return dict(조문_TP=tp, 조문_정밀도=round(p, 3), 조문_재현율=round(r, 3), 조문_F1=round(f1, 3),
                법령명_재현율=round(law_tp / len(gold_laws), 3) if gold_laws else 1.0,
                핵심근거_적중=must_hit,
                누락근거="; ".join(fmt(c) for c in sorted(gold_cites - pred_cites)),
                과잉근거="; ".join(fmt(c) for c in sorted(pred_cites - gold_cites)))



# ---------- 규모(건폐율·용적률·층수) ----------
def _f(v):
    try:
        x = float(str(v).replace("%", "").strip())
        return None if x != x else x                              # NaN 제외
    except (TypeError, ValueError):
        return None


def norm_floor(v):
    """'4층 이하' → '4', '제한 없음' → '제한없음'"""
    if v is None or (isinstance(v, float) and v != v):
        return None
    t = re.sub(r"\s", "", str(v))
    if not t or t in ("—", "-", "nan", "None"):
        return None
    if "제한없" in t or "무제한" in t:
        return "제한없음"
    m = re.search(r"(\d+)", t)
    return m.group(1) if m else t


def gold_scale(r):
    """정답 규모: '정답_건폐율/정답_용적률/정답_층수/정답_용도지역' 컬럼 우선, 없으면 답변 문장에서 추출
    (예: '허용 건축 규모 (일반상업지역 기준) … 건폐율: 80.0% … 용적률: 1300.0%')"""
    txt = " ".join(str(r[c]) for c in r.index                   # 정답셋 컬럼만 (AI… 결과 컬럼 제외)
                   if not str(c).startswith("AI") and ("답변" in str(c) or str(c).startswith("정답_규모"))
                   and isinstance(r[c], str))
    def pick(col, pat, conv):
        if col in r.index and pd.notna(r[col]) and str(r[col]).strip():
            return conv(r[col])
        m = re.search(pat, txt)
        return conv(m.group(1)) if m else None
    return dict(
        bcr=pick("정답_건폐율", r"건폐율\**\s*[:：]?\s*\**\s*([\d.]+)\s*%", _f),
        far=pick("정답_용적률", r"용적률\**\s*[:：]?\s*\**\s*([\d.]+)\s*%", _f),
        floor=pick("정답_층수", r"층수\**\s*[:：]?\s*\**\s*([^\n,(]+)", norm_floor),
        zone=pick("정답_용도지역", r"규모\s*\(([^)]+?)\s*기준\)", lambda x: str(x).strip()))


SCALE_VERDICTS = ("가능", "조건부")   # 규모(건폐율·용적률·층수)는 지을 수 있을 때만 정해짐 → 불가는 채점 제외


def score_scale(r, gold_verdict=None):
    """정답에 값이 있는 항목만 비교 (없으면 None). 정답이 불가면 규모는 채점하지 않음"""
    out = dict(정답_규모="", AI_규모="")
    if gold_verdict is not None and gold_verdict not in SCALE_VERDICTS:
        return out
    g = gold_scale(r)
    if not any(v is not None for v in g.values()):
        return out
    ai_bcr, ai_far, ai_floor = _f(r.get("AI건폐율")), _f(r.get("AI용적률")), norm_floor(r.get("AI층수"))
    ai_zone = str(r.get("AI적용용도지역") or "")
    if g["bcr"] is not None:
        out["건폐율_일치"] = ai_bcr is not None and abs(ai_bcr - g["bcr"]) < 0.01
    if g["far"] is not None:
        out["용적률_일치"] = ai_far is not None and abs(ai_far - g["far"]) < 0.01
    if g["floor"] is not None:
        out["층수_일치"] = ai_floor == g["floor"]
    if g["zone"]:
        out["적용용도지역_일치"] = bool(ai_zone) and (g["zone"] in ai_zone or ai_zone.split("·")[0] in g["zone"])
    checks = [out[k] for k in ("건폐율_일치", "용적률_일치", "층수_일치") if k in out]
    out["규모_일치"] = all(checks) if checks else None
    fmt_ = lambda z, b, f, fl: " · ".join(x for x in [z or "", f"건폐율 {b:g}%" if b is not None else "",
                                                     f"용적률 {f:g}%" if f is not None else "",
                                                     f"층수 {fl}" if fl else ""] if x)
    out["정답_규모"] = fmt_(g["zone"], g["bcr"], g["far"], g["floor"])
    out["AI_규모"] = fmt_(ai_zone, ai_bcr, ai_far, r.get("AI층수") if isinstance(r.get("AI층수"), str) else "")
    return out


# ---------- 행위허가(필요 허가 종류) ----------
# 1차 사업 허가 10종. 정답셋 표기와 AI 답변 문장을 같은 규칙으로 분류 (공백 제거 후 정규식)
PERMIT_TYPES = [
    ("개발행위허가(토지분할 등)", r"토지분할|분할허가|토지의분할|개발행위허가\(토지분할"),
    ("개발행위허가(단순 형질변경)", r"형질변경"),
    ("개발제한구역 내 행위허가", r"개발제한구역[^.。\n]{0,20}행위허가|행위허가[^.。\n]{0,10}개발제한구역"),
    ("개발제한구역 내 건축허가", r"개발제한구역[^.。\n]{0,20}건축허가"),
    ("건축허가(신축)", r"건축허가"),
    ("공장설립승인(허가)", r"공장설립"),
    ("농지전용허가", r"농지전용"),
    ("초지전용허가", r"초지전용"),
    ("산지전용허가", r"산지전용"),
    ("도로 점용허가", r"도로점용"),
]
PERMIT_NAMES = [n for n, _ in PERMIT_TYPES]


def classify_permits(text, with_evidence=False):
    """문장에서 허가 종류 찾기 → {허가명: 근거 문장 일부}
    '개발제한구역 내 건축허가'로 잡힌 부분은 '건축허가(신축)'에서 다시 세지 않음"""
    if not isinstance(text, str) or not text.strip():
        return {} if with_evidence else set()
    t = re.sub(r"[ \t]", "", text)
    found = {}
    for name, pat in PERMIT_TYPES:
        src = t
        if name == "건축허가(신축)":                       # 개발제한구역 건축허가·행위허가 부분 제외
            for n2, p2 in PERMIT_TYPES[2:4]:
                src = re.sub(p2, "", src)
        m = re.search(pat, src)
        if m:
            a, b = max(0, m.start() - 30), min(len(src), m.end() + 30)
            found[name] = re.sub(r"[*#`]", "", src[a:b]).replace("\n", " ")
    return found if with_evidence else set(found)


def gold_permits(value):
    """정답셋 '정답_필요허가' (예: '산지전용허가; 건축허가(신축)') → 허가명 집합, 분류 안 된 항목"""
    if not isinstance(value, str) or not value.strip():
        return None, []
    got, unknown = set(), []
    for item in re.split(r"[;,\n/·]", value):
        item = item.strip()
        if not item:
            continue
        c = classify_permits(item)
        if "개발제한구역 내 건축허가" in c:
            c.discard("건축허가(신축)")
        if c:
            got |= c
        else:
            unknown.append(item)
    return got, unknown


def score_permits(r):
    g, unknown = gold_permits(r.get("정답_필요허가"))
    if g is None:
        return {}
    a = set(x for x in str(r.get("AI필요허가") or "").split("; ") if x)
    tp, fp, fn = g & a, a - g, g - a
    p = len(tp) / len(a) if a else (1.0 if not g else 0.0)
    rc = len(tp) / len(g) if g else 1.0
    return {"허가_정밀도": round(p, 3), "허가_재현율": round(rc, 3),
            "허가_완전일치": g == a, "허가_누락": "; ".join(sorted(fn)), "허가_과잉": "; ".join(sorted(fp)),
            "정답_필요허가(분류)": "; ".join(n for n in PERMIT_NAMES if n in g),
            "허가_미분류": "; ".join(unknown), "_permit_sets": (g, a)}


def permit_table(res):
    """허가 종류별 TP·FP·FN·정밀도·재현율·F1 (정답_필요허가가 있는 케이스만)"""
    rows = []
    sets = res.attrs.get("permit_sets") or []
    if not sets:
        return pd.DataFrame()
    for n in PERMIT_NAMES:
        tp = sum(n in g and n in a for g, a in sets)
        fp = sum(n not in g and n in a for g, a in sets)
        fn = sum(n in g and n not in a for g, a in sets)
        p = tp / (tp + fp) if tp + fp else None
        r = tp / (tp + fn) if tp + fn else None
        if tp + fn == 0 and tp + fp == 0:
            f1 = None                                             # 정답에도 AI에도 없음 → 산출 불가
        else:
            f1 = 2 * tp / (2 * tp + fp + fn)                      # = 2PR/(P+R), 한쪽이 0건이어도 0으로 계산
        rows.append({"허가 종류": n, "정답 필요 건수": tp + fn, "AI 언급 건수": tp + fp, "TP": tp, "FP(과잉)": fp,
                     "FN(누락)": fn, "정밀도": p, "재현율": r, "F1": f1})
    return pd.DataFrame(rows)


def load_gold(gold_path):
    gold = pd.read_excel(gold_path)
    if "정답판정" not in [str(c).strip() for c in gold.columns]:   # 첫 줄이 제목이면 2번째 줄을 헤더로
        gold = pd.read_excel(gold_path, header=1)
    gold.columns = [str(c).strip() for c in gold.columns]
    return gold


def evaluate(gold, pred):
    """정답셋 DataFrame + 예측 DataFrame → (요약, 혼동행렬, 케이스별) DataFrame"""
    if "회차" in pred.columns and pred["회차"].max() > 1:
        # 반복(일관성) 결과가 들어오면 정확도 채점은 1회차만 사용 — 일관성은 consistency_report.xlsx 참고
        print("※ 반복 실행 결과 파일입니다. 정확도 채점에는 1회차만 사용합니다.")
        pred = pred[pred["회차"] == 1]
    pcols = [c for c in pred.columns if c not in gold.columns or c == "case_id"]
    df = gold.merge(pred[pcols], on="case_id", how="left")
    rows = []
    for _, r in df.iterrows():
        g_cites = extract_citations(r["근거"])
        p_cites = extract_citations(r.get("AI근거"))
        must = extract_citations(r["필수근거"]) if "필수근거" in r and pd.notna(r["필수근거"]) else set()
        gv, pv = norm_verdict(r["정답판정"]), norm_verdict(r.get("AI판정"))
        s = score_case(g_cites, p_cites, must)
        s.update(case_id=r["case_id"], 정답판정=gv, AI판정=pv, 판정일치=gv == pv,
                 오허가=(gv == "불가" and pv == "가능"),            # 가장 위험한 오류
                 오불허=(gv == "가능" and pv == "불가"))            # 기회 손실 오류
        s.update(score_scale(r, gv))
        s.update(score_permits(r))
        # 종합 정답: 판정 + 핵심근거 (+ 정답이 가능·조건부이고 정답 규모가 있으면 규모까지)
        sc = s.get("규모_일치")
        s["종합정답"] = bool(s["판정일치"] and s["핵심근거_적중"] and (sc is None or bool(sc)))
        rows.append(s)
    res = pd.DataFrame(rows)
    front = ["case_id", "정답판정", "AI판정", "판정일치", "오허가", "오불허", "핵심근거_적중", "종합정답"]
    res = res[front + [c for c in res.columns if c not in front]]
    n = len(res)
    summary = pd.DataFrame([
        ("케이스 수", n),
        ("판정 정확도", res["판정일치"].mean()),
        ("오허가율 (정답 불가→AI 가능)", res["오허가"].sum() / max((res["정답판정"] == "불가").sum(), 1)),
        ("오불허율 (정답 가능→AI 불가)", res["오불허"].sum() / max((res["정답판정"] == "가능").sum(), 1)),
        ("핵심근거 적중률", res["핵심근거_적중"].mean()),
        ("조문 F1 (평균)", res["조문_F1"].mean()),
        ("법령명 재현율 (평균)", res["법령명_재현율"].mean()),
        ("종합 정답률 (판정+핵심근거+규모)", res["종합정답"].mean()),
    ], columns=["지표", "값"])
    extra = []                                                    # 규모: 정답에 값이 있는 케이스만
    for col, name in [("건폐율_일치", "건폐율 정확도"), ("용적률_일치", "용적률 정확도"),
                      ("층수_일치", "층수 정확도"), ("적용용도지역_일치", "적용 용도지역 일치율"),
                      ("규모_일치", "규모 정확도 (건폐율·용적률·층수 모두 일치)")]:
        if col in res and res[col].notna().any():
            v = res[col].dropna().astype(bool)
            extra.append((f"{name} (대상 {len(v)}건)", v.mean()))
    if "_permit_sets" in res and res["_permit_sets"].notna().any():   # 행위허가: 정답_필요허가가 있는 케이스만
        sets = [x for x in res["_permit_sets"] if isinstance(x, tuple)]
        TP = sum(len(g & a) for g, a in sets)
        FP = sum(len(a - g) for g, a in sets)
        FN = sum(len(g - a) for g, a in sets)
        P = TP / (TP + FP) if TP + FP else 0.0
        R = TP / (TP + FN) if TP + FN else 0.0
        extra += [(f"필요 행위허가 정밀도 (대상 {len(sets)}건)", P),
                  (f"필요 행위허가 재현율 (대상 {len(sets)}건)", R),
                  (f"필요 행위허가 F1 (대상 {len(sets)}건)", 2 * P * R / (P + R) if P + R else 0.0),
                  (f"필요 행위허가 완전일치율 (대상 {len(sets)}건)", sum(g == a for g, a in sets) / len(sets))]
    if extra:
        summary = pd.concat([summary, pd.DataFrame(extra, columns=["지표", "값"])], ignore_index=True)
    if "_permit_sets" in res:                                     # 집합은 엑셀에 못 쓰므로 attrs로 옮김
        res.attrs["permit_sets"] = [x for x in res["_permit_sets"] if isinstance(x, tuple)]
        res = res.drop(columns=["_permit_sets"])
    confusion = pd.crosstab(res["정답판정"], res["AI판정"], margins=True)
    return summary, confusion, res


def save_result(summary, confusion, res, out_path):
    pt = permit_table(res)
    with pd.ExcelWriter(out_path) as w:
        summary.to_excel(w, sheet_name="요약", index=False)
        confusion.to_excel(w, sheet_name="판정_혼동행렬")
        res.to_excel(w, sheet_name="케이스별", index=False)
        if not pt.empty:
            pt.to_excel(w, sheet_name="행위허가별", index=False)


def main(gold_path, pred_path, out_path=os.path.join("dashboard", "qa_result.xlsx")):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    summary, confusion, res = evaluate(load_gold(gold_path), pd.read_excel(pred_path))
    save_result(summary, confusion, res, out_path)
    print(summary.to_string(index=False))
    print("\n", confusion)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
