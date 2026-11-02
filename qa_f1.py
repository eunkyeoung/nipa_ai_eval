"""
[AI 2] 사전 진단 판단 F1 스코어
--------------------------------
[AI 1]과 같은 정답셋(gold.xlsx)과 AI 결과(pred.xlsx)로 4종 판단 유형별 지표를 산출합니다.

  판단 유형 4종 (시스템 judgment.verdict 기준)
    가능     = possible      (허가 가능)
    조건부   = conditional   (조건부 가능)
    불가     = impossible    (허가 불가)
    검토필요 = needs_review  (검토 필요)

  산출 내용
    (5) 유형별 TP / FP / FN / TN  — sklearn.metrics.confusion_matrix 로 4×4 혼동행렬 작성
    (6) 유형별 Precision / Recall / Specificity / F1 (%)  — 4종 × 4지표 = 16개 값
    (7) 가중평균 F1 = Σ(유형별 F1 × 유형별 사례 수) / 전체 사례 수, (선택) 중요도 가중치, 목표(95%) 달성 판정
    (8) 오탐(FP)·미탐(FN) 사례 목록 + 자동 추정 원인 (확인 원인·개선사항은 사람이 작성)

사용
  python qa_f1.py gold.xlsx dashboard/pred.xlsx
  python qa_f1.py gold.xlsx dashboard/pred.xlsx --target 95 --weights 불가=2,가능=1,조건부=1,검토필요=1
  python qa_f1.py gold.xlsx dashboard/pred.xlsx --no-answer 검토필요     # 무응답을 검토필요로 간주
필요
  pip install scikit-learn
"""
import argparse
import os

import pandas as pd

from qa_eval import extract_citations, load_gold, norm_verdict

TYPES = ["가능", "조건부", "불가", "검토필요"]
TYPE_DESC = {"가능": "허가 가능 (possible)", "조건부": "조건부 가능 (conditional)",
             "불가": "허가 불가 (impossible)", "검토필요": "검토 필요 (needs_review)"}
NO_ANSWER = "무응답"            # 되묻기·응답없음·오류 — 4종 어디에도 속하지 않는 AI 결과


# ---------------- 데이터 준비 ----------------
def prepare(gold, pred, no_answer="separate"):
    """정답·AI 판정을 4종 라벨로 맞춘 케이스 표"""
    if "회차" in pred.columns and pred["회차"].max() > 1:
        pred = pred[pred["회차"] == 1]
    pcols = [c for c in pred.columns if c not in gold.columns or c == "case_id"]
    df = gold.merge(pred[pcols], on="case_id", how="left")
    df["정답유형"] = df["정답판정"].map(norm_verdict)
    df["AI유형"] = df.get("AI판정", pd.Series(index=df.index, dtype=object)).map(norm_verdict)
    df["AI원본판정"] = df.get("AI판정")
    df.loc[~df["AI유형"].isin(TYPES), "AI유형"] = NO_ANSWER
    if no_answer in TYPES:                                   # 무응답을 특정 유형(예: 검토필요)으로 간주
        df.loc[df["AI유형"] == NO_ANSWER, "AI유형"] = no_answer
    bad = df[~df["정답유형"].isin(TYPES)]
    df = df[df["정답유형"].isin(TYPES)].copy()
    return df, bad


# ---------------- (5) 혼동행렬 & TP/FP/FN/TN ----------------
def confusion(df):
    """행 = 실제(정답), 열 = 판단(AI). 4×4 + (무응답 열)"""
    cols = TYPES + ([NO_ANSWER] if (df["AI유형"] == NO_ANSWER).any() else [])
    labels = cols                                             # 정답에는 무응답이 없으므로 행은 4종만 사용
    try:
        from sklearn.metrics import confusion_matrix
        cm = confusion_matrix(df["정답유형"], df["AI유형"], labels=labels)
        tool = "sklearn.metrics.confusion_matrix"
    except ImportError:                                       # sklearn 미설치 시 동일 계산
        cm = pd.crosstab(df["정답유형"], df["AI유형"]).reindex(index=labels, columns=labels, fill_value=0).values
        tool = "pandas.crosstab (sklearn 미설치)"
    mat = pd.DataFrame(cm, index=labels, columns=labels).loc[TYPES, cols]
    mat.index.name, mat.columns.name = "실제(정답) ↓ / 판단(AI) →", None
    return mat, tool


