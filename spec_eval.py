"""
성과지표(의뢰자 제시 기준, 시험의뢰서 v.13) 시험항목 2-1 ~ 2-5 산정 로직
-------------------------------------------------------------------
[AI 1] 사전 진단 판단 정확도        목표 ≥ 95 %
[AI 2] 사전 진단 판단 F1 스코어      목표 ≥ 95 %  (유의어·유사 표현 질의, 2종 가중평균 F1)
[AI 3] 사전 진단 판단 일관성        목표 ≥ 98 %  (6단계 처리 로그 회차 간 일치)
[AI 4] 조건부 가능 판단 정확도      목표 ≥ 95 %  (판정 결과 + 단서 조건 근거 조문 집합)
[AI 5] 환각 발생률                  목표 ≤ 3 %   (환각 유형 ①~④)

모든 결과 수치는 성과지표 기준대로 실수(%, 소수점 셋째자리 이하 버림 = 소수 둘째자리까지) 표기.
"""
import hashlib
import json
import math
import os
import re

import pandas as pd

from qa_eval import LAW_ALIAS, classify_permits, norm_law

# ---------------------------------------------------------------- 공통
SPEC = {
    "AI1": dict(no="2-1", name="[AI 1] 인허가 질의에 대한 사전 진단 판단 결과 도출 기능", metric="사전 진단 판단 정확도",
                target=95.0, cmp=">=", log="diagnose_log_ai1.jsonl",
                formula="[AI 1] 사전 진단 판단 정확도(%) = AI 판단 결과 일치 건수 / 전체 테스트 건수 × 100",
                data="표준 질의문 50건 (허가 가능 25 / 허가 불가능 25)"),
    "AI2": dict(no="2-2", name="[AI 2] 유의어·유사 표현 질의에 대한 동일 사전 진단 결과 재현 기능", metric="사전 진단 판단 F1 스코어",
                target=95.0, cmp=">=", log="diagnose_log_ai2.jsonl",
                formula="[AI 2] 종합 F1 Score(%) = 판단 유형별 F1 Score의 가중평균  (가중치 = 판단 유형별 정답 질의 수, F1 = 2 × Precision × Recall / (Precision + Recall))",
                data="시나리오 10건 × 질의 5건(원문 1 + 변형 4) = 50 질의"),
    "AI3": dict(no="2-3", name="[AI 3] 사전 진단 추론·데이터 조회 과정의 동일 프로세스 수행 및 로그 기록 기능", metric="사전 진단 판단 일관성",
                target=98.0, cmp=">=", log="diagnose_log_ai3.jsonl",
                formula="[AI 3] 사전 진단 판단 일관성(%) = 동일 결과 도출 횟수 / 전체 반복 수행 횟수 × 100  (1회차 로그 = 기준, 기준 회차도 동일 결과에 포함)",   # 수행계획서 문구
                data="질의 10건 × 반복 5회 = 50회 수행"),
    "AI4": dict(no="2-4", name="[AI 4] 예외·단서 규정 적용 사례의 허가 가능/허가 불가능 판정 및 단서 조건 제시 기능", metric="조건부 가능 판단 정확도",
                target=95.0, cmp=">=", log="diagnose_log_ai4.jsonl",
                formula="[AI 4] 조건부 가능 판단 정확도(%) = 조건부 판단 및 제시 조건이 모두 적합한 건수 / 전체 조건부 테스트 건수 × 100  (제시 조건 = 단서 조건 근거 조문, 보완사항·행정절차는 보조 지표)",
                data="표준 질의문 50건 (허가 가능 20 / 허가 불가능 30) · 예외 유형 4종(예외 조항 30 / 단서 규정 14 / 협의 대상 0 / 심의 대상 6)"),
    "AI5": dict(no="2-5", name="[AI 5] 법령·조례 근거의 정확성이 확보된 사전 진단 응답 생성 기능", metric="환각 발생률",
                target=3.0, cmp="<=", log="diagnose_log_ai5.jsonl",
                formula="[AI 5] 환각 발생률(%) = 환각 사례로 판정된 응답 건수 / 전체 테스트 응답 건수 × 100",                          # 수행계획서 문구
                data="질의문 50건 + 정답 조문셋 50건 + 법령·조례 조문 DB"),
    "AI6": dict(no="2-6", name="[AI 6] 자연어 기반 지도 제어 기능", metric="자연어 기반 지도 제어 정확도",
                target=90.0, cmp=">=", log="command_log_ai6.jsonl", dec=1, cmd=True,
                formula="[AI 6] 명령 실행 정확도(%) = 정상 수행 건수 / 전체 테스트 건수 × 100  (정상 수행 = ① 의도 ② 파라미터 ③ 지도 제어 모두 일치)",
                data="자연어 지도 제어 명령 50건 (공간규제 분석 15 / 도로 접근성 분석 15 / 산지ㆍ농지 규제 분석 20)",
                types=["공간규제 분석", "도로 접근성 분석", "산지ㆍ농지 규제 분석"],
                causes=["의도 해석 오류", "핵심 파라미터 추출 오류", "GIS 지도 제어 실행 오류"]),
    "AI7": dict(no="2-7", name="[AI 7] 자연어 기반 3D 시뮬레이션 명령 처리 기능", metric="자연어 기반 3D 시뮬레이션 명령 정확도",
                target=90.0, cmp=">=", log="command_log_ai7.jsonl", dec=1, cmd=True,
                formula="[AI 7] 3D 생성 및 분석 정확도(%) = 기준 충족 건수 / 전체 평가 건수 × 100  (기준 충족 = ① 의도 ② 파라미터 ③ 3D 생성·실행 모두 일치)",
                data="자연어 3D 시뮬레이션 명령 50건 (기본 제어 15 / 경사도 5 / 토공량 5 / 지형 생성 5 / 가상건물 5 / 일조 7 / 조망 8)",
                types=["3D 지도 기본 제어", "필지 경사도 분석", "필지 토공량 산출", "지형 생성", "가상건물 생성",
                       "일조 시뮬레이션 실행", "조망 시뮬레이션 실행"],
                causes=["의도 해석 오류", "핵심 파라미터 추출 오류", "3D 시뮬레이션 명령 실행 오류"]),
}
TYPE2 = ["허가 가능", "허가 불가능"]
UNDECIDED = "판단불가"


def trunc2(x):
    """소수점 셋째자리 이하 버림 (예: 94.5678 → 94.56)"""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return math.floor(float(x) * 100 + 1e-9) / 100


def pct(n, d, dec=2):
    """백분율. dec=2 → 소수점 셋째자리 이하 버림(AI1~5), dec=1 → 둘째자리 이하 버림(AI6·7)"""
    if not d:
        return None
    k = 10 ** dec
    return math.floor(n / d * 100 * k + 1e-9) / k


def passed(item, value):
    if value is None:
        return None
    t = SPEC[item]["target"]
    return value >= t if SPEC[item]["cmp"] == ">=" else value <= t


def sha256(path):
    if not path or not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_table(path):
    """CSV(UTF-8/CP949) 또는 XLSX"""
    if path is None or not os.path.exists(path):
        return None
    if str(path).lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(path, dtype=str)
    else:
        for enc in ("utf-8-sig", "cp949", "utf-8"):
            try:
                df = pd.read_csv(path, dtype=str, encoding=enc)
                break
            except UnicodeDecodeError:
                continue
    df.columns = [str(c).strip() for c in df.columns]
    df = df.fillna("")
    return df[(df.astype(str).apply(lambda s: s.str.strip()) != "").any(axis=1)].reset_index(drop=True)   # 엑셀 수식 빈 행 제외


def type2(v):
    """판단 유형 2종 (조건부 가능 = 허가 가능, 단서 조건은 부가 정보)"""
    s = re.sub(r"\s", "", str(v or "")).lower()
    if not s:
        return UNDECIDED
    if s in ("impossible",) or any(k in s for k in ("불가", "불허")):
        return "허가 불가능"
    if s in ("possible", "conditional") or any(k in s for k in ("가능", "허가", "허용", "조건부")):
        return "허가 가능"
    return UNDECIDED


def split_list(v):
    """'a; b, c' / "['a','b']" / JSON 배열 → 리스트"""
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "[]"):
        return []
    if s.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(s.replace("'", '"').replace("‘", '"').replace("’", '"'))]
        except Exception:                                                    # noqa: BLE001
            s = s.strip("[]")
    return [x.strip().strip("'\"‘’") for x in re.split(r"[;,\n]", s) if x.strip().strip("'\"‘’")]


# ---------------------------------------------------------------- 조문 정규화 (법령ID.조.항, 호·목 제외)
_SHORT = {}
for _abbr, _full in LAW_ALIAS.items():
    _k = re.sub(r"\s", "", _full)
    if _k not in _SHORT or len(_abbr) < len(_SHORT[_k]):
        _SHORT[_k] = re.sub(r"\s", "", _abbr)
_SHORT.update({"국토의계획및이용에관한법률": "국토계획법", "산업집적활성화및공장설립에관한법률": "산업집적법",
               "산업입지및개발에관한법률": "산업입지법"})


def law_id(canon):
    """정식명(공백 제거) → 짧은 법령ID (표시용). 시행령·시행규칙은 접미사 유지"""
    for full, short in sorted(_SHORT.items(), key=lambda kv: -len(kv[0])):
        if canon.startswith(full):
            return short + canon[len(full):]
    return canon


_ART = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?(?:\s*제?\s*(\d+)\s*항)?|별표\s*(\d+)(?:\s*의\s*(\d+))?")
_LAWNAME = re.compile(r"[가-힣A-Za-z0-9·\s]*?(?:법률|법|조례|규칙|령)(?:\s*시행령|\s*시행규칙)?(?=[^가-힣]|$)")


def _law_in(seg, prev):
    seg = re.sub(r"제\s*\d+\s*(?:호|목)|(?<![가-힣])[가-힣]목", " ", seg.replace("같은 법", "같은법"))
    for part in reversed(re.split(r"[·—,()\-\[\]]", seg)):
        names = [m.group(0).strip() for m in _LAWNAME.finditer(part) if m.group(0).strip()]
        if names:
            n = names[-1]
            if n in ("시행령", "시행규칙", "영", "규칙") or n.startswith("같은법"):     # 약식 표기: '영' = 앞 법령의 시행령, '규칙' = 시행규칙
                base = re.sub(r"(시행령|시행규칙)$", "", prev or "")
                suf = "시행규칙" if "규칙" in n else "시행령"
                return base + suf if base else None
            return norm_law(n)
    if prev and re.search(r"(?<![가-힣])영(?![가-힣])", seg or ""):          # '(영)', '·영' 등 → 앞 법령의 시행령 (예: '택지개발촉진법 제6조(영), 제2조')
        return re.sub(r"(시행령|시행규칙)$", "", prev) + "시행령"
    return prev


def parse_citations(text):
    """문장 → {(법령정식명, 조, 항|None)}.  '건축법 제44조 제1항' → ('건축법','44','1')
    '국토계획법 제75조의3제1항제2호' → ('국토의계획및이용에관한법률','75의3','1')
    정규화 표기 'A.44.1' / 'A.2' 도 그대로 인식"""
    out = set()
    if not text:
        return out
    items = text if isinstance(text, (list, tuple, set)) else [text]
    for it in items:
        s = str(it)
        m = re.fullmatch(r"\s*([^.\s][^.]*?)\.([0-9]+(?:의[0-9]+)?|별표[0-9]+)(?:\.([0-9]+))?\s*", s)
        if m and not re.search(r"제\s*\d", s):                            # 이미 정규화된 표기 ("개발제한구역법.12.1"처럼 법령명에 '제'가 있어도 인식)
            out.add((norm_law(m.group(1)), m.group(2), m.group(3)))
            continue
        chunks = re.findall(r"\[([^\]]+)\]", s) or [s]
        prev = None
        for ch in chunks:
            pos = 0
            for mm in _ART.finditer(ch):
                law = _law_in(ch[pos:mm.start()], prev)
                pos = mm.end()
                if not law:
                    continue
                jo, ui, hang, bt, bui = mm.groups()
                art = (f"{jo}의{ui}" if ui else jo) if jo else (f"별표{bt}의{bui}" if bui else f"별표{bt}")
                out.add((law, art, hang if jo else None))
                prev = law
    return out


def fmt_cite(c):
    law, art, hang = c
    return f"{law_id(law)}.{art}" + (f".{hang}" if hang else "")


def cite_match(a, g):
    """AI 조문 a가 정답 조문 g와 같은가: 법령·조 일치 + 항 일치 (어느 한쪽이 항을 생략했으면 조 단위로 비교)"""
    return a[0] == g[0] and a[1] == g[1] and (g[2] is None or a[2] is None or a[2] == g[2])


