# 사유코드 기능 백업 (2026-11-03)

시험의뢰서에서 사유코드를 빼기로 하여 QA 시스템에서도 제거했습니다.
나중에 다시 넣게 되면 이 폴더의 파일과 아래 코드로 복원합니다.

## 근거 (시험의뢰서 v.15, 제거 전)

- p.33 시험조건: 정답(검증) 데이터 = 판단 유형, **사유 코드**, 적용 법령, 근거 조문
  (예시: 사례 ID 'AI1-0001', 판단유형 '허가 가능', 사유코드 'COND-ROAD-01', 근거조문 ['건축법.2.1', '건축법.44'])
- p.34 시험방법 (3): 출력 = 판단유형, **사유코드**, 근거법령, 적용룰셋
- p.37 (AI2) 출력에도 동일 문구
- 채점(정확도)에는 사유코드를 쓰지 않음 — 출력·정답 라벨 항목일 뿐

## 파일

| 파일 | 복원 위치 | 내용 |
|---|---|---|
| `reason_code.csv` | `spec_data/reason_code.csv` | 코드 8종 정의 + 룰ID·판별키워드 (행 순서 = 우선순위) |
| `answer_ai1_사유코드.csv` | `spec_data/answer_ai1.csv` | 사유코드 컬럼이 들어간 AI1 정답셋 10건 |
| `answer_ai1_작성_사유코드.xlsx` | `spec_data/answer_ai1_작성.xlsx` | 사유코드를 수식으로 자동판정하는 정답셋 작성 엑셀 (입력 / answer_ai1 / 사유코드표 / 작성안내) |

시스템(AIPS)은 사유코드를 출력하지 않습니다. 그래서 QA가 응답의 판정, 위반 룰, 불허 사유를 `reason_code.csv` 규칙으로 바꿔서 "QA 변환"으로 표시했습니다.
룰엔진(192.168.200.29:8777)도 사유코드 필드는 없습니다. `판정.decision`, `evaluated_rules[status=fail]`, `matrix_gate`를 재료로 변환했습니다.

확장안: 룰엔진 룰 114개(`GET http://192.168.200.29:8777/api/rulelog/table`)를 분야 단위로 묶는 약 20개 코드 체계를 검토했습니다. 아직 반영하지 않았습니다.
- DENY: ROAD, ZONE, DIST, UPF, WATER, MNT, FARM, SLOPE, IND, CULT, SCALE, ETC
- COND: ROAD, REVIEW, MNT, FARM, CULT, SCALE
- PERM: ZONE, DIST

## 코드 복원

### 1) `spec_eval.py`: `map_response()` 바로 위에 추가

```python
# 사유코드 — 규격서 출력 항목(판단유형·사유코드·근거법령·적용룰셋). 시스템이 코드를 주지 않으므로
# 판정 + 위반 룰·불허 사유를 spec_data/reason_code.csv 의 룰ID·판별키워드로 변환 (위에서부터 첫 일치, 없으면 ZONE)
REASON_CODES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spec_data", "reason_code.csv")


def derive_reason_code(verdict, evidence=(), rule_ids=(), conditional=False, table=None):
    t2 = type2(verdict)
    if t2 == UNDECIDED:
        return ""
    prefix = "DENY" if t2 == "허가 불가능" else ("COND" if conditional else "PERM")
    df = table if table is not None else read_table(REASON_CODES)
    text, ids = " ".join(str(e) for e in evidence or []), set(rule_ids or [])
    if df is not None:
        for _, row in df.iterrows():
            code = str(row["사유코드"])
            if not code.startswith(prefix):
                continue
            kws = split_list(row.get("판별키워드"))
            if (ids & set(split_list(row.get("룰ID")))) or (kws and any(k in text for k in kws)):
                return code
    return f"{'DENY' if prefix == 'DENY' else 'PERM'}-ZONE-01"


def reason_code_of(r):
    """AIPS 응답(로그 1행) → (사유코드, 출처). 시스템이 사유코드를 주면 그 값을 우선"""
    if r.get("사유코드_시스템"):
        return r["사유코드_시스템"], "시스템"
    deny = list(r.get("불허사유") or []) + list(r.get("위반조건") or [])
    ev = deny if type2(r.get("판단유형")) == "허가 불가능" and deny else \
        deny + list(r.get("근거조문") or []) + list(r.get("적용룰셋") or [])
    cond = "condition" in str(r.get("원판정") or "").lower() or "조건부" in str(r.get("원판정") or "")
    return derive_reason_code(r.get("판단유형"), ev, r.get("위반룰") or [], cond), "QA 변환"
```