def per_type(mat, n_total):
    """유형별 TP/FP/FN/TN + Precision/Recall/Specificity/F1 (%)"""
    rows = []
    for t in TYPES:
        tp = int(mat.loc[t, t])
        fp = int(mat[t].sum() - tp)                           # AI가 t로 판단했지만 실제는 다른 유형
        fn = int(mat.loc[t].sum() - tp)                       # 실제는 t인데 AI가 다른 유형(또는 무응답)
        tn = int(n_total - tp - fp - fn)
        pct = lambda a, b: round(a / b * 100, 2) if b else None   # 분모 0 → 산출 불가(None)
        p, r, sp = pct(tp, tp + fp), pct(tp, tp + fn), pct(tn, tn + fp)
        support = tp + fn
        if p is not None and r is not None:
            f1 = round(2 * p * r / (p + r), 2) if (p + r) else 0.0
        else:
            f1 = 0.0 if support else None                     # 실제 사례가 있는데 한 번도 못 맞힘 → 0
        rows.append(dict(판단유형=t, 설명=TYPE_DESC[t], 사례수=support, AI판단수=tp + fp,
                         TP=tp, FP=fp, FN=fn, TN=tn,
                         **{"Precision(%)": p, "Recall(%)": r, "Specificity(%)": sp, "F1(%)": f1,
                            "오탐률(%)": None if sp is None else round(100 - sp, 2)}))
    return pd.DataFrame(rows)


# ---------------- (7) 가중평균 F1 ----------------
def weighted_f1(metrics, weights=None, target=95.0):
    m = metrics[metrics["사례수"] > 0].copy()
    m["F1(%)"] = m["F1(%)"].fillna(0)
    n = m["사례수"].sum()
    wf1 = round((m["F1(%)"] * m["사례수"]).sum() / n, 2) if n else None
    out = [("전체 사례 수", int(n)),
           ("평가 대상 유형 (사례 1건 이상)", ", ".join(m["판단유형"])),
           ("사례 0건이라 제외된 유형", ", ".join(metrics.loc[metrics["사례수"] == 0, "판단유형"]) or "-"),
           ("가중평균 F1(%) = Σ(F1×사례수)/전체 사례수", wf1),
           ("매크로 F1(%) (단순평균, 참고)", round(m["F1(%)"].mean(), 2) if len(m) else None)]
    final = wf1
    if weights:
        m["중요도"] = m["판단유형"].map(lambda t: float(weights.get(t, 1.0)))
        den = (m["사례수"] * m["중요도"]).sum()
        iwf1 = round((m["F1(%)"] * m["사례수"] * m["중요도"]).sum() / den, 2) if den else None
        out.append(("중요도 가중치", ", ".join(f"{t}={weights.get(t, 1.0)}" for t in TYPES)))
        out.append(("중요도 가중 F1(%) = Σ(F1×사례수×중요도)/Σ(사례수×중요도)", iwf1))
        final = iwf1
    out.append(("목표(%)", target))
    out.append(("종합 F1 Score(%)", final))
    out.append(("달성 여부", "달성" if final is not None and final >= target else "미달성"))
    return pd.DataFrame(out, columns=["항목", "값"]), final


# ---------------- (8) 오탐·미탐 사례 분석 ----------------
def guess_cause(r):
    """정답·AI 결과로 추정한 원인 (사람이 확인 후 '확인 원인'에 확정)"""
    g, a = r["정답유형"], r["AI유형"]
    gold_c = extract_citations(r.get("근거"))
    ai_all = extract_citations(r.get("AI전체인용법령"))
    deny = str(r.get("AI불가근거") or "")
    summary = str(r.get("AI요약") or "")
    if a == NO_ANSWER or str(r.get("AI원본판정")) in ("되묻기", "응답없음", "오류"):
        if "위치" in summary or "주소" in summary:
            return "입력 오류 — 주소 검색 실패 (주소 표기 확인)"
        return "시스템 무응답/오류"
    if a == "검토필요":
        return "모델 신뢰도 부족 — 판단 보류(검토 필요)"
    if g == "검토필요":
        return "검토 필요 상황을 단정 판단 — 불확실성 인식 부족"
    laws_missing = bool(gold_c) and not ({l for l, _ in gold_c} & {l for l, _ in ai_all})
    if g in ("불가", "조건부") and a == "가능":
        return ("룰셋 적용 누락 — 정답 근거 법령을 검토하지 않음" if laws_missing
                else "룰 판정 오류 — 근거 법령은 검토했으나 허용으로 판단")
    if g in ("가능", "조건부") and a == "불가":
        if "제44조" in deny or "접도" in deny or "도로" in deny:
            return "공간 데이터/측정 오류 — 접도(도로) 분석 결과 상이"
        if "구역" in deny or "시설" in deny or "지역" in deny:
            return "용도지역·구역 판단 상이 — 과잉 규제 적용"
        return "룰 판정 오류 — 불허 사유 확인 필요"
    if {g, a} == {"가능", "조건부"} or {g, a} == {"불가", "조건부"}:
        return "조건부 여부 판단 상이 — 조건(허가 조건·예외 규정) 인식 차이"
    return "원인 미분류"