def cite_set_equal(ai, gold):
    """정규화 기준 집합 일치(순서 무시): 정답 각각이 AI에 있고, AI 각각이 정답 중 하나와 일치"""
    if not ai and not gold:
        return True
    return all(any(cite_match(a, g) for a in ai) for g in gold) and \
        all(any(cite_match(a, g) for g in gold) for a in ai)


def cite_overlap(ai, gold):
    return any(cite_match(a, g) for a in ai for g in gold)


# ---------------------------------------------------------------- AIPS 응답 → 성과지표 출력 필드
def _events_dict(events):
    st = {"start": {}, "stages": {}, "diag": None, "parcel": None, "intent": None, "reply": "", "end": {}}
    for ev, d in events:
        if ev == "orc.start":
            st["start"] = d
        elif ev == "orc.end":
            st["end"] = d
        elif ev == "orc.response":
            k = d.get("event")
            dd = d.get("data") or {}
            if k == "stage" and dd.get("status") == "done":
                st["stages"][dd.get("id")] = dd
            elif k == "diagnosis":
                st["diag"] = dd
            elif k == "parcel":
                st["parcel"] = dd
            elif k == "intent":
                st["intent"] = dd.get("intent")
            elif k == "reply":
                st["reply"] = str(dd.get("reply", ""))
    return st


def rulesets(diag):
    """적용 룰셋 ID 집합: 조건 룰(rule_id) + 용도지역 매트릭스(MATRIX:용도지역코드:시설코드) + overlay"""
    j = (diag or {}).get("judgment") or {}
    ids = {c.get("rule_id") for c in j.get("conditions") or [] if c.get("rule_id")}
    mg = j.get("matrix_gate") or {}
    for z in mg.get("zone_results") or []:
        if z.get("zone_cd"):
            ids.add(f"MATRIX:{z.get('zone_cd')}:{z.get('fclty_cd', '')}")
    for o in mg.get("overlay_rules") or []:
        ids.add("OVERLAY:" + (o.get("id") or o.get("rule_id") or json.dumps(o, ensure_ascii=False)[:40]
                              if isinstance(o, dict) else str(o)))
    return sorted(i for i in ids if i)


def cond_texts(j, rep):
    """[AI 4] 시스템이 판정에서 밝힌 조건 문장: 실패 조건·근거 조건(natural), 단서가 붙은 지역·지구 판정 문장, 불허 사유"""
    out = []
    ev = {e.get("rule_id"): e for e in j.get("evidence") or [] if isinstance(e, dict)}
    for c in j.get("conditions") or []:
        if isinstance(c, dict) and c.get("status") == "fail":
            out.append(str((ev.get(c.get("rule_id")) or {}).get("natural") or c.get("natural") or c.get("source") or ""))
    for z in (j.get("matrix_gate") or {}).get("zone_results") or []:
        if isinstance(z, dict) and str(z.get("in_combine")) == "True" and z.get("decision_kind") and z.get("decision"):
            out.append(str(z["decision"])[:300])
    out += [str(x) for x in ((rep or {}).get("band") or {}).get("deny_reasons") or []]
    return " / ".join(dict.fromkeys(x for x in out if x.strip()))


def proc_set(xs):
    """[AI 4] 필요 행정절차 비교용 정규화: 괄호 설명((신축)·(대상 시)·[농지]) 제거, '·'로 붙은 절차 분리, 절차가 아닌 '사전검토' 제외"""
    out = set()
    for x in xs or []:
        for p in re.split(r"[·;]", str(x)):
            p = re.sub(r"\s+", "", re.sub(r"\([^)]*\)|\[[^\]]*\]", "", p))
            if p and p not in ("사전검토", "-"):
                out.add(p)
    return out


def map_response(events, base=None):
    """성과지표 출력 형식: 판단유형·근거조문·적용법령·적용룰셋·보완사항·필요행정절차·설명문 + 6단계 로그"""
    from qa_runner import extract                                           # 지연 import (순환 방지)
    base = base or extract(events)
    st = _events_dict(events)
    diag = st["diag"] or {}
    j = diag.get("judgment") or {}
    v = j.get("verdict") or diag.get("status")
    decisive = parse_citations(str(base.get("AI근거") or ""))
    cond_src = parse_citations([c.get("source") for c in j.get("conditions") or [] if c.get("source")])
    # [AI 4] 단서 조건 문장(matrix_gate.zone_results[].decision_parts[].원문·text)의 조문 — 예: 농공단지 입주자격 '산업집적법 시행령 제6조 제1항'
    for z in (j.get("matrix_gate") or {}).get("zone_results") or []:
        if isinstance(z, dict) and str(z.get("in_combine")) == "True":
            for dp in z.get("decision_parts") or []:
                if isinstance(dp, dict):
                    cond_src |= parse_citations(re.findall(r"〔([^〕]+)〕", str(dp.get("원문") or dp.get("text") or "")))
    # [AI 4] 판정 기준 "정답 근거 조문 전부 포함"(2026-11-11) → 응답이 제시한 판정 근거를 빠짐없이 모음:
    #   판정 검토 단계의 법령 목록(stage.detail.laws), 지역·지구 판정 근거(zone_results[].source), 중첩 규칙 근거(overlay_rules[].basis)
    for s_ in st["stages"].values():
        if s_.get("id") in CORE_STAGES and s_.get("status") == "done":
            cond_src |= parse_citations([str(x).split(" — ")[0] for x in (s_.get("detail") or {}).get("laws") or []])
    mg = j.get("matrix_gate") or {}
    for z in mg.get("zone_results") or []:
        if isinstance(z, dict) and str(z.get("in_combine")) == "True" and z.get("source"):
            cond_src |= parse_citations(str(z["source"]).split(" — ")[0])
    for o in mg.get("overlay_rules") or []:
        if isinstance(o, dict) and o.get("basis"):
            cond_src |= parse_citations(str(o["basis"]))
    laws = sorted({law_id(c[0]) for c in decisive})
    rep = diag.get("report") or {}
    recs = [r.get("question") for r in diag.get("recommendations") or [] if isinstance(r, dict) and r.get("question")]
    proc = [p for p in str(base.get("AI필요허가") or "").split("; ") if p]
    civil = (st["stages"].get("civil_procedure") or {}).get("detail") or {}
    for step in re.findall(r"\*\*[①-⑨]\s*([^*]+?)\*\*", civil.get("markdown") or ""):
        step = re.sub(r"\s*\(.*?\)\s*", "", step).strip()
        if step and step not in proc:
            proc.append(step)
    return {
        "판단유형": type2(v) if v else UNDECIDED,
        "원판정": v or base.get("AI판정"),
        "근거조문": sorted(fmt_cite(c) for c in decisive),
        "단서조건근거조문": sorted(fmt_cite(c) for c in (cond_src | decisive)),
        "적용법령": laws,
        "적용룰셋": rulesets(diag),
        "보완사항": " / ".join(recs),
        "단서조건문장": cond_texts(j, rep),            # [AI 4] 보완사항 비교용: 시스템이 판정에서 밝힌 조건 문장
        "필요행정절차": proc,
        "설명문": (rep.get("card") or {}).get("summary") or (rep.get("band") or {}).get("line") or st["reply"],
        "결론문": (rep.get("band") or {}).get("line") or "",
        "불허사유": (rep.get("band") or {}).get("deny_reasons") or [],
        "위반조건": [c.get("source") for c in j.get("conditions") or [] if c.get("status") == "fail"],
        "build": st["start"].get("build"), "contract_version": st["start"].get("contract_version"),
        **{k: "; ".join(v) for k, v in versions_in(json.dumps(diag, ensure_ascii=False)).items()},
        "trace_id": st["end"].get("trace_id"),
    }


# 6단계 판단 프로세스 (성과지표 2-3, 의뢰자제시기준규격서 v1.51 32쪽)
STEPS = ["① 질의 파싱", "② 대상 필지(PNU) 조회", "③ 토지특성(지목, 면적) 조회", "④ 적용 법령·조례 검색",
         "⑤ 룰셋 적용", "⑥ 최종 판단 도출"]
CORE_STAGES = {"zone_use", "development_permit", "land_conversion", "building_permit"}   # 판정용 검토 단계 (수행계획서 5단계: 허가기준·수치기준 판정) — 이 밖의 단계(민원절차 안내 등)는 ⑨ 후속 기능
STEP_FIELDS = ["실행", "데이터소스", "조회키", "결과건수", "적용룰셋", "검색법령", "중간판정값"]


TOOL_LABEL = {                                   # AIPS 도구 호출(작업 이름) → 수행계획서 기능 이름 (180~182쪽, 근거·요약 표시용)
    "INTEGRATED_LOCATION_SEARCH": "위치 식별", "SELECT": "필지 선택",
    "AREA": "면적 산출", "LAYER_FIND": "레이어 제어(걸친 용도지역·지구)",
    "ATTR": "필지 정보 수집(지목·면적·용도지역)", "CONTACT_SUMMARY": "접도 조건 수집", "ROAD_PLAN_GEOM": "인접 도로 수집",
    "FOREST_CONVERSION": "규제 정보 수집(산지전용)", "FARMLAND_CONVERSION": "규제 정보 수집(농지전용)",
    "VALUE": "경사도 산출",
}


def _env_steps(parcel):
    """parcel.envelopes(도구 호출 기록) → 단계 분류"""
    out = {2: [], 3: [], 5: []}
    for en in (parcel or {}).get("envelopes") or []:
        args = en.get("args") or {}
        plan = ((args.get("_metadata") or {}).get("plan") or [{}])[0]
        tool = plan.get("tool") or en.get("tool")
        pa = plan.get("args") or {}
        op = pa.get("action") or pa.get("operation") or ""
        pl = en.get("payload") or {}
        rs = pl.get("resolve") or {}
        facts = rs.get("facts") or {}
        src = f"{en.get('callee')}/{tool}:{op}".rstrip(":")
        key = pa.get("query") or pa.get("pnu") or pa.get("featureRef") or ""
        rec = dict(src=src, key=str(key), status=en.get("status"), facts=facts, op=str(op).upper(), cid=en.get("call_id") or "")
        if rec["op"] in ("INTEGRATED_LOCATION_SEARCH", "SELECT"):
            out[2].append(rec)
        elif rec["op"] in ("FOREST_CONVERSION", "FARMLAND_CONVERSION", "LAYER_FIND") or "regulation" in src:
            out[5].append(rec)
        else:
            out[3].append(rec)
    return out


def _count(facts):
    for k in ("결과수", "후보수", "필지수", "규제건수", "계획도로수", "접도면수"):
        if k in facts:
            return facts[k]
    return 1 if facts else 0