### 2) `spec_eval.py`: `map_response()` 반환부

`return {` 를 `out = {` 로 바꾸고, 딕셔너리에 아래 두 줄을 추가합니다.

```python
        "사유코드_시스템": j.get("reason_code") or diag.get("reason_code") or rep.get("reason_code") or "",   # "원판정" 다음
        "위반룰": [c.get("rule_id") for c in j.get("conditions") or [] if c.get("status") == "fail" and c.get("rule_id")],  # "위반조건" 다음
```

닫는 `}` 뒤에 아래를 추가합니다.

```python
    out["사유코드"], out["사유코드_출처"] = reason_code_of(out)
    return out
```

### 3) `spec_eval.py`: `score_ai1()` 사례별 행

```python
        gr = (g.get("사유코드") or "") if g is not None else ""
        pr, src = (r["사유코드"], r.get("사유코드_출처") or "QA 변환") if isinstance(r.get("사유코드"), str) \
            and r.get("사유코드") else reason_code_of(r)                     # 이전 로그는 저장된 응답 필드로 변환
        rows.append(dict(..., 판단_일치=gv == pv,
                         정답_사유코드=gr, 예측_사유코드=pr, 사유코드_출처=src, 사유코드_일치=(gr == pr) if gr else None,
                         ...))
```

### 4) `spec_eval.py`: 템플릿·gold 변환·참조 조회

- `TEMPLATES["AI1"]["answer_ai1.csv"]` 컬럼: `["사례ID", "판단유형", "사유코드", "적용법령", "근거조문"]`, 예시 행에 `"PERM-ZONE-01"`
- `gold_to_ai1()`의 `an.append([cid, 판단유형, "", ...])`: 사유코드 칸은 비워 두고 사람이 기입
- `REF_COLS`에 `"참조_사유코드"`를 추가합니다(`"참조_룰엔진판정"` 다음).
- `fetch_ref()`의 `v = j.get("판정") or {}` 다음에 아래를 추가합니다.

```python
    fails = [x for x in v.get("evaluated_rules") or [] if x.get("status") == "fail"]
    ev = list(v.get("violations") or []) + [f"{x.get('source', '')} {x.get('natural', '')}" for x in fails]
    if v.get("decision") != "DENIED":                                   # 허용 사유(지구 특례 등)는 매트릭스 근거에서
        ev += [str(z.get("source") or "") + " " + str(z.get("zone") or "") for z in (v.get("matrix_gate") or {}).get("zone_results") or []]
```

  `out.update(...)`에 아래를 추가합니다.

```python
               참조_사유코드=derive_reason_code(REF_DECISION.get(v.get("decision"), ""), ev,
                                         [x.get("rule_id") for x in fails], v.get("decision") == "CONDITIONAL"),
```

### 5) 화면·진행표

- `spec_runner.py`의 `prog.append({...})`: `"판단유형": ...` 다음에 `"사유코드": rec.get("사유코드", ""),`
- `qa_app.py`의 `progress_panel()` 표시 컬럼 목록: `"판단유형"` 다음에 `"사유코드"`
- `spec_page.py` 사례 상세 `out = [...]` 위에 아래를 추가합니다.

```python
        code, src = (rec["사유코드"], rec.get("사유코드_출처") or "QA 변환") if isinstance(rec.get("사유코드"), str) \
            and rec.get("사유코드") else S.reason_code_of(rec)
```

  목록의 `("판단 유형", ...)` 다음에 `("사유 코드", f"{code} ({src})" if code else "-")`를 추가합니다.

## 복원 확인

```
python spec_runner.py AI1 --rescore spec/AI1/<시각> --answer spec_data/answer_ai1.csv --testset spec_data/testset_ai1.csv
```

`result.xlsx`의 사례별 시트에 정답_사유코드, 예측_사유코드, 사유코드_출처 컬럼이 나오면 복원된 것입니다.