def error_cases(df, addr_col=None, fac_col=None):
    rows = []
    for _, r in df[df["정답유형"] != df["AI유형"]].iterrows():
        g, a = r["정답유형"], r["AI유형"]
        kinds = [f"{g} 미탐(FN)"] + ([f"{a} 오탐(FP)"] if a in TYPES else [])
        rows.append({"case_id": r["case_id"],
                     "주소": r.get(addr_col) if addr_col else r.get("주소"),
                     "시설물": r.get(fac_col) if fac_col else r.get("시설물"),
                     "실제(정답)": g, "판단(AI)": a, "오류 구분": " / ".join(kinds),
                     "추정 원인": guess_cause(r),
                     "정답 근거": r.get("근거"), "AI 근거": r.get("AI근거"), "AI 요약": r.get("AI요약"),
                     "확인 원인": "", "개선사항": "", "후속 계획": ""})
    return pd.DataFrame(rows)


def fp_patterns(errs):
    """특이도 보조 분석: 유형별 오탐이 어떤 실제 유형에서 왔는지"""
    if errs.empty:
        return pd.DataFrame(columns=["판단(AI)", "실제(정답)", "건수", "주요 추정 원인"])
    fp = errs[errs["판단(AI)"].isin(TYPES)]
    g = fp.groupby(["판단(AI)", "실제(정답)"])
    return g.agg(건수=("case_id", "count"),
                 **{"주요 추정 원인": ("추정 원인", lambda s: s.value_counts().index[0])}).reset_index()


# ---------------- 전체 실행 ----------------
def run(gold, pred, weights=None, target=95.0, no_answer="separate", addr_col=None, fac_col=None):
    df, bad = prepare(gold, pred, no_answer)
    mat, tool = confusion(df)
    metrics = per_type(mat, len(df))
    summary, final = weighted_f1(metrics, weights, target)
    errs = error_cases(df, addr_col, fac_col)
    notes = [("혼동행렬 산출 도구", tool), ("무응답 처리", "별도 열(무응답) — 해당 실제 유형의 미탐(FN)으로만 계산"
              if no_answer not in TYPES else f"'{no_answer}'로 판단한 것으로 간주")]
    if len(bad):
        notes.append(("정답판정이 4종이 아니어서 제외된 케이스", ", ".join(map(str, bad["case_id"]))))
    summary = pd.concat([summary, pd.DataFrame(notes, columns=["항목", "값"])], ignore_index=True)
    return dict(summary=summary, matrix=mat, metrics=metrics, errors=errs,
                patterns=fp_patterns(errs), final=final, cases=df)


def save(res, out):
    with pd.ExcelWriter(out) as w:
        res["summary"].to_excel(w, sheet_name="종합F1", index=False)
        res["matrix"].to_excel(w, sheet_name="혼동행렬")
        res["metrics"].to_excel(w, sheet_name="유형별지표", index=False)
        res["errors"].to_excel(w, sheet_name="오탐미탐사례", index=False)
        res["patterns"].to_excel(w, sheet_name="오탐패턴", index=False)


def parse_weights(s):
    if not s:
        return None
    out = {}
    for kv in s.split(","):
        k, v = kv.split("=")
        out[norm_verdict(k.strip()) if norm_verdict(k.strip()) in TYPES else k.strip()] = float(v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gold")
    ap.add_argument("pred")
    ap.add_argument("--out", default=os.path.join("dashboard", "f1_result.xlsx"))
    ap.add_argument("--target", type=float, default=95.0, help="목표 F1(%%)")
    ap.add_argument("--weights", help="유형별 중요도 예: 불가=2,가능=1,조건부=1,검토필요=1")
    ap.add_argument("--no-answer", default="separate", help="무응답 처리: separate(기본) 또는 검토필요 등 4종 중 하나")
    a = ap.parse_args()
    res = run(load_gold(a.gold), pd.read_excel(a.pred), parse_weights(a.weights), a.target, a.no_answer)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    save(res, a.out)
    pd.set_option("display.width", 200)
    print("■ 4×4 혼동행렬 (행: 실제, 열: 판단)\n", res["matrix"], "\n")
    print("■ 유형별 지표\n", res["metrics"][["판단유형", "사례수", "TP", "FP", "FN", "TN", "Precision(%)",
                                          "Recall(%)", "Specificity(%)", "F1(%)"]].to_string(index=False), "\n")
    print("■ 종합\n", res["summary"].to_string(index=False))
    print(f"\n오탐·미탐 {len(res['errors'])}건 → {a.out} '오탐미탐사례' 시트")


if __name__ == "__main__":
    main()