def step_log(events, base=None):
    """응답 → 6단계 로그 [{단계번호, 단계명, 실행, 데이터소스, 조회키, 결과건수, 적용룰셋, 검색법령, 중간판정값, 근거}]
    값은 모두 AIPS 응답 스트림(SSE)에 실려 온 시스템 처리 기록에서 가져옴 — 근거 = 응답 원문(raw)의 이벤트 번호·필드"""
    st = _events_dict(events)
    at = {"intent": None, "parcel": None, "diagnosis": None, "laws": []}       # 근거 이벤트 번호 (0부터, raw 순서)
    for i, (ev, d) in enumerate(events):
        k = (d or {}).get("event") if ev == "orc.response" else None
        if k in ("intent", "parcel", "diagnosis"):
            at[k] = i if k != "intent" or at[k] is None else at[k]
        if k == "stage" and ((d.get("data") or {}).get("detail") or {}).get("laws"):
            at["laws"].append(i)
    def ev_(i, what):                          # "응답 원문 26번 항목(최종 진단 결과) › 최종 판정" — 번호 = 원문 JSON 왼쪽 번호
        return f"응답 원문 {i}번 항목({what})" if i is not None else ""

    def tools(recs):                           # 도구 호출을 한국어 이름으로, 같은 종류는 묶고 [ ] 안에 원문의 call_id
        g = {}
        for r in recs:
            g.setdefault(TOOL_LABEL.get(r["op"], r["src"].split("/")[-1]), []).append(r.get("cid") or "?")
        return ", ".join(f"{k}[{', '.join(v)}]" for k, v in g.items())
    diag = st["diag"] or {}
    j = diag.get("judgment") or {}
    env = _env_steps(st["parcel"])
    fm = diag.get("facility_match") or {}
    mg = j.get("matrix_gate") or {}
    loc = next((r for r in env[2] if r["op"] == "INTEGRATED_LOCATION_SEARCH"), None)
    attr = next((r for r in env[3] if "attr" in r["src"].lower() or r["op"] == "ATTR"), None)
    laws = set()
    for s_ in st["stages"].values():
        laws |= parse_citations((s_.get("detail") or {}).get("laws") or [])
    regs = set()
    for r in env[5]:
        for x in r["facts"].get("규제") or []:
            regs.add(re.split(r"\s+[\d,.]+㎡", str(x))[0])
        for x in r["facts"].get("레이어") or []:
            regs.add(str(x))
    if attr:
        regs |= {str(x) for x in attr["facts"].get("용도지역지구") or []}
    land = env[3] + env[5]                     # ③ 토지특성 = 필지 정보(지목·면적·용도지역) + 규제 정보 조회
    rows = [
        dict(실행=bool(st["intent"] or fm), 데이터소스="orchestrator:intent",                       # ① 질의 파싱
             조회키="", 결과건수=1 if st["intent"] else 0, 적용룰셋=[], 검색법령=[],
             중간판정값=f"{st['intent'] or ''}|{fm.get('item') or fm.get('asked') or ''}"),
        dict(실행=bool(env[2]), 데이터소스=";".join(sorted({r["src"] for r in env[2]})),               # ② PNU 조회
             조회키=(loc or {}).get("key", ""), 결과건수=_count((loc or {}).get("facts", {})),
             적용룰셋=[], 검색법령=[], 중간판정값=str((loc or {}).get("facts", {}).get("PNU", ""))),
        dict(실행=bool(land), 데이터소스=";".join(sorted({r["src"] for r in land})),                   # ③ 토지특성 조회
             조회키=";".join(sorted({r["key"] for r in land})), 결과건수=len(land),
             적용룰셋=[], 검색법령=[],
             중간판정값=f"지목={(attr or {}).get('facts', {}).get('지목', '')}|지역지구={';'.join(sorted(regs))}"),
        dict(실행=bool(laws), 데이터소스="stage.detail.laws", 조회키="", 결과건수=len(laws),            # ④ 법령·조례 검색
             적용룰셋=[], 검색법령=sorted(fmt_cite(c) for c in laws), 중간판정값=""),
        dict(실행=bool(j) or bool(mg), 데이터소스="judgment.conditions;matrix_gate", 조회키="",       # ⑤ 룰셋 적용 (용도지역 허용 판정 포함)
             결과건수=int(j.get("rules_applied") or 0), 적용룰셋=rulesets(diag), 검색법령=[],
             중간판정값=";".join(f"{z.get('zone')}:{z.get('sym')}" for z in mg.get("zone_results") or [])),
        dict(실행=bool(j.get("verdict")), 데이터소스="judgment.verdict", 조회키="", 결과건수=1 if j.get("verdict") else 0,
             적용룰셋=[], 검색법령=[], 중간판정값=type2(j.get("verdict")) if j.get("verdict") else UNDECIDED),   # ⑥ 최종 판단
    ]
    basis = [ev_(at["intent"], "질의 해석 결과"),
             ev_(at["parcel"], "필지 조회 결과") + f" › {tools(env[2])}" if env[2] else "",
             ev_(at["parcel"], "필지 조회 결과") + f" › {tools(land)}" if land else "",
             (f"응답 원문 {', '.join(map(str, sorted(set(at['laws']))))}번 항목(단계별 검토 결과) › 관련 법령" if at["laws"] else ""),
             ev_(at["diagnosis"], "최종 진단 결과") + " › 적용 규칙·용도지역 허용 판정" if at["diagnosis"] is not None else "",
             ev_(at["diagnosis"], "최종 진단 결과") + " › 최종 판정" if at["diagnosis"] is not None else ""]
    return [dict(단계번호=i + 1, 단계명=STEPS[i], **r, 근거=basis[i]) for i, r in enumerate(rows)]


EVENT_KIND = {"intent": "질의 해석 결과", "parcel": "필지 조회 결과", "stage": "단계별 검토 결과",
              "diagnosis": "최종 진단 결과", "reply": "답변 문장"}
STAGE_STATUS = {"pending": "대기", "running": "진행 중", "done": "완료"}
PLAN_VERDICT = {"possible": "승인 가능", "conditional": "조건부 가능", "impossible": "부적합",   # 수행계획서 4종 판단 유형
                "needs_review": "검토 필요", "unknown": "검토 필요"}
PLAN_STEP = {1: "1. 행위·위치·규모·용도 추출", 2: "2. PNU 기준 토지특성·용도지역·규제구역·지형 조회",
             3: "3. 적용 법령·조례·지침·고시 후보 검색", 5: "5. Rule Engine 기반 필수 허가기준 충족 판정",
             8: "8. 4종 최종 분류", 9: "9. 후속 기능모듈 분기 및 기록"}       # 수행계획서 167~168쪽 처리 흐름
PLAN_NAME = [("토지 규제 · 분할 에이전트", "토지특성 통합수집 에이전트"), ("도로 분석 에이전트", "토지특성 통합수집 에이전트"),
             ("공간 오케스트레이터", "통합 오케스트레이터"), ("지도 이동 에이전트", "지도제어 에이전트"),
             ("토지 정보 에이전트", "토지특성 통합수집 에이전트"), ("공간 분석 에이전트", "지도제어 에이전트"),
             ("3D 에이전트", "3D 시뮬레이션 에이전트")]
# ↑ 진행 메시지 속 이름 → 수행계획서 명칭 (179~183쪽): 접도·인접 도로·규제 정보 = 토지특성 통합수집(A2),
#   2D 공간 분석·레이어 제어 = 지도제어(A1, 20쪽), 공간분석 Vision(A3)은 영상 분석 전용이라 AIPS 응답에 해당 없음
PLAN_SUB = {"spatial": "지도제어·공간분석 에이전트", "permit_review": "사전진단 추론 에이전트", "OK": "정상"}
INTENT_LABEL = {"permit_review": "사전진단(인허가 가능 여부 판단)", "map_control": "지도제어", "map3d_analysis": "3D 시뮬레이션",
                "land_characteristics": "토지특성 조회", "law_text": "법령 조문 조회"}
TRACE_STEP = [("행위제한", 5), ("가능한지 확인", 5), ("건폐율", 5), ("관계 법령", 3), ("설명을 쓰고", 9),
              ("건축 가능 여부를 검토", 1), ("질문을 이해", 1), ("에이전트", 2), ("답을 정리", 2)]


def plan_terms(text):
    for a, b in PLAN_NAME:
        text = text.replace(a, b)
    return text


def event_rows(raw):
    """응답 원문(raw: [{event, data}, …]) → 번호별 요약 [{번호, 종류, 수행계획서 단계, 내용}] — 번호는 원문 JSON 의 0, 1, 2 …
    이름·판단 유형은 수행계획서 용어로 바꿔 표시 (원문은 그대로)"""
    rows = []
    for i, e in enumerate(raw or []):
        ev, d = e.get("event"), e.get("data") or {}
        step = None
        if ev == "orc.start":
            kind, txt = "처리 시작", f"시스템 빌드 {d.get('build', '-')} · 응답 규격 {d.get('contract_version', '-')}"
        elif ev == "orc.end":
            st = d.get("sub_status") or {}
            kind, txt = "처리 종료", ", ".join(f"{PLAN_SUB.get(k, k)} {PLAN_SUB.get(v, v)}" for k, v in st.items()) or "-"
        elif ev == "orc.trace":
            msg = str(d.get("message") or "-")
            kind, txt = "진행 메시지", plan_terms(msg)
            step = next((n for k, n in TRACE_STEP if k in msg), None)
        elif ev == "orc.response":
            k, x = d.get("event"), d.get("data") or {}
            kind = EVENT_KIND.get(k, f"결과 ({k})")
            if k == "intent":
                it = x.get("intent", "-")
                step, txt = 1, f"질의를 '{INTENT_LABEL.get(it, it)}'(으)로 이해"
            elif k == "parcel":
                ens = x.get("envelopes") or []
                g = {}
                for en in ens:
                    plan = (((en.get("args") or {}).get("_metadata") or {}).get("plan") or [{}])[0]
                    op = str((plan.get("args") or {}).get("action") or (plan.get("args") or {}).get("operation") or "").upper()
                    g.setdefault(TOOL_LABEL.get(op, op or en.get("tool")), []).append(en.get("call_id", "?"))
                step, txt = 2, f"조회 {len(ens)}건: " + ", ".join(f"{n}[{', '.join(c)}]" for n, c in g.items())
            elif k == "stage":
                v = x.get("verdict")
                step = 5 if x.get("id") in CORE_STAGES else 9
                txt = (f"{str(x.get('title') or x.get('id')).replace(' 대기', '').replace(' 중…', '').replace(' 완료', '')} "
                       f"— {STAGE_STATUS.get(x.get('status'), x.get('status'))}" + (f" (판정: {PLAN_VERDICT.get(v, v)})" if v else ""))
            elif k == "diagnosis":
                v = (x.get("judgment") or {}).get("verdict")
                recs = [r for r in x.get("recommendations") or [] if isinstance(r, dict)]
                step = 8
                txt = (f"4종 최종 분류: {PLAN_VERDICT.get(v, v) if v else '-'} · 근거 법령·적용 규칙"
                       + (f" · 조건부 대안 {len(recs)}건" if recs else "") + (" · 사전진단 보고서" if x.get("report") else ""))
            elif k == "reply":
                txt = str(x.get("reply") or "")[:80]
            else:
                txt = ", ".join(list(x.keys())[:5])
        else:
            kind, txt = ev or "-", ""
        rows.append({"번호": i, "종류": kind, "수행계획서 단계": PLAN_STEP.get(step, "–"), "내용": txt})
    return rows


def labeled_raw(raw):
    """원문 JSON 의 번호 옆에 종류 이름을 붙임: {'0 · 시작': {...}, '14 · 필지 조회 결과': {...}}"""
    return {f"{r['번호']} · {r['종류']}": e for r, e in zip(event_rows(raw), raw or [])}


def diff_detail(base, other, diffs, err=None):
    """불일치 필드별로 1회차 값 → 이번 회차 값 (집합은 추가·빠짐)"""
    if not diffs:
        return "-"
    out = []
    by_name = {x["단계명"]: x for x in other}
    key = {"실행": "실행", "데이터소스": "데이터소스", "조회키": "조회키", "결과건수": "결과건수",
           "적용룰셋": "적용룰셋", "검색법령": "검색법령", "최종판단": "중간판정값"}
    for stage, f in diffs:
        if f == "오류":
            out.append(f"{stage}: {err or '응답 없음'}")
            continue
        if f == "실행순서":
            seq = lambda L: "→".join(str(x["단계번호"]) for x in L if x["실행"])
            out.append(f"실행 순서: 1회차 {seq(base)} / 이번 {seq(other)}")
            continue
        a = next((x for x in base if x["단계명"] == stage), {}).get(key[f])
        b = by_name.get(stage, {}).get(key[f])
        if f in ("적용룰셋", "검색법령"):
            add, miss = sorted(set(b or []) - set(a or [])), sorted(set(a or []) - set(b or []))
            out.append(f"{stage} {f}: " + " / ".join(x for x in (f"추가 {', '.join(add)}" if add else "",
                                                               f"빠짐 {', '.join(miss)}" if miss else "") if x))
        else:
            out.append(f"{stage} {f}: 1회차 {a} → 이번 {b}")
    return " / ".join(out)


def compare_steps(base, other):
    """두 회차 6단계 로그 비교 → 불일치 단계 목록 [(단계명, 필드)]  (규격서 v1.51 비교 대상 필드만)
    단계 실행 순서(①~⑥ 실행 여부·순서), 조회 데이터소스, 조회 키·결과 건수, 적용 룰셋 ID 집합(⑤),
    검색 법령 집합(④), 최종 판단 유형(⑥) — 타임스탬프·소요시간·요청 ID·로그 시퀀스 번호는 제외"""
    diffs = []
    for a, b in zip(base, other):
        n = a["단계번호"]
        for f in ("실행", "데이터소스", "조회키", "결과건수"):
            if str(a.get(f)) != str(b.get(f)):
                diffs.append((a["단계명"], f))
        if n == 5 and set(a["적용룰셋"]) != set(b["적용룰셋"]):
            diffs.append((a["단계명"], "적용룰셋"))
        if n == 4 and set(a["검색법령"]) != set(b["검색법령"]):
            diffs.append((a["단계명"], "검색법령"))
        if n == 6 and str(a.get("중간판정값")) != str(b.get("중간판정값")):
            diffs.append((a["단계명"], "최종판단"))
    if [x["단계번호"] for x in base if x["실행"]] != [x["단계번호"] for x in other if x["실행"]]:
        diffs.append(("단계 실행 순서", "실행순서"))
    return diffs


# ---------------------------------------------------------------- 로그 읽기
def read_log(run_dir, item):
    p = os.path.join(run_dir, SPEC[item]["log"])
    if not os.path.exists(p):
        return pd.DataFrame()
    rows = []
    with open(p, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    key = "질의ID" if "질의ID" in df.columns else "사례ID"
    df[key] = df[key].astype(str)
    df["회차"] = df["회차"].astype(int)
    for c in LIST_FIELDS:                        # 오류 행(연결 끊김 등)은 목록 필드가 없음 → 빈 목록으로
        if c in df.columns:
            df[c] = df[c].map(lambda v: v if isinstance(v, list) else [])
    return df.drop_duplicates([key, "회차"], keep="last").reset_index(drop=True)   # 이어서 실행 중복 제거


CUT_OFF = "응답 중단 (최종 판정 없이 응답이 끝남)"


def error_text(rec):
    """로그 1행의 오류 설명: 통신 오류 메시지, 또는 판정 없이 끝난 응답 → '응답 중단', 정상이면 ''"""
    e = rec.get("오류")
    if isinstance(e, str) and e.strip():
        return e
    if str(rec.get("원판정") or "") == "응답없음" or (rec.get("판단유형") == UNDECIDED and "원판정" not in rec):
        return CUT_OFF
    return ""


def error_detail(rec, run_dir=None):
    """오류 설명 + 발생 위치: (종류, 위치) — 정상이면 ('', '')
    통신 오류는 예외 종류로, 응답 중단은 저장된 응답 원문의 마지막 진행 단계·메시지로 위치를 찾음"""
    e = error_text(rec)
    if not e:
        return "", ""
    if e != CUT_OFF:
        kind = ("연결 끊김 — 서버가 연결을 끊음" if "Connection" in e else
                "시간 초과 — 제한 시간 안에 응답 없음" if "Timeout" in e or "timed out" in e else
                "서버 오류 — " + e[:60] if "HTTPError" in e else "통신 오류 — " + e[:60])
        return kind, "질의 전송 직후 (응답을 받기 전)"
    raw = os.path.join(run_dir, "raw", f"{rec.get('사례ID') or rec.get('질의ID')}_{int(rec.get('회차') or 1)}.json") if run_dir else ""
    if not raw or not os.path.exists(raw):
        return "응답 중단 — 최종 판정 없이 응답이 끝남", "-"
    with open(raw, encoding="utf-8") as fh:
        events = json.load(fh)
    stage, msg = "", ""
    for ev in events:
        d = ev.get("data") or {}
        if ev.get("event") == "orc.trace" and d.get("message"):
            msg = d["message"]
        elif ev.get("event") == "orc.response" and d.get("event") == "stage":
            s = d.get("data") or {}
            stage = str(s.get("title") or "").replace("…", "").strip()
    return "응답 중단 — 최종 판정 없이 응답이 끝남", f"{stage or '-'} 단계에서 끊김 (마지막 메시지: {msg or '-'})"


LIST_FIELDS = ["근거조문", "단서조건근거조문", "적용법령", "적용룰셋", "필요행정절차", "불허사유", "위반조건", "단계로그"]


def gold_cites(v):
    return parse_citations(split_list(v))


# ---------------------------------------------------------------- [AI 1]
def ai_basis(r):
    """AI가 밝힌 판단 근거 한 줄: 불허 사유 → 위반 조건 → 결론문 → 설명문 순"""
    for k in ("불허사유", "위반조건"):
        v = r.get(k)
        if isinstance(v, (list, tuple)) and [x for x in v if x]:
            return "; ".join(str(x) for x in v if x)
    for k in ("결론문", "설명문"):
        v = str(r.get(k) or "").strip()
        if v and v != "nan":
            return v[:200]
    return "응답에 근거 설명 없음"


def mismatch_reason(gv, pv, r, gc=None, pc=None, gl=None, pl=None):
    """판단·근거 조문·적용 법령 불일치 사유 문장"""
    why = []
    if gv != pv:
        if pv == UNDECIDED:
            why.append(f"판단 불일치 — 정답 {gv}, AI 판단 없음({r.get('원판정') or r.get('오류') or '판정 미제시'})")
        else:
            why.append(f"판단 불일치 — 정답 {gv}, AI {pv}. AI 판단 근거: {ai_basis(r)}")
    if gc and pc is not None and not cite_overlap(pc, gc):            # AI1 보조 지표: 정답 조문과 하나라도 일치하면 일치 (2026-11-10)
        only_g = sorted(fmt_cite(c) for c in gc if not any(cite_match(p, c) for p in pc))
        only_p = sorted(fmt_cite(c) for c in pc if not any(cite_match(c, g) for g in gc))
        why.append("근거 조문 불일치(정답 조문과 하나도 일치하지 않음) — " + " / ".join(x for x in (
            f"정답에만 있음: {', '.join(only_g)}" if only_g else "", f"AI에만 있음: {', '.join(only_p)}" if only_p else "") if x))
    if gl and pl is not None and not gl & pl:                       # 정답 법령과 하나라도 일치하면 일치
        why.append(f"적용 법령 불일치(정답 법령과 하나도 일치하지 않음) — 정답 {', '.join(sorted(law_id(x) for x in gl))} / AI {', '.join(sorted(law_id(x) for x in pl)) or '없음'}")
    return " / ".join(why) or "-"


def score_ai1(log, answer):
    """판단 유형 2종 정확도 + 보조 지표(근거조문, 적용법령)"""
    a = answer.set_index("사례ID")
    rows = []
    for _, r in log.iterrows():
        cid = str(r["사례ID"])
        g = a.loc[cid] if cid in a.index else None
        gv = type2(g.get("판단유형")) if g is not None else UNDECIDED
        pv = r.get("판단유형") or UNDECIDED
        gc, pc = gold_cites(g.get("근거조문")) if g is not None else set(), parse_citations(r.get("근거조문") or [])
        gl = {norm_law(x) for x in split_list(g.get("적용법령"))} if g is not None else set()
        pl = {norm_law(x) for x in r.get("적용법령") or []}
        rows.append(dict(사례ID=cid, 질의문=r.get("질의문"), 정답_판단유형=gv, 예측_판단유형=pv, 판단_일치=gv == pv,
                         예측_적용룰셋="; ".join(r.get("적용룰셋") or []),
                         정답_근거조문="; ".join(sorted(fmt_cite(c) for c in gc)),
                         예측_근거조문="; ".join(r.get("근거조문") or []),
                         근거조문_일치=(cite_overlap(pc, gc) if gc else None),        # 보조 지표: 정답 조문과 하나라도 일치 (부분 포함 인정)
                         정답_적용법령="; ".join(split_list(g.get("적용법령"))) if g is not None else "",
                         예측_적용법령="; ".join(r.get("적용법령") or []),
                         적용법령_일치=(bool(gl & pl) if gl else None),
                         불일치사유=mismatch_reason(gv, pv, r, gc, pc, gl, pl),
                         **{c: r.get(c, "") for c in REF_COLS if c in log.columns}))
    res = pd.DataFrame(rows)
    n = len(res)
    acc = pct(res["판단_일치"].sum(), n) if n else None
    by_type = []
    for t in TYPE2:
        s = res[res["정답_판단유형"] == t]
        by_type.append(dict(판단유형=t, 정답건수=len(s), 일치=int(s["판단_일치"].sum()),
                            불일치=int((~s["판단_일치"]).sum()), **{"일치율(%)": pct(s["판단_일치"].sum(), len(s))}))
    by_type.append(dict(판단유형="계", 정답건수=n, 일치=int(res["판단_일치"].sum()) if n else 0,
                        불일치=int((~res["판단_일치"]).sum()) if n else 0, **{"일치율(%)": acc}))
    aux = []
    for name, col in [("적용 법령", "적용법령_일치"), ("근거 조문", "근거조문_일치")]:
        s = res[col].dropna() if n else pd.Series(dtype=bool)
        note = ""
        aux.append({"보조 지표": name, "대상": len(s), "일치": int(s.astype(bool).sum()), "불일치": int((~s.astype(bool)).sum()),
                    "일치율(%)": pct(s.astype(bool).sum(), len(s)), "비고": note})
    return dict(value=acc, n=n, tables={"판단 유형 2종별 일치·불일치 건수": pd.DataFrame(by_type), "보조 지표(적용 법령·근거 조문)별 일치·불일치 건수 및 일치율": pd.DataFrame(aux)},
                cases=res, confusion=pd.crosstab(res["정답_판단유형"], res["예측_판단유형"]) if n else pd.DataFrame())


VERSION_KEYS = {"룰엔진버전": "engine_version", "공간정보기준일": "stdVer"}      # AIPS 응답 원문 안의 필드명


def versions_in(text):
    """응답 원문(JSON 문자열)에서 버전 필드 값 찾기 → {룰엔진버전: '3.3.0', 공간정보기준일: '2026-04-24'}"""
    return {k: sorted(set(re.findall(rf'"{f}":\s*"([^"]+)"', text))) for k, f in VERSION_KEYS.items()}


def version_table(log, run_dir=None):
    """시험방법 (2) 동결 버전 확인 — 시스템 응답에 있는 값만 표기, 없는 항목은 '미제공'"""
    found = {c: {str(x) for x in log[c].dropna() if str(x).strip()} if c in log.columns else set()
             for c in ("build", "contract_version", *VERSION_KEYS)}
    if run_dir and not (found["룰엔진버전"] or found["공간정보기준일"]):        # 이전 실행: 저장된 응답 원문에서 찾기
        import glob
        for f in glob.glob(os.path.join(run_dir, "raw", "*.json")):
            with open(f, encoding="utf-8") as fh:
                for k, v in versions_in(fh.read()).items():
                    found[k] |= set(v)
    meta = {}
    if run_dir and os.path.exists(os.path.join(run_dir, "meta.json")):
        with open(os.path.join(run_dir, "meta.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
    meta = backfill(meta)                                                # 이전 실행: 실행 이후 바뀌지 않은 것이 확인되면 현재 조회값으로 채움
    found["모델명"] = {meta.get("모델명") or MODEL_NAME_DEFAULT}             # 응답에 없음 → 실행 시작 때 콘솔 조회값(없으면 기본값)
    found["법령DB"] = {meta["법령DB"]} if meta.get("법령DB") else set()       # 응답에 없음 → 실행 시작 때 법령 수집 관리자 조회값
    found["법령해석"] = {meta["법령해석"]} if meta.get("법령해석") else set()  # 매일 바뀌므로 실행 시작 때 기록한 값만 표시
    rows = [("시스템 빌드 번호", "build", ""), ("응답 규격 버전", "contract_version", ""),
            ("AI 사전진단 엔진 모델 버전", "모델명", f"{meta.get('모델명출처') or '기본값'} — 시스템 응답 값 아님"),
            ("룰셋(룰엔진) 버전", "룰엔진버전", "룰엔진 버전(engine_version) — 룰셋 자체 버전 필드는 없음"),
            ("법령·조례 DB 버전", "법령DB", f"{meta['법령DB출처']} — 시스템 응답 값 아님" if meta.get("법령DB")
             else "시스템 응답에 없음 (실행 이후 변경 여부를 확인할 수 없어 비워 둠)"),
            ("법령해석사례", "법령해석", "매일 04시 자동 증분 수집 — 법령·조례 DB와 별도로 갱신됨 (동결 대상 여부 확인 필요)"),
            ("용도지역·지구 표준분류 개정 기준일", "공간정보기준일",
             "응답의 stdVer — 시스템이 쓰는 용도지역·지구 표준분류 코드표의 개정 일자 (2026-04-24 개정판)")]
    infra = meta.get("인프라") or {}
    for k, v0 in INSTALL_DOC.items():                                    # 인프라 버전: 실행 당시 조회값, 없으면 설치정보 v1.1
        v, src = (infra.get(k) or ["", ""])
        found[k] = {v or v0}
        rows.append((k, k, f"{src} — 시스템 응답 값 아님" if v else "설치정보 v1.1 기준 (실행 당시 조회값 아님)"))
    for k, (v, src) in SPATIAL_DOC.items():                              # 로그인이 필요한 값 → 확인한 날의 값과 출처를 고정 기재
        found[k] = {v}
        rows.append((k, k, src))
    return pd.DataFrame([{"항목": k, "값": "; ".join(sorted(found[c])) if found.get(c) else "-", "비고": note}
                         for k, c, note in rows])


MODEL_NAME_DEFAULT = "gpt-oss-120b"                                           # 시험의뢰서 시험환경 'LLM 추론 환경 (gpt-oss-120b)'
MODEL_CONSOLE = "http://192.168.200.115:18800/api/models"                     # 공간정보 에이전트·MCP 콘솔 (조회만)


LAW_STATS = "http://192.168.200.116:17801/api/stats"                          # 법령 수집·적재 관리자 (조회만)


def current_law_db():
    """실행 시작 때 법령 수집·적재 관리자에서 마지막 수집일·건수 조회 → (값, 출처). 실패하면 ('', '')"""
    try:
        import datetime as dt
        import requests
        s = requests.get(LAW_STATS, timeout=5).json()
        at = (s.get("collection") or {}).get("last_collected_at") or ""
        day = (dt.datetime.fromisoformat(at.replace("Z", "+00:00")) + dt.timedelta(hours=9)).strftime("%Y-%m-%d") if at else "-"
        return (f"수집 기준 {day} (법령 {s['law']['total']:,} · 문서 {s['document']['total']:,} · 조문 {s['article']['total']:,})",
                "법령 수집·적재 관리자(116:17801) 조회값")
    except Exception:                                                         # noqa: BLE001
        return "", ""


SPATIAL_DOC = {"법령 온톨로지(그래프 DB) 버전": ("4.2.0-grounding",
                                         "Fuseki nipa 데이터셋 owl:versionInfo, 그래프 649개·트리플 1,408,044 (2026-11-04 조회, 로그인 필요) — 실행 당시 조회값 아님"),
               "공간 DB(PostgreSQL·PostGIS)": ("PostgreSQL 18.6 · PostGIS 3.6.4",
                                            "토지특성 공간 DB 명세(2026-10-19, 116:17432 ipss) 기준 — 실행 당시 조회값 아님")}
def current_itpt():
    """실행 시작 때 법령해석사례 건수·최신 회신일·마지막 수집 시각 조회 (매일 04시 갱신). 실패하면 ''"""
    try:
        import datetime as dt
        import requests
        it = requests.get(LAW_STATS, timeout=5).json().get("interpretation") or {}
        at = it.get("last_collected_at") or ""
        kst = (dt.datetime.fromisoformat(at.replace("Z", "+00:00")) + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M") if at else "-"
        y = str(it.get("latest_rply_ymd") or "")
        return f"{it.get('total', 0):,}건 · 최신 회신 {y[:4]}-{y[4:6]}-{y[6:]} · 수집 {kst}"
    except Exception:                                                         # noqa: BLE001
        return ""


INSTALL_DOC = {"LLM 서빙(vLLM)": "0.24.0", "그래프 DB(Fuseki)": "6.1.0", "벡터 DB(Milvus)": "2.6.18"}   # 설치정보 v1.1
INFRA_URL = {"LLM 서빙(vLLM)": "http://192.168.200.128:8003/version",            # 인증 없이 버전만 주는 주소
             "그래프 DB(Fuseki)": "http://192.168.200.116:3030/$/server"}


def infra_versions():
    """실행 시작 때 인프라 버전 조회 (vLLM·Fuseki는 서버에서 직접, Milvus는 조회 경로가 없어 설치정보 v1.1 값)"""
    import requests
    out = {}
    for k, url in INFRA_URL.items():
        try:
            m = re.search(r'"version"\s*:\s*"([^"]+)"', requests.get(url, timeout=5).text)
            out[k] = (m.group(1), "서버 조회값") if m else ("", "")
        except Exception:                                                     # noqa: BLE001
            out[k] = ("", "")
    out["벡터 DB(Milvus)"] = (INSTALL_DOC["벡터 DB(Milvus)"], "설치정보 v1.1")
    return {k: list(v) for k, v in out.items()}


def backfill(meta):
    """이전 실행(모델명·법령DB 기록 전)이면 현재 조회값을 채움 — 그 값이 실행 시작 전부터 그대로였다고 확인될 때만"""
    started = meta.get("started") or ""
    if not started or (meta.get("모델명") and meta.get("법령DB")):
        return meta
    meta = dict(meta)
    import datetime as dt
    import requests
    note = f"현재 조회값 — 실행({started}) 이후 변경 없음 확인"
    if not meta.get("모델명"):
        try:
            d = requests.get(MODEL_CONSOLE, timeout=5).json()
            name, src = current_model()
            sw = next((re.search(r"(\d{4}-\d{2}-\d{2})\s*전환", m.get("note") or "") for m in d.get("declared") or []
                       if m.get("id") == name), None)
            if sw and sw.group(1) < started[:10]:                          # 전환일이 실행일보다 앞섬 → 실행 때도 같은 모델
                meta["모델명"], meta["모델명출처"] = name, f"{src.replace(' 조회값', '')} {note} (전환 {sw.group(1)})"
        except Exception:                                                     # noqa: BLE001
            pass
    if not meta.get("법령DB"):
        try:
            s = requests.get(LAW_STATS, timeout=5).json()
            at = (s.get("collection") or {}).get("last_collected_at") or ""
            kst = (dt.datetime.fromisoformat(at.replace("Z", "+00:00")) + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S") if at else ""
            if kst and kst < started and not (s.get("collection") or {}).get("running_jobs"):   # 마지막 수집이 실행 전 → 실행 때도 같은 DB
                val, src = current_law_db()
                if val:
                    meta["법령DB"], meta["법령DB출처"] = val, f"{src.replace(' 조회값', '')} {note}"
        except Exception:                                                     # noqa: BLE001
            pass
    return meta


def current_model():
    """실행 시작 때 에이전트·MCP 콘솔에서 지금 쓰는 모델명 조회 → (모델명, 출처). 실패하면 기본값"""
    try:
        import collections
        import requests
        ag = requests.get(MODEL_CONSOLE, timeout=5).json().get("agents") or []
        name = collections.Counter(a.get("model") for a in ag if a.get("model")).most_common(1)[0][0]
        return name, "에이전트·MCP 콘솔(115:18800) 조회값"
    except Exception:                                                         # noqa: BLE001
        return MODEL_NAME_DEFAULT, "기본값(시험의뢰서 시험환경)"


# ---------------------------------------------------------------- [AI 2]
def score_ai2(log, answer, testset=None):
    """2×2 혼동행렬, 판단 유형별 Precision·Recall·F1, 가중평균 F1, 보조: 표현 변형 재현 일치율"""
    a = answer.set_index("시나리오ID")
    log = log.copy()
    log["정답_판단유형"] = log["시나리오ID"].map(lambda s: type2(a.loc[str(s)].get("판단유형")) if str(s) in a.index else UNDECIDED)
    log["예측_판단유형"] = log["판단유형"].fillna(UNDECIDED)
    log["일치"] = log["정답_판단유형"] == log["예측_판단유형"]
    log["불일치사유"] = [mismatch_reason(g, p, r) for g, p, (_, r) in zip(log["정답_판단유형"], log["예측_판단유형"], log.iterrows())]
    n = len(log)
    cols = TYPE2 + ([UNDECIDED] if (log["예측_판단유형"] == UNDECIDED).any() else [])
    cm = pd.crosstab(log["정답_판단유형"], log["예측_판단유형"]).reindex(index=TYPE2, columns=cols, fill_value=0)
    cm.index.name = "정답 ↓ / AI 판단 →"
    rows, wsum = [], 0.0
    for t in TYPE2:
        tp = int(cm.loc[t, t])
        fp = int(cm[t].sum() - tp)
        fn = int(cm.loc[t].sum() - tp)
        p = tp / (tp + fp) * 100 if tp + fp else None
        r = tp / (tp + fn) * 100 if tp + fn else None
        f1 = (2 * p * r / (p + r) if (p or 0) + (r or 0) else 0.0) if p is not None and r is not None else (0.0 if tp + fn else None)
        sup = tp + fn
        wsum += (f1 or 0) * sup
        rows.append({"판단유형": t, "정답 질의 수": sup, "TP": tp, "FP": fp, "FN": fn,
                     "Precision(%)": trunc2(p), "Recall(%)": trunc2(r), "F1(%)": trunc2(f1)})
    total = int(cm.values.sum())
    wf1 = trunc2(wsum / total) if total else None
    # 보조: 표현 변형 재현 일치율 — 원문 질의 결과와 같은 판단을 낸 변형 질의 비율
    rep_rows, var_n, var_ok = [], 0, 0
    for sid, g in log.groupby("시나리오ID", sort=False):
        orig = g[g["구분"].astype(str).str.contains("원문")]
        ov = orig["예측_판단유형"].iloc[0] if len(orig) else None
        vs = g[~g["구분"].astype(str).str.contains("원문")]
        ok = int((vs["예측_판단유형"] == ov).sum()) if ov else 0
        var_n += len(vs)
        var_ok += ok
        rep_rows.append({"시나리오ID": sid, "원문 질의문": orig["질의문"].iloc[0] if len(orig) and "질의문" in orig else "",
                         "정답 판단유형": g["정답_판단유형"].iloc[0], "원문 결과": ov or "-",
                         "변형 질의 수": len(vs), "원문과 동일": ok, "재현 일치율(%)": pct(ok, len(vs)),
                         "정답 일치 질의": int(g["일치"].sum())})
    rep_rows.append({"시나리오ID": "전체", "원문 질의문": f"시나리오 {log['시나리오ID'].nunique()}건 합계", "정답 판단유형": "-", "원문 결과": "-", "변형 질의 수": var_n,
                     "원문과 동일": var_ok, "재현 일치율(%)": pct(var_ok, var_n), "정답 일치 질의": int(log["일치"].sum())})
    return dict(value=wf1, n=n, confusion=cm,
                tables={"판단 유형 2종별 Precision·Recall·F1 스코어": pd.DataFrame(rows),
                        "보조 지표: 표현 변형 재현 일치율 (시나리오별·전체)": pd.DataFrame(rep_rows)},
                aux={"표현 변형 재현 일치율(%)": pct(var_ok, var_n)},
                cases=log[["질의ID", "시나리오ID", "구분", "질의문", "정답_판단유형", "예측_판단유형", "일치", "불일치사유"]])


# ---------------------------------------------------------------- [AI 3]
def score_ai3(log):
    """1회차 기준 로그와 2~5회차 비교. 비교 대상 필드가 모두 같으면 일치 수행 (1회차 자신도 일치에 포함)"""
    run_rows, stage_cnt = [], {}
    case_ok = []
    for cid, g in log.groupby("사례ID", sort=False):
        g = g.sort_values("회차")
        base = g.iloc[0]["단계로그"]
        all_ok = True
        for _, r in g.iterrows():
            if not r.get("단계로그") or r.get("오류"):
                diffs = [("응답 오류", "오류")]
            elif not base:
                diffs = [("기준 회차 오류", "오류")]
            elif int(r["회차"]) == int(g.iloc[0]["회차"]):
                diffs = []
            else:
                diffs = compare_steps(base, r["단계로그"])
            ok = not diffs
            all_ok &= ok
            for st_, _f in {d[0]: d[1] for d in diffs}.items():
                stage_cnt[st_] = stage_cnt.get(st_, 0) + 1
            run_rows.append({"사례ID": cid, "질의문": r.get("질의문", ""), "회차": int(r["회차"]),
                             "기준": "기준 (1회차)" if int(r["회차"]) == int(g.iloc[0]["회차"]) else "1회차와 비교",
                             "일치": ok, "불일치 단계": "; ".join(sorted({d[0] for d in diffs})) or "없음",
                             "불일치 필드": "; ".join(f"{d[0][:1]}{d[1]}" for d in diffs) or "없음",
                             "불일치 내용": diff_detail(base, r.get("단계로그") or [], diffs, r.get("오류")),
                             "최종 판단": next((s["중간판정값"] for s in r["단계로그"] if s["단계번호"] == 6), "")})
        case_ok.append({"사례ID": cid, "질의문": g.iloc[0].get("질의문", ""), "수행 횟수": len(g), "일치 수행": sum(x["일치"] for x in run_rows if x["사례ID"] == cid),
                        "5회 모두 일치": all_ok})
    runs = pd.DataFrame(run_rows)
    n = len(runs)
    val = pct(runs["일치"].sum(), n) if n else None
    cases = pd.DataFrame(case_ok)
    case_val = pct(cases["5회 모두 일치"].sum(), len(cases)) if len(cases) else None
    base_f = "실행 여부 · 조회 데이터소스 · 조회 키 · 조회 결과 건수"
    fields = {1: base_f, 2: base_f, 3: base_f, 4: base_f + " · 검색 법령 집합(순서 무시)",
              5: base_f + " · 적용 룰셋 ID 집합(순서 무시)", 6: base_f + " · 최종 판단 유형"}
    comp_n = int((runs["기준"] != "기준 (1회차)").sum()) if n else 0          # 2~5회차를 1회차와 비교한 횟수
    stage_rows = []
    for i_, s in enumerate(STEPS + ["단계 실행 순서"] + sorted(set(stage_cnt) - set(STEPS) - {"단계 실행 순서"}), 1):
        bad = stage_cnt.get(s, 0)
        stage_rows.append({"단계": s, "비교한 필드": fields.get(i_, "①~⑥ 단계의 실행 순서와 실행 여부" if s == "단계 실행 순서" else "응답 오류 등"),
                           "비교 횟수": comp_n, "일치": comp_n - bad, "불일치 발생 건수": bad,
                           "일치율(%)": pct(comp_n - bad, comp_n) if comp_n else None})
    stages = pd.DataFrame(stage_rows)
    pivot = runs.pivot_table(index="사례ID", columns="회차", values="일치", aggfunc="first").map(
        lambda x: "O" if x is True else ("X" if x is False else "")) if n else pd.DataFrame()
    if n:                                                           # 회차 열 이름을 "1회" … 로, 사례별 질의문을 앞에
        pivot.columns = [f"{c}회" for c in pivot.columns]
        pivot.insert(0, "질의문", runs.groupby("사례ID", sort=False)["질의문"].first().reindex(pivot.index))
    steplog = pd.DataFrame([{"사례ID": cid, "회차": int(r["회차"]), "단계번호": s_["단계번호"], "단계명": s_["단계명"],
                             "실행": "예" if s_.get("실행") else "아니오", "조회 데이터소스": s_.get("데이터소스", ""),
                             "조회 키": s_.get("조회키", ""), "조회 결과 건수": s_.get("결과건수", ""),
                             "적용 룰셋 ID": "; ".join(s_.get("적용룰셋") or []), "검색 법령": "; ".join(s_.get("검색법령") or []),
                             "최종 판단 유형": s_.get("중간판정값", "") if s_["단계번호"] == len(STEPS) else "",
                             "근거": s_.get("근거", "")}
                            for cid, g in log.groupby("사례ID", sort=False) for _, r in g.sort_values("회차").iterrows()
                            for s_ in (r.get("단계로그") or [])])
    return dict(steplog=steplog, value=val, n=n, tables={"사례별·회차별 비교 결과 (O 일치 / X 불일치)": pivot.reset_index() if n else pivot,
                                        "불일치 발생 단계별 건수": stages,
                                        "보조 지표: 사례 단위 일관성": cases},
                aux={"사례 단위 일관성(%)": case_val}, cases=runs)


# ---------------------------------------------------------------- [AI 4]
def _kw(s):
    return {w for w in re.findall(r"[가-힣A-Za-z]{2,}|\d+(?:\.\d+)?", str(s or "")) if w not in ("또는", "이상", "이하", "따른", "확보")}


def score_ai4(log, answer, testset=None):
    """판정 결과 + 단서 조건 근거 조문(집합) 모두 일치해야 정답. 보조: 보완사항(3단), 필요 행정절차(집합)"""
    a = answer.set_index("사례ID")
    tmeta = testset.set_index("사례ID") if testset is not None and "사례ID" in testset else None
    rows = []
    for _, r in log.iterrows():
        cid = str(r["사례ID"])
        g = a.loc[cid] if cid in a.index else None
        gv = type2(g.get("판정결과")) if g is not None else UNDECIDED
        pv = r.get("판단유형") or UNDECIDED
        gc = gold_cites(g.get("근거조문")) if g is not None else set()
        pc = parse_citations(r.get("단서조건근거조문") or [])
        # 규격서 수정(2026-11-11): 정답 근거 조문을 모두 포함하면 적합 — 정답 외 관련 조문의 추가 제시는 허용
        cite_ok = bool(gc) and all(any(cite_match(p, c) for p in pc) for c in gc)
        miss_c = sorted(fmt_cite(c) for c in gc if not any(cite_match(p, c) for p in pc))
        # 보완사항 3단 표기
        gb = str(g.get("보완사항", "")) if g is not None else ""
        pb = " ".join(str(r.get(k) or "") for k in ("보완사항", "단서조건문장", "설명문"))
        if not gb.strip():
            sup = None
        else:
            gk, pk = _kw(gb), _kw(pb)
            hit = len(gk & pk) / len(gk) if gk else 0
            sup = "정확 일치" if hit >= 0.8 else ("부분 일치" if hit > 0 else "불일치")
        gp = split_list(g.get("필요행정절차")) if g is not None else []
        pp = r.get("필요행정절차") or []
        proc_ok = (proc_set(gp) == proc_set(pp)) if gp else None      # 규격서: 집합 일치(순서 무시) — 표기만 정규화
        etype = str(tmeta.loc[cid].get("예외유형", "")) if tmeta is not None and cid in tmeta.index else ""
        rows.append(dict(사례ID=cid, 예외유형=etype or "-", 질의문=r.get("질의문"),
                         정답_판정결과=gv, 예측_판정결과=pv, 판정_일치=gv == pv,
                         정답_근거조문="; ".join(sorted(fmt_cite(c) for c in gc)),
                         예측_근거조문="; ".join(r.get("단서조건근거조문") or []), 근거조문_일치=cite_ok,
                         적합=(gv == pv) and cite_ok,
                         부적합사유=" / ".join(x for x in (
                             f"판정 불일치 — 정답 {gv}, AI {pv}. AI 판단 근거: {ai_basis(r)}" if gv != pv else "",
                             f"AI 응답에 없는 정답 근거 조문: {', '.join(miss_c)}" if miss_c else "") if x) or "-",
                         정답_보완사항=gb, 예측_보완사항=r.get("보완사항") or "", 보완사항_판정=sup,
                         정답_필요행정절차="; ".join(gp), 예측_필요행정절차="; ".join(pp), 필요행정절차_일치=proc_ok))
    res = pd.DataFrame(rows)
    if len(res):                                    # 화면에서 적합·부적합 사유가 바로 보이도록 질의문 뒤로
        front = ["사례ID", "예외유형", "질의문", "적합", "부적합사유"]
        res = res[front + [c for c in res.columns if c not in front]]
    n = len(res)
    val = pct(res["적합"].sum(), n) if n else None

    def grp(col, keys):
        out = []
        for k in keys:
            s = res[res[col] == k]
            out.append({col.replace("정답_", ""): k, "건수": len(s), "적합": int(s["적합"].sum()),
                        "부적합": int((~s["적합"]).sum()), "적합률(%)": pct(s["적합"].sum(), len(s))})
        return pd.DataFrame(out)
    t1 = grp("정답_판정결과", TYPE2)
    etypes = ["예외 조항", "단서 규정", "협의 대상", "심의 대상"] + \
        (sorted(set(res["예외유형"]) - {"예외 조항", "단서 규정", "협의 대상", "심의 대상", "-"}) if n else [])   # 규격서 4종은 0건이어도 항상 표시
    t2 = grp("예외유형", etypes) if n else pd.DataFrame()
    aux = []
    for name, col in [("필요 행정절차", "필요행정절차_일치")]:
        s = res[col].dropna().astype(bool) if n else pd.Series(dtype=bool)
        aux.append({"보조 지표": name, "대상": len(s), "일치": int(s.sum()), "불일치": int((~s).sum()),
                    "적합률(%)": pct(s.sum(), len(s)), "비고": ""})
    sb = res["보완사항_판정"].dropna() if n else pd.Series(dtype=object)
    aux.append({"보조 지표": "보완사항", "대상": len(sb), "일치": int((sb == "정확 일치").sum()),
                "불일치": int((sb == "불일치").sum()), "적합률(%)": pct((sb == "정확 일치").sum(), len(sb)),
                "비고": f"부분 일치 {int((sb == '부분 일치').sum())}건 (적합률은 정확 일치 기준)"})
    return dict(value=val, n=n, tables={"판정 결과(허가 가능/허가 불가능)별 적합·부적합 건수": t1, "예외 유형별 적합·부적합 건수": t2,
                                        "보조 지표(보완사항, 필요 행정절차) 일치·불일치 건수 및 적합률": pd.DataFrame(aux)},
                cases=res.assign(필요행정절차_일치=res["필요행정절차_일치"].map(           # 화면 표시용 글자 (참/거짓·빈 값 섞이면 표 변환 오류)
                    lambda x: "-" if x is None or (isinstance(x, float) and pd.isna(x)) else ("일치" if x else "불일치"))) if n else res)


# ---------------------------------------------------------------- [AI 5]
def load_law_db(path):
    df = read_table(path)
    if df is None or df.empty:
        return None
    col = next((c for c in df.columns if "조문" in c or c.lower() in ("id", "article_id")), df.columns[0])
    return parse_citations(df[col].tolist())


OLD_LAW_NAMES = {"도시공원법": "도시공원 및 녹지 등에 관한 법률",          # 폐지·개칭된 옛 법령명 → 현행 명칭 (환각 사유 안내용, 판정에는 쓰지 않음)
                 "도시공원법시행령": "도시공원 및 녹지 등에 관한 법률 시행령"}


def score_ai5(log, answer, law_db=None):
    """환각 ①~④ 자동 판정 (하나라도 해당하면 환각 1건, 중복 계수 안 함). ⑤ 논리 불일치는 시험항목에서 제외(2026-11-10)"""
    a = answer.set_index("사례ID")
    arts, hangs = set(), {}
    for l_, j_, h_ in law_db or ():                  # ① 조문 DB 색인: 조 단위 + 조별 항 목록
        arts.add((l_, j_))
        if h_:
            hangs.setdefault((l_, j_), set()).add(h_)

    def exists(c):                                   # 조가 있어야 하고, 항을 인용했으면 DB에 그 항이 있어야 함 (예: 국토계획법 제58조 제7항 → 6항까지라 미존재)
        return (c[0], c[1]) in arts and (not c[2] or (c[0], c[1]) not in hangs or c[2] in hangs[(c[0], c[1])])
    rows = []
    for _, r in log.iterrows():
        cid = str(r["사례ID"])
        g = a.loc[cid] if cid in a.index else None
        pc = parse_citations(r.get("근거조문") or [])
        gc = gold_cites(g.get("근거조문")) if g is not None else set()
        gl = {norm_law(x) for x in split_list(g.get("적용법령"))} if g is not None else set()
        pl = {norm_law(x) for x in r.get("적용법령") or []}
        h1 = None if law_db is None else any(not exists(c) for c in pc)
        h2 = not pc
        h3 = bool(pc) and bool(gc) and not cite_overlap(pc, gc)
        # ④ 정답 적용 법령은 허용 집합(근거조문 열의 법령, 이 중 어느 것을 근거로 해도 맞음) → ③과 같은 방식으로
        #    응답의 적용 법령이 허용 법령과 하나도 일치하지 않으면 불일치 (2026-11-10, 완전 일치 규칙에서 변경 · 시험기관 확인 대상)
        h4 = bool(gl) and not (gl & pl)
        missing = [fmt_cite(c) for c in pc if law_db is not None and not exists(c)]
        why = []                                       # 환각 사유 (유형별로 무엇이 왜 걸렸는지)
        if h1:
            olds = sorted({c[0] for c in pc if not exists(c) and c[0] in OLD_LAW_NAMES})
            why.append("① 법령·조례 조문 DB에 없는 조문 인용: " + ", ".join(missing)
                       + (f" — 옛 법령명 {'·'.join(olds)} 사용(현행 「{OLD_LAW_NAMES[olds[0]]}」). DB에는 현행 명칭만 있음" if olds else ""))
        if h2:
            why.append("② 응답에 근거 조문이 없음 (판정 단계·조건·불허 사유 어디에도 조문 번호 없음)")
        if h3:
            why.append(f"③ 인용 조문({', '.join(sorted(fmt_cite(c) for c in pc))})이 정답 조문셋과 하나도 일치하지 않음")
        if h4:
            why.append(f"④ 적용 법령({', '.join(sorted(law_id(x) for x in pl)) or '없음'})이 정답 허용 법령({', '.join(split_list(g.get('적용법령')))})과 하나도 일치하지 않음")
        rows.append(dict(사례ID=cid, 질의문=r.get("질의문"), 판단유형=r.get("판단유형"),
                         설명문=str(r.get("설명문") or ""),                 # 규격서 출력: 판단 결과·설명문·인용 조문·적용 법령
                         인용조문="; ".join(r.get("근거조문") or []), 정답조문="; ".join(sorted(fmt_cite(c) for c in gc)),
                         적용법령="; ".join(r.get("적용법령") or []), 정답_적용법령="; ".join(split_list(g.get("적용법령")) if g is not None else []),
                         **{"① 인용 조문 미존재": h1, "② 근거 조문 부재": h2, "③ 근거 오인용": h3, "④ 법령·조례 근거 불일치": h4},
                         환각=bool(h1) or h2 or h3 or h4, 환각사유=" / ".join(why) or "-",
                         DB미존재조문="; ".join(missing)))
    res = pd.DataFrame(rows)
    n = len(res)
    val = pct(res["환각"].sum(), n) if n else None
    how = {"① 인용 조문 미존재": "응답의 인용 조문이 법령·조례 조문 DB에 존재하지 않음",       # 규격서 v1.51 40쪽 표 그대로
           "② 근거 조문 부재": "응답에 근거 조문 항목이 없거나 값이 비어 있음",
           "③ 근거 오인용": "인용 조문이 실재하나 정답 조문셋과 교집합이 없음",
           "④ 법령·조례 근거 불일치": "응답의 적용 법령이 정답 적용 법령과 다름 (정답 허용 법령과 하나도 일치하지 않음)"}       # ⑤ 논리 불일치는 시험항목에서 제외(2026-11-10)
    types = []
    for k in how:
        s = res[k] if n else pd.Series(dtype=object)
        types.append({"환각 유형": k, "판정 방법": how[k], "검출 건수": int(s.fillna(False).astype(bool).sum()) if n else 0,
                      "산정": "산정 대상 — 조문 DB 미제공으로 판정 보류" if k.startswith("①") and law_db is None else "산정 대상"})
    summ = pd.DataFrame([{"구분": "정상 응답", "건수": int((~res["환각"]).sum()) if n else 0},
                         {"구분": "환각 응답", "건수": int(res["환각"].sum()) if n else 0},
                         {"구분": "전체", "건수": n}])
    return dict(value=val, n=n, tables={"환각 유형 ①~④별 검출 건수": pd.DataFrame(types), "정상·환각 응답 건수": summ},
                aux={}, cases=res)


SCORERS = {"AI1": score_ai1, "AI2": score_ai2, "AI3": score_ai3, "AI4": score_ai4, "AI5": score_ai5}


def score(item, run_dir, answer_path=None, testset_path=None, lawdb_path=None):
    log = read_log(run_dir, item)
    if log.empty:
        return None
    if SPEC[item].get("cmd"):
        r = score_cmd(item, log, read_table(answer_path), read_table(testset_path))
    elif item == "AI3":
        r = score_ai3(log)
    elif item == "AI5":
        r = score_ai5(log, read_table(answer_path), load_law_db(lawdb_path))
    elif item in ("AI2", "AI4"):
        r = SCORERS[item](log, read_table(answer_path), read_table(testset_path))
    else:
        r = score_ai1(log, read_table(answer_path))
    r["passed"] = passed(item, r["value"])
    r["item"] = item
    if not SPEC[item].get("cmd"):                                   # AI1~5: 동결 버전 확인 (시험방법 (2))
        r["tables"]["버전 정보"] = version_table(log, run_dir)
        et = error_table(log, r.get("cases"), run_dir)
        if not et.empty:                                            # 응답 오류 표는 첫 결과 표 바로 다음에
            items = list(r["tables"].items())
            r["tables"] = dict(items[:1] + [(f"응답 오류 ({len(et)}건)", et)] + items[1:])
    return drop_pred_prefix(r)


def error_table(log, cases=None, run_dir=None):
    """응답 오류(통신 오류·응답 중단) 목록: 사례·질의·정답·오류 종류·발생 위치 — 판단불가로 집계된 건의 원인"""
    key = "질의ID" if "질의ID" in log.columns else "사례ID"
    gold = {}
    if isinstance(cases, pd.DataFrame) and key in cases:
        gcol = next((c for c in ("정답_판단유형", "정답_판정결과") if c in cases), None)
        if gcol:
            gold = dict(zip(cases[key].astype(str), cases[gcol]))
    rows = []
    for _, rr in log.iterrows():
        kind, where = error_detail(rr, run_dir)
        if kind:
            rows.append({key: rr[key], "회차": rr.get("회차"), "질의문": rr.get("질의문"), "정답": gold.get(str(rr[key]), "-"),
                         "오류 종류": kind.split(" — ")[0], "원인": kind.split(" — ")[-1], "발생 위치": where,
                         "소요(초)": rr.get("소요초")})
    df = pd.DataFrame(rows)
    if not df.empty and (df["회차"].fillna(1).astype(int) == 1).all():          # 반복 없는 항목은 회차 칸 생략
        df = df.drop(columns=["회차"])
    return df


def drop_pred_prefix(r):
    """출력(화면·result.xlsx)에서 '예측_' 접두어 제거 — 예측_판단유형 → 판단유형 (산정 로직의 내부 이름은 그대로)"""
    def ren(df):
        if not isinstance(df, pd.DataFrame):
            return df
        new = {c: c[3:] for c in df.columns if isinstance(c, str) and c.startswith("예측_") and c[3:] not in df.columns}
        df = df.rename(columns=new)
        for ax in (df.index, df.columns):                     # 혼동행렬 축 이름
            if isinstance(ax.name, str) and ax.name.startswith("예측_"):
                ax.name = ax.name[3:]
        return df
    r["cases"] = ren(r.get("cases"))
    r["confusion"] = ren(r.get("confusion"))
    r["tables"] = {k: ren(v) for k, v in (r.get("tables") or {}).items()}
    return r


def save_result(r, path):
    with pd.ExcelWriter(path) as w:
        sp = SPEC[r["item"]]
        pd.DataFrame([("시험항목", f"{sp['no']} {sp['name']}"), ("지표", sp["metric"]),
                      ("결과(%)", r["value"]), ("목표", f"{sp['target']} % {'이상' if sp['cmp'] == '>=' else '이하'}"),
                      ("판정", "달성" if r["passed"] else "미달성"), ("대상 건수", r["n"]), ("산정식", sp["formula"])]
                     + [(k, v) for k, v in (r.get("aux") or {}).items()],
                     columns=["항목", "값"]).to_excel(w, sheet_name="결과", index=False)
        if isinstance(r.get("confusion"), pd.DataFrame) and not r["confusion"].empty:
            r["confusion"].to_excel(w, sheet_name="혼동행렬")
        for i, (name, df) in enumerate(r["tables"].items()):
            if isinstance(df, pd.DataFrame) and not df.empty:
                df.to_excel(w, sheet_name=re.sub(r"[\[\]:*?/\\()·]", "", name)[:28] or f"표{i}", index=False)
        r["cases"].to_excel(w, sheet_name="사례별", index=False)
        if isinstance(r.get("steplog"), pd.DataFrame) and not r["steplog"].empty:   # AI3: 회차별 6단계 처리 로그 (규격서 저장 항목)
            r["steplog"].to_excel(w, sheet_name="6단계 처리 로그", index=False)


# ---------------------------------------------------------------- 데이터 양식 (템플릿)
TEMPLATES = {
    "AI1": {
        "testset_ai1.csv": (["사례ID", "질의문", "주소", "시설물", "행위유형",               # AIPS에는 질의문만 입력
                             "PNU", "용도지역_자료", "규제구역_자료", "지목", "면적"],      # 정답 대조용 참조 정보 (규격서 v1.51)
                            [["AI1-0001", "경상북도 영천시 과전동 91에 단독주택 지을 수 있어?", "경상북도 영천시 과전동 91", "단독주택",
                              "건축(신축)", "4723010800100910000", "일반상업지역; 역사문화환경 보존지역", "가축사육제한구역", "대", "599"]]),
        "answer_ai1.csv": (["사례ID", "판단유형", "적용법령", "근거조문"],
                           [["AI1-0001", "허가 가능", "영천시도시계획조례", "영천시도시계획조례.33; 영천시도시계획조례.별표8"]]),
    },
    "AI2": {
        "testset_ai2.csv": (["질의ID", "시나리오ID", "구분", "질의문"],
                            [["AI2-0001", "S-001", "원문", "경상북도 영천시 과전동 91에 단독주택 지을 수 있어?"],
                             ["AI2-0002", "S-001", "변형1", "과전동 91번지에 단독주택 올려도 되나요?"],
                             ["AI2-0003", "S-001", "변형2", "거기 단독주택 신축 가능한지 봐줘"],
                             ["AI2-0004", "S-001", "변형3", "영천시 과전동 91에 주거용 건물 지을 수 있습니까?"],
                             ["AI2-0005", "S-001", "변형4", "과전동 91에 집 지어도 될까?"]]),
        "answer_ai2.csv": (["시나리오ID", "판단유형"], [["S-001", "허가 가능"]]),
    },
    "AI3": {
        "testset_ai3.csv": (["사례ID", "질의문", "주소", "시설물", "행위유형", "PNU"],
                            [["AI3-0001", "경상북도 영천시 과전동 91에 단독주택 지을 수 있어?", "경상북도 영천시 과전동 91", "단독주택",
                              "건축(신축)", "4723010800100910000"]]),
    },
    "AI4": {
        "testset_ai4.csv": (["사례ID", "질의문", "예외유형"],
                            [["AI4-0001", "경상북도 영천시 조교동 43-12에 단독주택 지을 수 있어?", "단서 규정"]]),
        "answer_ai4.csv": (["사례ID", "판정결과", "근거조문", "보완사항", "필요행정절차"],
                           [["AI4-0001", "허가 가능", "건축법.2.1; 건축법.44",
                             "도로 폭 4 m 이상 확보 또는 건축법 제45조에 따른 도로 지정", "개발행위허가; 도로 지정 협의"]]),
    },
    "AI5": {
        "testset_ai5.csv": (["사례ID", "질의문", "주소", "시설물", "행위유형", "PNU", "용도지역_자료", "규제구역_자료", "지목", "면적"],
                            [["AI5-0001", "경상북도 영천시 과전동 91에 단독주택 지을 수 있어?", "경상북도 영천시 과전동 91", "단독주택",
                              "건축(신축)", "4723010800100910000", "일반상업지역; 역사문화환경 보존지역", "가축사육제한구역", "대", "599"]]),
        "answer_ai5.csv": (["사례ID", "적용법령", "근거조문"],
                           [["AI5-0001", "영천시도시계획조례", "영천시도시계획조례.33; 영천시도시계획조례.별표8"]]),
        "law_article_db.csv": (["조문ID", "법령명", "구분", "조", "항", "시행일"],          # 법령 수집·적재 관리자(116:17801) 적재분에서 생성
                               [["건축법.44", "건축법", "law", "44", "", "20260101"], ["건축법.44.1", "건축법", "law", "44", "1", "20260101"]]),
    },
}


def write_templates(folder):
    os.makedirs(folder, exist_ok=True)
    made = []
    for item, files in TEMPLATES.items():
        for fn, (cols, rows) in files.items():
            p = os.path.join(folder, fn)
            if not os.path.exists(p):
                pd.DataFrame(rows, columns=cols).to_csv(p, index=False, encoding="utf-8-sig")
                made.append(p)
    return made


def gold_to_ai1(gold_df, addr_col, fac_col, folder):
    """기존 gold.xlsx → testset_ai1.csv / answer_ai1.csv (바로 시험해 볼 수 있도록)"""
    ts, an = [], []
    for _, g in gold_df.iterrows():
        cid = f"AI1-{int(float(g['case_id'])):04d}" if str(g["case_id"]).replace(".0", "").isdigit() else str(g["case_id"])
        q = g["질의문"] if "질의문" in g and str(g.get("질의문", "")).strip() else f"{g[addr_col]}에 {g[fac_col]} 지을 수 있어?"
        cites = parse_citations(str(g.get("근거", "")))
        ts.append([cid, q, str(g[addr_col]).strip(), str(g[fac_col]).strip()] + [""] * 6)   # 참조 정보는 비워 둠
        an.append([cid, "허가 불가능" if type2(g.get("정답판정")) == "허가 불가능" else "허가 가능",
                   "; ".join(sorted({law_id(c[0]) for c in cites})), "; ".join(sorted(fmt_cite(c) for c in cites))])
    os.makedirs(folder, exist_ok=True)
    pd.DataFrame(ts, columns=TEMPLATES["AI1"]["testset_ai1.csv"][0]).to_csv(
        os.path.join(folder, "testset_ai1.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(an, columns=TEMPLATES["AI1"]["answer_ai1.csv"][0]).to_csv(
        os.path.join(folder, "answer_ai1.csv"), index=False, encoding="utf-8-sig")
    return len(ts)


# AI1 참조 정보 — 시험셋에는 주소·시설물만 두고, PNU·용도지역·규제구역·토지특성은 룰엔진 실측 서버에서 가져옴.
# 시스템 입력은 여전히 질의문뿐이고, 이 값들은 정답 대조·원인 분석용 참조로만 결과에 붙음 (정확도 산정 미포함)
REF_BASE = os.environ.get("AIPS_REF_BASE", "http://192.168.200.29:8777")    # 참조 룰엔진 (개발자 PC)
REF_COLS = ["참조_PNU", "참조_용도지역", "참조_규제구역", "참조_토지특성", "참조_룰엔진판정", "참조_룰엔진사유", "참조_오류"]
REF_DECISION = {"APPROVED": "허가 가능", "CONDITIONAL": "허가 가능", "DENIED": "허가 불가능"}


def fetch_ref(addr, facility, base=REF_BASE, timeout=60):
    """주소·시설물 → 룰엔진 /api/full (웨이버스 → Fuseki → 룰엔진, 약 3초) → 참조_* 컬럼"""
    import requests
    out = dict.fromkeys(REF_COLS, "")
    addr, facility = str(addr or "").strip(), str(facility or "").strip()
    if not base or not addr or addr == "nan":
        out["참조_오류"] = "주소 없음" if base else ""
        return out
    try:
        j = requests.post(f"{base.rstrip('/')}/api/full", timeout=timeout,
                          json={"query": addr, "input_type": "auto", "user_facility": facility or "단독주택",
                                "user_activity": ""}).json()
    except Exception as e:                                          # noqa: BLE001
        out["참조_오류"] = f"{type(e).__name__}: {str(e)[:200]}"
        return out
    p = (j.get("웨이버스") or {}).get("parcel") or {}
    zone, other = [], []
    for z in p.get("zones") or []:
        if not z.get("zone"):
            continue
        s = f"{z.get('zone')}({z.get('conflict') or '포함'})"
        # 국토계획법 용도지역 = UQA(도시지역)·UQB(관리)·UQC(농림)·UQD(자연환경보전), 그 외 지구·구역·시설은 규제구역
        (zone if str(z.get("code") or "")[:3] in ("UQA", "UQB", "UQC", "UQD") else other).append(s)
    road = (p.get("road_contact") or {}).get("total_contact_length")
    v = j.get("판정") or {}
    out.update(참조_PNU=p.get("pnu") or "", 참조_용도지역="; ".join(zone), 참조_규제구역="; ".join(other),
               참조_토지특성="; ".join(f"{k}={x}" for k, x in [("면적", f"{p.get('area_m2')}㎡" if p.get("area_m2") else ""),
                                                          ("지목", p.get("jimok")), ("접도길이", f"{round(road, 1)}m" if road is not None else "")] if x),
               참조_룰엔진판정=REF_DECISION.get(v.get("decision"), v.get("decision") or ""),
               참조_룰엔진사유="; ".join(v.get("violations") or []),
               참조_오류=j.get("error") or ("; ".join(f"{s}" for s in (j.get("웨이버스") or {}).get("failed_steps") or [])))
    return out


# ---------------------------------------------------------------- [AI 6] 지도 제어 · [AI 7] 3D 시뮬레이션
# 실행 결과(시스템 응답)는 결과 파일(result_ai6.csv / result_ai7.csv)을 가져와 채점합니다.
# 정답·결과의 파라미터와 시나리오는 "키=값; 키=값" 형식 (성과지표 예시의 "위치 ○○ / 분석대상 ○○"도 읽음)
def _norm_tok(v):
    s = re.sub(r"[\s·ㆍ・,]", "", str(v or "")).lower()
    return re.sub(r"번지|번$", "", s)


def parse_kv(v):
    """'위치=경산시 263번지; 분석대상=용도지역ㆍ지구' / '위치 경산시 263번지 / 층수 3' / JSON 객체 → {키: 값}"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return {}
    if isinstance(v, dict):
        return {str(k).strip(): str(x) for k, x in v.items()}
    s = str(v).strip()
    if s.startswith("{"):
        try:
            d = json.loads(s)

            def flat(d, pre=""):
                out = {}
                for k, x in d.items():
                    if isinstance(x, dict):
                        out.update(flat(x, f"{pre}{k}."))
                    else:
                        out[f"{pre}{k}"] = "; ".join(map(str, x)) if isinstance(x, list) else str(x)
                return out
            return flat(d)
        except json.JSONDecodeError:
            pass
    out = {}
    for part in re.split(r"[;\n]|\s/\s|(?<=\S)/(?=\S*\s)", s):
        part = part.strip().strip("'\"‘’")
        if not part:
            continue
        m = re.match(r"([^=:：]+)[=:：](.+)", part)
        if m:
            k, x = m.group(1), m.group(2)
        else:
            bits = part.split(None, 1)
            k, x = (bits[0], bits[1]) if len(bits) == 2 else (part, "")
        out[k.strip()] = x.strip()
    return out


def kv_match(gold, pred):
    """정답 키 각각이 결과에 있고 값이 같아야 일치 (표기 차이 허용: '263번지' = '263', 공백·가운뎃점 무시).
    결과 키는 정답 키와 같거나 끝부분이 같으면 같은 항목으로 봄 (예: '지도상태.이동위치' ↔ '이동위치')"""
    miss = []
    for k, gv in gold.items():
        pv = pred.get(k)
        if pv is None:
            pv = next((v for kk, v in pred.items() if kk.split(".")[-1] == k or _norm_tok(kk) == _norm_tok(k)), None)
        g, p_ = _norm_tok(gv), _norm_tok(pv)
        if pv is None or not (g == p_ or (g and g in p_) or (p_ and p_ in g)):
            miss.append(f"{k}(정답 {gv} / 결과 {pv if pv is not None else '없음'})")
    return not miss, miss


def _intents(v):
    return {re.sub(r"\s", "", x) for x in split_list(v)}


def score_cmd(item, log, answer, testset=None):
    """AI6·AI7: ① 의도(집합 정확 일치) → ② 핵심 파라미터 → ③ 지도·3D 실행(시나리오) 순으로 판정,
    셋 다 일치해야 정상 수행. 오류 원인은 최초 불일치 단계로 분류. 부분 일치는 오류."""
    sp = SPEC[item]
    a = answer.set_index("사례ID")
    tmeta = testset.set_index("사례ID") if testset is not None and "사례ID" in testset else None
    rows = []
    for _, r in log.iterrows():
        cid = str(r["사례ID"])
        g = a.loc[cid] if cid in a.index else None
        ctype = str(g.get("명령유형", "")) if g is not None else ""
        if not ctype and tmeta is not None and cid in tmeta.index:
            ctype = str(tmeta.loc[cid].get("명령유형", ""))
        gi, pi = (_intents(g.get("정답의도")) if g is not None else set()), _intents(r.get("해석의도"))
        ok1 = bool(gi) and gi == pi
        ok2, miss2 = kv_match(parse_kv(g.get("정답파라미터")) if g is not None else {}, parse_kv(r.get("해석파라미터")))
        status = str(r.get("실행상태") or "").strip()
        manual = str(r.get("시나리오판정") or "").strip().upper()
        if manual in ("O", "X", "일치", "불일치"):
            ok3, miss3 = manual in ("O", "일치"), ([] if manual in ("O", "일치") else ["시험원 판정 불일치"])
        else:
            ok3, miss3 = kv_match(parse_kv(g.get("정답시나리오")) if g is not None else {}, parse_kv(r.get("실행결과")))
        if status and status not in ("완료", "성공", "done", "success", "ok", "OK"):
            ok3, miss3 = False, [f"실행상태 {status}"] + miss3
        cause = "" if ok1 and ok2 and ok3 else sp["causes"][0 if not ok1 else (1 if not ok2 else 2)]
        rows.append({"사례ID": cid, "명령유형": ctype or "-", "명령문": r.get("명령문") or r.get("질의문"),
                     "정답_의도": "; ".join(split_list(g.get("정답의도")) if g is not None else []),
                     "결과_의도": "; ".join(split_list(r.get("해석의도"))), "① 의도": ok1,
                     "② 파라미터": ok2, "③ 실행": ok3, "정상수행": ok1 and ok2 and ok3, "오류원인": cause,
                     "불일치 내용": " | ".join(miss2 + miss3) if not (ok2 and ok3) else "",
                     "실행상태": status or "-", "캡처파일": r.get("캡처파일") or ""})
    res = pd.DataFrame(rows)
    n, dec = len(res), sp.get("dec", 2)
    ok = int(res["정상수행"].sum()) if n else 0
    val = pct(ok, n, dec)
    total = pd.DataFrame([{"전체 테스트 건수": n, "정상 수행 건수": ok, "오류 건수": n - ok, "정확도(%)": val}])
    types = sp["types"] + sorted(set(res["명령유형"]) - set(sp["types"]) - {"-"}) if n else sp["types"]
    by_t = pd.DataFrame([{"명령 유형": t, "건수": int((res["명령유형"] == t).sum()) if n else 0,
                          "정상 수행": int(res[res["명령유형"] == t]["정상수행"].sum()) if n else 0,
                          "정확도(%)": pct(int(res[res["명령유형"] == t]["정상수행"].sum()), int((res["명령유형"] == t).sum()), dec) if n else None}
                         for t in types])
    by_c = pd.DataFrame([{"오류 원인 유형": c, "건수": int((res["오류원인"] == c).sum()) if n else 0} for c in sp["causes"]])
    return dict(value=val, n=n, tables={"총괄 결과 (전체 테스트 건수·정상 수행 건수·오류 건수·정확도)": total,
                                        f"명령 유형 {len(sp['types'])}종별 세부 결과": by_t, "오류 원인 유형별 건수": by_c},
                cases=res)


RESULT_COLS = ["사례ID", "해석의도", "해석파라미터", "실행대상기능", "실행결과", "실행상태", "캡처파일", "시나리오판정"]


def import_results(item, result_path, testset_path=None, answer_path=None, run_dir=None):
    """시스템 실행 결과 파일 → spec/AIn/<시각>/ (로그·meta·결과) 생성 후 채점"""
    df = read_table(result_path)
    if df is None or df.empty or "사례ID" not in df:
        raise ValueError("결과 파일에 '사례ID' 컬럼이 필요합니다.")
    ts = read_table(testset_path) if testset_path else None
    tsi = ts.set_index("사례ID") if ts is not None and "사례ID" in ts else None
    run_dir = run_dir or os.path.join("spec", item, __import__("time").strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, SPEC[item]["log"]), "w", encoding="utf-8") as f:
        for _, r in df.iterrows():
            rec = {c: r.get(c, "") for c in df.columns}
            rec["회차"] = 1
            cid = str(r["사례ID"])
            if tsi is not None and cid in tsi.index:
                rec.setdefault("명령문", tsi.loc[cid].get("명령문", ""))
                rec.setdefault("명령유형", tsi.loc[cid].get("명령유형", ""))
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    meta = dict(item=item, 시험항목=SPEC[item]["name"], source="결과 파일 가져오기", result_file=result_path,
                result_sha256=sha256(result_path), testset=testset_path, testset_sha256=sha256(testset_path),
                answer=answer_path, answer_sha256=sha256(answer_path),
                started=__import__("time").strftime("%Y-%m-%d %H:%M:%S"))
    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return run_dir


TEMPLATES["AI6"] = {
    "testset_ai6.csv": (["사례ID", "명령유형", "명령문"],
                        [["AI6-0001", "공간규제 분석", "경산시 ○○읍 ○○리 263번지 필지에 저촉되는 용도지역ㆍ지구를 분석해서 지도에 보여줘"]]),
    "answer_ai6.csv": (["사례ID", "명령유형", "정답의도", "정답파라미터", "정답시나리오"],
                       [["AI6-0001", "공간규제 분석", "지도 이동; 규제 저촉 분석; 분석 결과 표출",
                         "위치=경산시 ○○읍 ○○리 263번지; 분석대상=용도지역ㆍ지구",
                         "이동위치=263번지 필지; 분석결과표출=저촉 용도지역ㆍ지구"]]),
    "result_ai6.csv": (RESULT_COLS,
                       [["AI6-0001", "지도 이동; 규제 저촉 분석; 분석 결과 표출",
                         "위치=경산시 ○○읍 ○○리 263번지; 분석대상=용도지역ㆍ지구",
                         "공간규제 분석: 주소 지오코딩, 지도이동/확대, 토지이용계획 조회, 저촉/접합 분석, 지도 표출",
                         "이동위치=경산시 ○○읍 ○○리 263번지; 분석결과표출=저촉 용도지역ㆍ지구", "완료", "AI6-0001.png", ""]]),
}
TEMPLATES["AI7"] = {
    "testset_ai7.csv": (["사례ID", "명령유형", "명령문", "선행상태"],
                        [["AI7-0001", "일조 시뮬레이션 실행", "경산시 ○○읍 ○○리 263번지에 3층 단독주택을 가상 건물로 세우고 일조 시뮬레이션 실행해줘", ""]]),
    "answer_ai7.csv": (["사례ID", "명령유형", "정답의도", "정답파라미터", "정답시나리오"],
                       [["AI7-0001", "일조 시뮬레이션 실행", "가상건물 생성; 일조 시뮬레이션 실행",
                         "위치=경산시 ○○읍 ○○리 263번지; 층수=3; 용도=단독주택; 기준일=12-22",
                         "생성건물층수=3; 생성건물용도=단독주택; 시뮬레이션종류=일조; 대상=생성건물; 기준일=12-22; 시간대=09:00~15:00"]]),
    "result_ai7.csv": (RESULT_COLS,
                       [["AI7-0001", "가상건물 생성; 일조 시뮬레이션 실행",
                         "위치=경산시 ○○읍 ○○리 263번지; 층수=3; 용도=단독주택; 기준일=12-22",
                         "주소검색 Tool, 가상 건물 생성 Tool, 일조 시뮬레이션 Tool",
                         "생성건물층수=3; 생성건물용도=단독주택; 시뮬레이션종류=일조; 대상=생성건물; 기준일=12-22; 시간대=09:00~15:00",
                         "완료", "AI7-0001.png", ""]]),
}
