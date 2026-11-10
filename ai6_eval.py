"""
[AI 6] 자연어 기반 지도 제어 정확도 — 응답 해석 · ①②③ 판정 · 집계 · 로그
--------------------------------------------------------------------
공간오케(17006) 응답을 ai6_client.gather() 로 묶은 것(turn)을 받아 규격서 2-6 기준으로 판정합니다.

  ① 의도 추출   : 시스템 해석 의도(의도 대응표 적용) == 정답 의도 (집합 완전 일치 — 빠져도·남아도 오류)
  ② 파라미터    : 위치는 PNU 로, 분석대상(레이어·분석 종류)·반경·확대 레벨은 값으로 대조 (표기 차이 무시: '263번지' = '263')
  ③ 지도 제어   : 응답의 지도 제어 명령(이동·확대 등) + 표출 도형(대상 필지·분석 결과)이 정답 시나리오와 일치
  - 셋 다 맞아야 정상 수행 1건. 되묻기 = 오류. 무응답·대기 상한 초과·시스템 오류 = '응답 오류'
  - 오류 원인 = ① → ② → ③ 중 처음 어긋난 것 (응답 오류는 그 앞)
  - 정확도(%) = 정상 수행 / 전체 테스트 건수 × 100, 소수점 셋째자리 이하 버림 (반올림 없음: 2/3 → 66.66)
  - 판정에 쓰지 않는 값(로그만): 호출 순서, 중심 좌표·줌 힌트, 분석으로 산출된 면적·거리

의도 대응표(intent_map_ai6.csv)는 시험 전에 확정해 시험 중 바꾸지 않습니다. 파일이 없으면 DEFAULT_RULES 를 씁니다.
이 파일은 표준 라이브러리만 씁니다(pandas 없이 시험 가능).
"""
import csv
import io
import hashlib
import json
import re
import zipfile
from collections import Counter, OrderedDict
from xml.etree import ElementTree as ET

ITEM = "AI6"
TYPES = ["공간규제 분석", "도로 접근성 분석", "산지·농지 규제 분석"]
TYPE_COUNTS = {"공간규제 분석": 15, "도로 접근성 분석": 15, "산지·농지 규제 분석": 20}
CAUSES = ["의도 해석 오류", "핵심 파라미터 추출 오류", "GIS 지도 제어 실행 오류", "응답 오류"]
TARGET = 90.0
DEC = 2                                          # 소수점 셋째자리 이하 버림 (회의 안건 6 — 둘째자리로 정하면 1)
PARSE_LOG = "command_parse_log_ai6.jsonl"        # 명령 해석 로그 (의도·핵심 파라미터·실행 대상 기능)
GIS_LOG = "gis_execution_log_ai6.jsonl"          # 실행 결과 로그 (지도 제어 명령·표출 도형·정답 대조·판정)
REQUIRE_TARGET_GEOMETRY = True        # ③ 표출대상: SELECT 등 이동 명령에 대상 필지 도형이 실려야 함 (2026-11-10 실측: map.SELECT 에 MultiPolygon)
# 규격: '중심 좌표·줌 레벨 … 로그에 기록하되 판정에 사용하지 않음(명령에 포함된 반경·거리 등 분석 조건 값은 ②에서 판정)'
# → 확대 레벨 숫자는 참고로만 기록. 확대 명령이 나갔는지는 ③ 지도제어=확대 로 판정
JUDGE_ZOOM_LEVEL = False                  # True 면 '대상 필지 도형'에 도형 원본(geometry)까지 요구 — 기본은 PNU 로 지정된 선택·이동 명령
ASK_CAUSE = "order"                             # 되묻기 원인: reason = 사유가 위치 후보 여럿·필요한 값 없음이면 '핵심 파라미터 추출 오류', 그 밖은 ①→②→③ · param = 늘 파라미터
ASK_PARAM_REASONS = {"ambiguous_candidates", "missing_required_value"}
RADIUS_OPS = {"BUFFER", "LAYER_NEARBY"}
RADIUS_EPS = 1e-6                     # 반경은 단위만 맞추고(0.3km = 300m) 값은 같아야 함 — 규격: 표기만 다른 경우만 일치
UNLOCATED_RULE = False                # True 면 '위치 미확정'(분석 미시작·PNU 없음)을 시스템 실패=GIS / 못 찾음=파라미터로 돌림.
#                                       규격(①→②→③ 처음 불일치)과 다른 운영 정의라 기본은 끔 — 세부 원인으로만 기록
SEARCH_OPS = {"INTEGRATED_LOCATION_SEARCH", "LEGAL_BOUNDARY_SEARCH", "ROAD_SEARCH", "GEOCODE", "GEOCODE_NORMALIZE"}
LAYER_ANALYSIS_OPS = {"INTERSECTIONLAYER", "LAYER_NEARBY"}                  # 명령에 든 반경이 넘어가는 분석 — 정답에 반경이 없는데 쓰면 ② 오류
# ② 레이어 자료 대조 — 이름이 맞아도 고른 레이어 ID 가 그 레이어의 자료 테이블이 아니면 ② 오류.
# 2026-11-10 공간 DB 확인: BE 레이어 검색이 '지구단위계획구역'을 도시계획시설(도로) 테이블 lsmd_cont_uq161 에 연결해
# 반경·교차 분석이 도로 도형을 '지구단위계획구역'으로 표출함. 허용 = 그 레이어 자료만 든 테이블(레이어 목록 aips_lyer · 코드표)
LAYER_ID_CHECK = True
LAYER_TABLES = {"지구단위계획구역": {"klip_c_uq161", "upis_c_uq161"},          # lsmd_cont_uq141 은 성장관리계획구역 등이 섞여 있어 제외
                "토지거래계약에관한허가구역": {"klip_c_uq175"},
                "문화유산보호": {"klip_c_uo301", "lsmd_cont_uo301"}}

DOTS = "·ㆍ・‧∙⋅･•"
MOVE_ACTIONS = {"SELECT", "MOVE_TO", "CENTER", "MOVE_AND_ZOOM", "FIT_BOUNDS"}
# 지도제어(③) 정답 값 → 지도 동작. 여럿이면 ',' 로 적고 모두 있어야 함. MOVE_AND_ZOOM(이동+확대 한 번에)은 줌 값이 있으면 확대·축소로도 인정
MAP_CONTROL = OrderedDict([("확대", {"SET_ZOOM", "ZOOM_IN", "MOVE_AND_ZOOM"}), ("축소", {"SET_ZOOM", "ZOOM_OUT", "MOVE_AND_ZOOM"}),
                           ("이동", MOVE_ACTIONS), ("레이어 표시", {"SHOW_LAYER"})])
LOCATE_OPS = {"INTEGRATED_LOCATION_SEARCH", "LEGAL_BOUNDARY_SEARCH", "ROAD_SEARCH", "SELECT", "GEOCODE", "GEOCODE_NORMALIZE"}

# ---------------------------------------------------------------- 의도 대응표 (기본값 — intent_map_ai6.csv 가 있으면 그것을 씀)
# 근거: 호출 = 'callee:OPERATION' (operation/action 값) · 동작 = 지도 동작 type · 표출 = 'geom'(분석 호출이 도형을 돌려줌)
#       인텐트 = 공간오케 workflow 이벤트의 의도 코드(classified·called) — 기본값에는 넣지 않음(실행 근거만으로 판정)
# 정답의도 '(무시)' = 의도로 세지 않는 보조 신호
DEFAULT_RULES = [
    ("지도 이동", "호출", "map_navigation_b:INTEGRATED_LOCATION_SEARCH|map_navigation_b:SELECT|map_navigation_b:MOVE_TO|"
                         "map_navigation_b:CENTER|map_navigation_b:MOVE_AND_ZOOM|map_navigation_b:FIT_BOUNDS|map_navigation_b:HIGHLIGHT|"
                         "map_navigation_b:ADD_MARKER",
     "주소가 있으면 공간오케가 위치 확정(검색 → SELECT)을 자동으로 붙임. 대상 필지 강조(SELECT_FEATURE → HIGHLIGHT)·마커(MAP_MARKER)도 여기에 흡수"),
    ("지도 이동", "동작", "SELECT|MOVE_TO|CENTER|MOVE_AND_ZOOM|FIT_BOUNDS", "화면 이동 명령"),
    ("지도 확대/축소", "호출", "map_navigation_b:SET_ZOOM|map_navigation_b:ZOOM_IN|map_navigation_b:ZOOM_OUT|map_navigation_b:MOVE_AND_ZOOM",
     "MOVE_AND_ZOOM(이동+확대를 한 번에 — 일반 모드 map_navigation_b)은 지도 이동과 함께 셈"),
    ("지도 확대/축소", "동작", "SET_ZOOM|ZOOM_IN|ZOOM_OUT|MOVE_AND_ZOOM", ""),
    ("규제 저촉 분석", "호출", "land_attr_b:OVERLAP_GEOMETRY|land_attr_b:OVERLAP_PLAN|spatial_regulation:INTERSECTIONLAYER",
     "용도지역·지구 중첩 · 지구단위계획구역 · 규제 레이어 저촉"),
    ("주변 규제 분석", "호출", "spatial_regulation:LAYER_NEARBY", "반경 안 규제 레이어 잘라 보기"),
    ("버퍼 분석", "호출", "spatial_regulation:BUFFER", ""),
    ("도로 접근성 분석", "호출", "road_access:CONTACT_SUMMARY|road_access:CONTACT_GEOM", ""),
    ("규제도로 분석", "호출", "road_access:REGULATION|road_access:ROAD_PLAN_GEOM",
     "REGULATION 은 도형 없음. 운영 연결표의 road_plan 갈래(ROAD_PLAN_GEOM)도 같은 의도로 인정"),
    ("산지전용 검토", "호출", "land_regulation:FOREST_CONVERSION", "도형 = 입력 필지 윤곽"),
    ("개발가능지 추출", "호출", "land_regulation:DEVELOPABLE_AREA", "개발가능 영역 0이면 도형 없음"),
    ("분석 결과 표출", "표출", "ANALYSIS", "위치 확정이 아닌 분석 호출을 계획·실행함 — 도형이 실제로 왔는지는 ③에서 봄"),
    ("분석 결과 표출", "동작", "SHOW_LAYER", "레이어 켜기"),
    ("(무시)", "호출", "spatial_regulation:LAYER_FIND", "레이어 id 찾기 — 교차·반경 분석의 앞 단계"),
    ("(무시)", "호출", "land_attr_b:ATTR|map_navigation_b:PNU_RESOLVE",
     "보조 호출 — 토지이용계획 조회(규격 Function 예시 '토지이용계획 조회') · PNU 확정. 의도로 세지 않음"),
    ("(무시)", "동작", "UI.FOCUS|ADD_MARKER|HIGHLIGHT", "검색 1건일 때 붙는 마커 등 — 지도 이동에 흡수"),
]
# 분석대상(②) — 정답 토큰 → 시스템 operation. 여기에 없는 토큰은 레이어 이름(lyr_query)으로 대조
TARGET_OPS = OrderedDict([
    ("용도지역·지구", {"OVERLAP_GEOMETRY"}),
    ("지구단위계획구역", {"OVERLAP_PLAN"}),
    ("접도", {"CONTACT_SUMMARY", "CONTACT_GEOM"}),
    ("규제도로", {"REGULATION", "ROAD_PLAN_GEOM"}),
    ("산지전용 규제 면적", {"FOREST_CONVERSION"}),
    ("산지전용", {"FOREST_CONVERSION"}),
    ("산지전용 가능성", {"FOREST_CONVERSION"}),
    ("개발가능지", {"DEVELOPABLE_AREA"}),
])
# 분석결과표출(③) — 정답 토큰 안의 낱말 → (operation, 도형 키). 위에서부터 처음 맞는 규칙
SCENARIO_RULES = [                                               # '버퍼'·'반경'이 먼저, '저촉'은 대상 낱말 뒤 — '반경 100m 버퍼 도형'은 버퍼,
    ("버퍼", {"BUFFER"}, ("result_geojson",)),                   # '반경 300m 내 지구단위계획구역 도형'은 반경 레이어 분석
    ("반경", {"LAYER_NEARBY"}, ("result_geojson",)),
    ("용도지역·지구", {"OVERLAP_GEOMETRY"}, ("result_geojson",)),
    ("지구단위계획구역", {"OVERLAP_PLAN", "INTERSECTIONLAYER"}, ("result_geojson",)),
    ("저촉", {"INTERSECTIONLAYER"}, ("result_geojson",)),
    ("접도선", {"CONTACT_SUMMARY", "CONTACT_GEOM"}, ("road_contact_geojson",)),
    ("산지전용", {"FOREST_CONVERSION"}, ("result_geojson",)),
    ("개발가능", {"DEVELOPABLE_AREA"}, ("result_geojson",)),
]
FUNCTION_LABEL = {                               # 실행 대상 기능 표기 (규격 출력 예시의 'Function')
    "INTEGRATED_LOCATION_SEARCH": "주소 검색(지오코딩)", "SELECT": "지도 이동(대상 필지 선택)", "MOVE_TO": "지도 이동",
    "SET_ZOOM": "지도 확대/축소", "ZOOM_IN": "지도 확대", "ZOOM_OUT": "지도 축소",
    "OVERLAP_GEOMETRY": "용도지역·지구 중첩 조회", "OVERLAP_PLAN": "지구단위계획구역 중첩 조회", "LAYER_FIND": "레이어 검색",
    "INTERSECTIONLAYER": "레이어 저촉(교차) 분석", "LAYER_NEARBY": "반경 내 레이어 분석", "BUFFER": "버퍼 분석",
    "CONTACT_SUMMARY": "접도 분석", "REGULATION": "규제도로 분석", "FOREST_CONVERSION": "산지전용 규제 분석",
    "DEVELOPABLE_AREA": "개발가능지 추출", "ATTR": "토지 정보 조회", "NEARBY_PARCELS": "반경 내 필지 검색",
}
TYPE_OF_CALLEE = {"land_attr_b": "공간규제 분석", "spatial_regulation": "공간규제 분석", "road_access": "도로 접근성 분석",
                  "land_regulation": "산지·농지 규제 분석"}


# ---------------------------------------------------------------- 표기 정리
def norm(s):
    """대조용: 공백·가운뎃점·쉼표 제거, 소문자, 끝의 '번지' 제거"""
    s = re.sub(f"[\\s{DOTS},]", "", str("" if s is None else s)).lower()
    return re.sub(r"번지$", "", s)


def canon_dot(s):
    """표시용: 가운뎃점 변형(ㆍ・‧ 등)을 '·' 하나로"""
    return re.sub(f"[{DOTS}]", "·", str("" if s is None else s)).strip()


def canon_type(s):
    s = re.sub(r"\s+", " ", canon_dot(s))
    for t in TYPES:
        if norm(t) == norm(s):
            return t
    return s


def pnu_of(v):
    d = re.sub(r"\D", "", str("" if v is None else v))
    return d if len(d) == 19 else ""


def meters(v):
    """'300m' · '0.3km' · '300미터' · '1킬로미터' · 300 → 300.0 (못 읽으면 None)"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        return meters(f"{v.get('value', '')}{v.get('unit', 'm')}")
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(km|킬로미터|킬로|m|미터)?", str(v), re.I)
    if not m:
        return None
    x = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "m").lower()
    return x * 1000 if unit in ("km", "킬로미터", "킬로") else x


def floor_pct(n, d, dec=DEC):
    """정수 연산으로 버림 — 부동소수 오차 없이 2/3 → 66.66"""
    if not d:
        return None
    k = 10 ** dec
    return (n * 100 * k // d) / k


def split_list(v, seps=";\n"):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in re.split(f"[{re.escape(seps)}]", str(v)) if x.strip()]


def parse_kv(v):
    """'키=값; 키=값' → OrderedDict (값 안의 ',' 는 그대로 둠 — 여러 값은 ',' 로 적음)"""
    out = OrderedDict()
    for part in split_list(v):
        k, sep, x = part.partition("=")
        if not sep:
            k, sep, x = part.partition(":")
        out[k.strip()] = x.strip()
    return out


# ---------------------------------------------------------------- 표 읽기 (CSV · XLSX, pandas 없이)
RAW_COLS = ("명령문",)                                             # 시스템에 그대로 보내는 칸 — 읽을 때 손대지 않음


def read_rows(path, sheet=None):
    """CSV(UTF-8(BOM 허용) 또는 CP949) 또는 XLSX 의 첫 시트(또는 이름이 sheet 인 시트) → [dict]. 모든 값은 문자열"""
    if not path:
        return []
    if str(path).lower().endswith((".xlsx", ".xlsm")):
        return _read_xlsx(path, sheet)
    with open(path, "rb") as f:
        data = f.read()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:                                     # 엑셀 'CSV (쉼표로 분리)' 저장 = CP949 (spec_eval.read_table 과 같게)
        text = data.decode("cp949")
    rows = list(csv.DictReader(io.StringIO(text)))
    return [{(k or "").strip(): ((v or "") if (k or "").strip() in RAW_COLS else (v or "").strip())
             for k, v in r.items() if k is not None} for r in rows]


def sheet_names(path):
    """XLSX 시트 이름 목록 (순서대로)"""
    M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    wb = ET.fromstring(zipfile.ZipFile(path).read("xl/workbook.xml"))
    return [s.get("name") for s in wb.find("{%s}sheets" % M)]


def _read_xlsx(path, sheet=None):
    M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ns = {"m": M}
    z = zipfile.ZipFile(path)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            ss.append("".join(t.text or "" for t in si.iter("{%s}t" % M)))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    sheets = [(s.get("name"), rels[s.get("{%s}id" % R)]) for s in wb.find("m:sheets", ns)]
    name, target = next(((n, t) for n, t in sheets if sheet and n == sheet), sheets[0])
    target = target[1:] if target.startswith("/") else "xl/" + target
    grid = []
    for r in ET.fromstring(z.read(target)).find("m:sheetData", ns):
        row = {}
        for c in r:
            col = re.match(r"[A-Z]+", c.get("r")).group(0)
            t, v = c.get("t"), c.find("m:v", ns)
            if t == "s" and v is not None:
                val = ss[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % M))
            else:
                val = v.text if v is not None else ""
            row[col] = val
        grid.append(row)
    if not grid:
        return []
    head = grid[0]
    cols = sorted(head, key=lambda c: (len(c), c))
    out = []
    for row in grid[1:]:
        d = {str(head[c]).strip(): (str(row.get(c, "") or "") if str(head[c]).strip() in RAW_COLS else str(row.get(c, "") or "").strip())
             for c in cols if str(head[c]).strip()}
        if any(d.values()):
            out.append(d)
    return out


RULE_KINDS = ("호출", "동작", "표출", "인텐트")                    # 대응표 근거 종류


def load_rules(path=None):
    """의도 대응표 → [{intent, kind, values(set), note}]"""
    raw = read_rows(path) if path else [dict(정답의도=a, 근거=b, 값=c, 비고=d) for a, b, c, d in DEFAULT_RULES]
    rules = []
    for r in raw:
        intent = canon_dot(r.get("정답의도") or r.get("정답 의도") or "")
        kind = (r.get("근거") or "").strip()
        vals = {v.strip().upper() if kind != "호출" else _call_key(v) for v in str(r.get("값") or "").split("|") if v.strip()}
        if intent and kind and vals:
            rules.append(dict(intent=intent, kind=kind, values=vals, note=r.get("비고", "")))
    if path:                                                       # 형식이 다른 표를 넣으면 규칙 0개 → 전부 의도 오류가 되므로 막음
        cols = list(raw[0]) if raw else []
        if not rules:
            raise ValueError("의도 대응표에서 규칙을 하나도 읽지 못했어요 — '정답의도 · 근거 · 값 · 비고' 칸이 필요해요 "
                             f"(지금 칸: {', '.join(cols) or '없음'}). 📂 시험 데이터에서 양식을 내려받아 쓰세요.")
        bad = sorted({r["kind"] for r in rules} - set(RULE_KINDS))
        if bad:
            raise ValueError(f"의도 대응표 '근거' 값은 {' · '.join(RULE_KINDS)} 중 하나여야 해요 (지금: {', '.join(bad)})")
    return rules


def _call_key(v):
    callee, _, op = str(v).strip().partition(":")
    return f"{callee.strip()}:{op.strip().upper()}"


def rules_csv(rules=None):
    """의도 대응표를 CSV 문자열로 (양식 내려받기·시험 데이터 폴더 저장용)"""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["정답의도", "근거", "값", "비고"])
    for a, b, c, d in (rules or DEFAULT_RULES):
        w.writerow([a, b, c, d])
    return buf.getvalue()


# ---------------------------------------------------------------- 응답 해석
def _plan_args(call):
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    plan = ((args.get("_metadata") or {}).get("plan") or []) if isinstance(args.get("_metadata"), dict) else []
    for step in plan:
        if isinstance(step, dict) and isinstance(step.get("args"), dict):
            return step["args"], step.get("tool")
    return {k: v for k, v in args.items() if not str(k).startswith("_")}, None


def _resolve(call):
    pay = call.get("payload") if isinstance(call.get("payload"), dict) else {}
    res = pay.get("resolve") if isinstance(pay.get("resolve"), dict) else {}
    return pay, res


def _plan_step(call):
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    plan = ((args.get("_metadata") or {}).get("plan") or []) if isinstance(args.get("_metadata"), dict) else []
    return next((st for st in plan if isinstance(st, dict)), {})


def call_op(call):
    """operation — 계획 항목 {tool, operation, args} 의 operation → args.operation/action → resolve.input.operation …
    계획이 없는 호출(코어 LLM 이 부른 normal 모드)에서 아무것도 못 찾으면 NORMAL"""
    a, _ = _plan_args(call)
    _, res = _resolve(call)
    inp = res.get("input") if isinstance(res.get("input"), dict) else {}
    op = (_plan_step(call).get("operation") or a.get("operation") or a.get("action") or inp.get("operation")
          or res.get("action") or res.get("operation") or "")
    return str(op).strip().upper() or "NORMAL"


def nonempty_geom(v):
    if not v:
        return False
    if isinstance(v, list):
        return any(nonempty_geom(x) for x in v)
    if isinstance(v, dict):
        if v.get("type") == "FeatureCollection":
            return bool(v.get("features"))
        if v.get("type") == "Feature":
            return nonempty_geom(v.get("geometry"))
        if v.get("type") == "GeometryCollection":
            return bool(v.get("geometries"))
        if "coordinates" in v:
            return bool(v.get("coordinates"))
        return any(nonempty_geom(x) for x in v.values() if isinstance(x, (dict, list)))
    if isinstance(v, str):
        return bool(v.strip()) and not v.strip().upper().endswith("EMPTY")
    return False


def _geom_count(v):
    if isinstance(v, list):
        return sum(_geom_count(x) for x in v)
    if isinstance(v, dict) and v.get("type") == "FeatureCollection":
        return len(v.get("features") or [])
    return 1 if nonempty_geom(v) else 0


def _crs(v):
    if isinstance(v, list):
        for x in v:
            c = _crs(x)
            if c:
                return c
        return ""
    if isinstance(v, dict):
        c = v.get("crs")
        if isinstance(c, dict):
            return str((c.get("properties") or {}).get("name") or "")
        return str(c or "")
    return ""


def call_summary(call):
    a, tool = _plan_args(call)
    pay, res = _resolve(call)
    if tool is None:                                               # 계획 없는 호출(코어 LLM 경로)은 args 가 {query} 뿐 — 반경 등 분석 조건은 resolve.input 에만 옴
        inp = res.get("input") if isinstance(res.get("input"), dict) else {}
        a = {**{k: inp[k] for k in ("distance", "radius", "lyr_id", "lyr_query") if inp.get(k) not in (None, "")}, **a}
    geom = res.get("geom") if isinstance(res.get("geom"), dict) else {}
    result = res.get("result") if isinstance(res.get("result"), dict) else {}
    pnu = pnu_of(a.get("featureRef") or a.get("pnu")) or pnu_of(result.get("pnu")) or pnu_of(res.get("pnu"))
    errors = pay.get("errors") or []
    return OrderedDict([
        ("call_id", call.get("call_id")), ("callee", call.get("callee")), ("tool", tool or call.get("tool")),
        ("operation", call_op(call)), ("status", call.get("status")), ("reason_code", call.get("reason_code")),
        ("elapsed_ms", call.get("elapsed_ms")), ("args", a),
        ("payload_status", pay.get("status")), ("resolve_status", res.get("status")),
        ("pnu", pnu), ("geom", {k: _geom_count(v) for k, v in geom.items() if nonempty_geom(v)}),
        ("geom_crs", {k: _crs(v) for k, v in geom.items() if nonempty_geom(v)}),
        ("errors", errors), ("error", call.get("error")),
    ])


def call_ok(cs):
    """호출이 결과를 냈나 — call.finished 상태 ok, 도구 오류 없음, resolve 상태가 실패·되묻기가 아님"""
    if str(cs.get("status") or "").lower() != "ok" or cs.get("errors") or cs.get("error"):
        return False
    if str(cs.get("payload_status") or "ok").lower() in ("failed", "error", "clarify"):
        return False
    return str(cs.get("resolve_status") or "ok").lower() in ("ok", "success", "")


_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")
ACTION_ALIAS = {"flyto": "MOVE_TO", "moveto": "MOVE_TO", "panto": "MOVE_TO", "setcenter": "CENTER", "center": "CENTER",
                "focusparcel": "SELECT", "selectfeature": "SELECT", "select": "SELECT", "highlight": "HIGHLIGHT",
                "zoomto": "SET_ZOOM", "setzoom": "SET_ZOOM", "zoomin": "ZOOM_IN", "zoomout": "ZOOM_OUT",
                "fitbounds": "FIT_BOUNDS", "moveandzoom": "MOVE_AND_ZOOM", "addmarker": "ADD_MARKER",
                "showlayer": "SHOW_LAYER", "hidelayer": "HIDE_LAYER", "layerclear": "LAYER_CLEAR", "clearmap": "RESET_MAP", "resetmap": "RESET_MAP",
                "panby": "PAN", "pan": "PAN"}


def action_type(item):
    t = str(item.get("type") or "")
    if t.lower().startswith("ui."):
        return t.upper()
    base = t[4:] if t.startswith("map.") else t
    key = re.sub(r"[^a-z]", "", base.lower())
    if key == "zoomby":
        d = (item.get("result") or {}).get("delta", item.get("delta", 0)) if isinstance(item.get("result"), dict) else item.get("delta", 0)
        try:
            return "ZOOM_IN" if float(d) > 0 else "ZOOM_OUT"
        except (TypeError, ValueError):
            return "SET_ZOOM"
    if key.startswith("move") and key[4:] in ("left", "right", "up", "down"):
        return "PAN"
    if key in ACTION_ALIAS:
        return ACTION_ALIAS[key]
    if re.fullmatch(r"[A-Z0-9_]+", base):
        return base
    return _CAMEL.sub("_", base).upper()


def _field(item, *keys):
    for src in (item, item.get("result") if isinstance(item.get("result"), dict) else {}, item.get("params") or {}):
        if isinstance(src, dict):
            for k in keys:
                if src.get(k) not in (None, ""):
                    return src.get(k)
    return None


def action_summary(item):
    layers = _field(item, "layers") or []
    return OrderedDict([
        ("type", action_type(item)), ("raw_type", item.get("type")), ("call_id", item.get("_call_id")),
        ("pnu", pnu_of(_field(item, "pnu", "featureRef"))), ("zoom", _field(item, "zoomLevel", "zoom", "level")),
        ("zoom_hint", _field(item, "zoomHint")), ("lng", _field(item, "lng", "x")), ("lat", _field(item, "lat", "y")),
        ("crs", _field(item, "crs", "srs")), ("has_geometry", nonempty_geom(_field(item, "geometry"))),
        ("layers", [l.get("name") or l.get("id") for l in layers if isinstance(l, dict)] if isinstance(layers, list) else []),
    ])


OP_LABEL = {"BUFFER": "버퍼"}                                     # 규격 출력의 '분석대상' 표기 (판정에는 안 씀)
for _k, _ops in TARGET_OPS.items():
    for _o in _ops:
        OP_LABEL.setdefault(_o, _k)


def target_labels(calls, layers, chosen=None):
    """규격 출력 '분석대상' — 분석 호출 operation 을 정답지 낱말(용도지역·지구 · 접도 …)로, 레이어 분석은 고른 레이어 이름으로"""
    out = []
    names = {c.get("lyr_id"): layer_name(c.get("name")) for c in chosen or [] if c.get("name")}
    for c in calls:
        if c["callee"] == "map_navigation_b" or c["operation"] in LOCATE_OPS or c["operation"] == "LAYER_FIND":
            continue
        a = c["args"] or {}
        lab = OP_LABEL.get(c["operation"]) or names.get(a.get("lyr_id")) \
            or next((str(a[k]) for k in ("lyr_query", "layer", "lyr_name") if a.get(k)), None) or f"{c['callee']}:{c['operation']}"
        if lab not in out:
            out.append(lab)
    for l in ([] if names else layers):                            # 고른 레이어를 알면 검색어는 빼고
        if l not in out:
            out.append(l)
    return out


def extract(turn, rules=None):
    """gather() 결과 → 판정 재료 + 규격 출력 JSON(의도·핵심파라미터·실행대상기능·지도상태)"""
    rules = rules if rules is not None else load_rules()
    calls = [call_summary(c) for c in turn.get("calls") or []]
    actions = [action_summary(a) for a in turn.get("actions") or []]
    by_kind = {}
    for r in rules:
        by_kind.setdefault(r["kind"], []).append(r)

    intents, evidence, unmapped = [], [], []

    def add(intent, kind, value):
        if intent == "(무시)":
            return
        evidence.append({"의도": intent, "근거": kind, "값": value})
        if intent not in intents:
            intents.append(intent)

    ignored = {v for r in by_kind.get("호출", []) if r["intent"] == "(무시)" for v in r["values"]}
    analysis = []                                                  # 위치 확정·보조가 아닌 호출 (계획된 분석)
    for cs in calls:                                               # 의도 = 시스템이 계획해 부른 것(성공 여부와 무관) — 결과는 ③에서 봄
        if not cs.get("callee"):
            continue
        key = f"{cs['callee']}:{cs['operation']}"
        if key in ignored:
            continue
        if cs["callee"] != "map_navigation_b" and cs["operation"] not in LOCATE_OPS:
            analysis.append(cs)
        hit = [r for r in by_kind.get("호출", []) if key in r["values"]]
        for r in hit:
            add(r["intent"], "호출" if call_ok(cs) else "호출(실패)", key)
        if not hit:
            unmapped.append(key)
    for a in actions:
        hit = [r for r in by_kind.get("동작", []) if a["type"] in r["values"]]
        for r in hit:
            add(r["intent"], "동작", a["type"])
    geom_calls = [c for c in calls if call_ok(c) and c["geom"] and c["operation"] not in LOCATE_OPS]
    for r in by_kind.get("표출", []):
        if "ANALYSIS" in r["values"] and analysis:
            add(r["intent"], "표출", ", ".join(f"{c['callee']}:{c['operation']}" for c in analysis))
        if "GEOM" in r["values"] and geom_calls:
            add(r["intent"], "표출", ", ".join(f"{c['callee']}:{c['operation']}" for c in geom_calls))
    wf = turn.get("workflow") or {}
    codes = (wf.get("intents") or {}) if isinstance(wf.get("intents"), dict) else {}
    for r in by_kind.get("인텐트", []):
        for code in set(codes.get("classified") or []) | set(codes.get("called") or []):
            if str(code).upper() in r["values"]:
                add(r["intent"], "인텐트", str(code))
    for key in unmapped:                                           # 대응표에 없는 성공 호출 = 정답에 없는 의도 (① 오류)
        intents.append(f"?{key}")

    ok_calls = [c for c in calls if call_ok(c)]
    pnus, search_pnus = [], []
    for c in calls:
        if c["pnu"] and c["operation"] in SEARCH_OPS:             # 검색 결과 첫 후보 — 대상이 아닐 수 있어 기록만
            search_pnus.append(c["pnu"])
        elif c["pnu"]:
            pnus.append(("호출 " + f"{c['callee']}:{c['operation']}", c["pnu"]))
    catalog = {}                                                   # LAYER_FIND 결과의 레이어 목록 → id: 이름
    for raw in turn.get("calls") or []:
        if call_op(raw) == "LAYER_FIND":
            catalog.update(_layer_catalog(raw))
    chosen = [{"operation": c["operation"], "lyr_id": (c["args"] or {}).get("lyr_id"),
               "name": catalog.get(str((c["args"] or {}).get("lyr_id")))}
              for c in calls if c["operation"] in LAYER_ANALYSIS_OPS]
    for a in actions:
        if a["pnu"]:
            pnus.append(("지도 동작 " + a["type"], a["pnu"]))
    layers, dists, zooms = [], [], []
    for c in calls:
        a = c["args"] or {}
        for k in ("lyr_query", "layer", "lyr_name"):
            if a.get(k):
                layers.append(str(a[k]))
        for k in ("distance", "radius", "setback_distance"):
            m = meters(a.get(k))
            if m is not None:
                dists.append(m)
        if c["operation"] in ("SET_ZOOM",) and a.get("zoomLevel") not in (None, ""):
            zooms.append(str(a.get("zoomLevel")))
    for a in actions:
        if a["type"] == "SET_ZOOM" and a["zoom"] not in (None, ""):
            zooms.append(str(a["zoom"]))
    ops = [c["operation"] for c in calls if c["operation"] not in LOCATE_OPS]   # ②는 '무엇을 하려 했나'(성공 여부는 ③)
    functions = []
    for c in calls:
        label = FUNCTION_LABEL.get(c["operation"], f"{c['callee']}:{c['operation']}") + ("" if call_ok(c) else " (실패)")
        if label not in functions:
            functions.append(label)
    if geom_calls or any(a["type"] == "SHOW_LAYER" for a in actions):
        functions.append("지도 표출")
    main = next((c for c in ok_calls if c["callee"] in TYPE_OF_CALLEE and c["operation"] not in ("LAYER_FIND",)), None)
    move = [a for a in actions if a["type"] in MOVE_ACTIONS]
    asked = bool(turn.get("asks")) or str(turn.get("status") or "").lower() == "asking"
    spec_output = OrderedDict([
        ("의도", [i for i in intents if not i.startswith("?")]),
        ("핵심파라미터", OrderedDict([
            ("위치", next((str((c["args"] or {}).get("query")) for c in calls if (c["args"] or {}).get("query")), "")),
            ("PNU", sorted({p for _, p in pnus})), ("분석대상", target_labels(calls, layers, chosen)),
            ("반경(m)", sorted(set(dists))), ("확대레벨", sorted(set(zooms)))])),
        ("실행대상기능", OrderedDict([("명령유형", TYPE_OF_CALLEE.get(main["callee"], "") if main else ""),
                                ("Function", functions)])),
        ("지도상태", OrderedDict([
            ("지도제어명령", [a["type"] for a in actions if not a["type"].startswith("UI.")]),
            ("표출대상", [f"필지 {a['pnu']}" for a in move if a["pnu"]]),
            ("분석결과표출", [f"{c['operation']} {k}×{n}" for c in geom_calls for k, n in c["geom"].items()])])),
    ])
    return OrderedDict([
        ("calls", calls), ("actions", actions), ("intents", intents), ("intent_evidence", evidence),
        ("unmapped_calls", unmapped), ("intent_codes", codes), ("pnus", pnus), ("layers", layers),
        ("distances", dists), ("zooms", zooms), ("ops", ops), ("geom_calls", [c["call_id"] for c in geom_calls]),
        ("analysis_calls", [c["call_id"] for c in analysis]),
        ("chosen_layers", chosen), ("search_pnus", search_pnus),
        ("radius", [m for c in calls if c["operation"] in RADIUS_OPS
                    for m in [meters((c["args"] or {}).get(k)) for k in ("distance", "radius")] if m is not None]),
        ("asked", asked), ("asks", turn.get("asks") or []), ("status", turn.get("status")),
        ("stop_reason", turn.get("stop_reason")), ("answer", turn.get("answer") or ""), ("spec_output", spec_output),
    ])


# ---------------------------------------------------------------- 판정
def response_error(res, turn):
    """'응답 오류' 사유 — 없으면 ''"""
    ended = (res or {}).get("ended_by")
    if ended == "deadline":
        return (res or {}).get("error") or "응답 대기 상한 초과"
    if ended in ("http", "exception"):
        return (res or {}).get("error") or ended
    if ended == "error" or (turn or {}).get("errors"):
        return "시스템 오류 이벤트: " + json.dumps(((turn or {}).get("errors") or [{}])[0], ensure_ascii=False)[:200]
    if ended == "eof" and not (turn or {}).get("finished"):
        return "응답 중단 (turn.finished 없이 연결 종료)"
    if str((turn or {}).get("status") or "").lower() in ("failed", "cancelled", "canceled", "error"):
        return f"턴 상태 {turn.get('status')} ({turn.get('stop_reason') or ''})"
    return ""


ALL_TARGET_OPS = set().union(*TARGET_OPS.values())


def layer_name(name):
    """'지구단위계획구역 · 용도지역지구' → '지구단위계획구역' (공간오케 layer_choice 가 비교하는 부분)"""
    return str(name or "").split(" · ")[0].strip()


def _layer_catalog(call):
    """LAYER_FIND 호출 결과에서 레이어 목록을 찾아 {id: 이름} — 결과가 resolve.result · resolve · payload 어디에 있든"""
    pay, res = _resolve(call)
    out = {}
    for box in (res.get("result"), res, pay):
        items = box.get("layers") if isinstance(box, dict) else None
        if isinstance(items, list):
            for x in items:
                if isinstance(x, dict):
                    i = x.get("id") or x.get("lyr_id")
                    if i:
                        out[str(i)] = str(x.get("name") or x.get("lyr_nm") or x.get("label") or i)
    return out


def layer_id_problem(tok, ext):
    """② 레이어 자료 대조 — 정답 레이어 tok 를 분석한 호출의 lyr_id 중 그 레이어 자료 테이블이 아닌 것 (없으면 [])"""
    allowed = next((v for k, v in LAYER_TABLES.items() if norm(k) == norm(tok)), None) if LAYER_ID_CHECK else None
    if not allowed:
        return []
    rows = [c for c in ext.get("chosen_layers") or [] if c.get("lyr_id")]
    named = [c for c in rows if c.get("name")
             and norm(tok) in {norm(layer_name(c["name"])), norm(layer_name(c["name"]).split("/")[-1])}]
    use = named or [c for c in rows if not c.get("name")]          # 이름을 못 찾은 경우에만 이름 없는 호출로
    return sorted({str(c["lyr_id"]) for c in use if str(c["lyr_id"]).lower() not in allowed})


def criteria_fingerprint():
    """판정 기준(분석대상·시나리오 대조 규칙과 판정 옵션)의 SHA-256 — 대응표와 함께 동결·기록"""
    blob = json.dumps({"TARGET_OPS": {k: sorted(v) for k, v in TARGET_OPS.items()},
                       "SCENARIO_RULES": [[a, sorted(b), list(c)] for a, b, c in SCENARIO_RULES],
                       "MAP_CONTROL": {k: sorted(v) for k, v in MAP_CONTROL.items()},
                       "LOCATE_OPS": sorted(LOCATE_OPS), "MOVE_ACTIONS": sorted(MOVE_ACTIONS), "RADIUS_OPS": sorted(RADIUS_OPS),
                       "REQUIRE_TARGET_GEOMETRY": REQUIRE_TARGET_GEOMETRY, "JUDGE_ZOOM_LEVEL": JUDGE_ZOOM_LEVEL,
                       "ASK_CAUSE": ASK_CAUSE, "ASK_PARAM_REASONS": sorted(ASK_PARAM_REASONS), "DEC": DEC, "TARGET": TARGET,
                       "UNLOCATED_RULE": UNLOCATED_RULE, "RADIUS_EPS": RADIUS_EPS, "SEARCH_OPS": sorted(SEARCH_OPS),
                       "LAYER_ANALYSIS_OPS": sorted(LAYER_ANALYSIS_OPS), "LAYER_ID_CHECK": LAYER_ID_CHECK,
                       "LAYER_TABLES": {k: sorted(v) for k, v in LAYER_TABLES.items()}},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def gold_pnu_of(gold):
    """정답 PNU — 정답파라미터 안의 'PNU=' (글자라 엑셀이 안 건드림) 우선, 없으면 참조PNU 칸"""
    return pnu_of(parse_kv(gold.get("정답파라미터")).get("PNU")) or pnu_of(gold.get("참조PNU"))


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _control_done(a, v):
    """③ 지도제어 값 v 를 지도 동작 a 가 했나 — MOVE_AND_ZOOM 은 줌 값이 있어야 확대·축소, 레이어 표시는 켠 레이어가 있어야 함"""
    if a["type"] not in MAP_CONTROL.get(v, ()):
        return False
    if a["type"] == "MOVE_AND_ZOOM" and v in ("확대", "축소"):
        return a["zoom"] not in (None, "")
    return a["type"] != "SHOW_LAYER" or bool(a["layers"])


def judge(gold, ext, res=None, turn=None):
    """정답 1행(dict) + extract() 결과 → {①, ②, ③, 정상수행, 오류원인, 되묻기, 응답오류, 불일치}"""
    out = OrderedDict([("①", None), ("②", None), ("③", None), ("정상수행", False), ("오류원인", ""),
                       ("되묻기", bool(ext.get("asked")) if ext else False), ("응답오류", ""), ("불일치", [])])
    err = response_error(res, turn) if res is not None else ""
    if err:
        out.update(응답오류=err, 오류원인="응답 오류", 불일치=[err])
        return out
    if not split_list(gold.get("정답의도")):                       # 정답 행이 없거나 정답의도가 빔 = 데이터 문제 — 시스템 오류(①)로 세지 않음
        out.update(오류원인="정답 없음", 불일치=["정답 없음 — 정답셋에 이 사례가 없거나 정답의도가 비어 있어 판정하지 않음"])
        return out
    gold_pnu = gold_pnu_of(gold)
    miss, notes = [], []
    # ① 의도
    want = {norm(x) for x in split_list(gold.get("정답의도"))}
    got = {norm(x) for x in ext["intents"]}
    names = {norm(x): x for x in ext["intents"]}
    gold_names = {norm(x): x for x in split_list(gold.get("정답의도"))}
    ok1 = bool(want) and want == got
    if not ok1:
        lack = [gold_names[x] for x in want - got]
        extra = [names[x] for x in got - want]
        miss.append("① 의도 " + " / ".join(filter(None, [f"누락 {', '.join(lack)}" if lack else "",
                                                       f"초과 {', '.join(extra)}" if extra else ""])))
    # ② 파라미터 — 레이어는 공간오케가 실제로 고른 레이어(lyr_id → LAYER_FIND 목록의 이름). 못 찾으면 검색어(lyr_query)로
    params = parse_kv(gold.get("정답파라미터"))
    picked = [c for c in ext.get("chosen_layers") or [] if c.get("name")]
    if picked:
        layer_pool = {norm(v) for c in picked for v in (layer_name(c["name"]), layer_name(c["name"]).split("/")[-1])}
        sys_layers = [layer_name(c["name"]) for c in picked]
    else:
        layer_pool = {norm(x) for x in ext["layers"]}
        sys_layers = list(ext["layers"])
        if ext.get("chosen_layers"):
            notes.append("고른 레이어의 이름을 응답에서 찾지 못해 검색어(lyr_query)로 대조")
    ok2, why2 = True, []
    sys_pnus = {p for _, p in ext["pnus"]}
    if gold_pnu or "PNU" in params or "위치" in params:
        if not gold_pnu:
            ok2 = False
            why2.append("정답 PNU 없음(참조PNU 칸 확인)")
        elif not sys_pnus:
            ok2 = False
            why2.append("시스템 PNU 없음(위치 확정 실패)")
        elif sys_pnus != {gold_pnu}:
            ok2 = False
            why2.append(f"위치 PNU 불일치 (정답 {gold_pnu} / 시스템 {', '.join(sorted(sys_pnus))})")
    for key, val in params.items():
        if key in ("위치", "PNU"):
            continue
        if key == "분석대상":
            for tok in [t.strip() for t in val.split(",") if t.strip()]:
                ops = next((o for k, o in TARGET_OPS.items() if norm(k) == norm(tok)), None)
                as_layer = norm(tok) in layer_pool                         # 같은 낱말이 레이어 이름일 수도 있음(지구단위계획구역 등)
                if ops is not None:
                    if not (ops & set(ext["ops"])) and not as_layer:
                        ok2 = False
                        why2.append(f"분석대상 '{tok}' 호출 없음 (시스템 {', '.join(ext['ops']) or '없음'})")
                elif not as_layer:
                    ok2 = False
                    why2.append(f"레이어 '{tok}' 불일치 (시스템 {', '.join(sys_layers) or '없음'})")
                bad_ids = layer_id_problem(tok, ext)
                if bad_ids:
                    ok2 = False
                    why2.append(f"레이어 '{tok}' 자료 불일치 — 고른 레이어 ID {', '.join(bad_ids)}는 이 레이어 자료가 아님 "
                                f"(허용 {', '.join(sorted(next(v for k, v in LAYER_TABLES.items() if norm(k) == norm(tok))))})")
        elif key == "반경":                                         # 반경 분석(버퍼·반경 안 레이어)에 넘어간 값의 집합 = 정답
            g = meters(val)
            got = sorted(set(ext.get("radius") or []))
            if g is None or not got or any(abs(g - d) > RADIUS_EPS for d in got):
                ok2 = False
                why2.append(f"반경 불일치 (정답 {val} / 시스템 {', '.join(f'{d:g}m' for d in got) or '없음'})")
        elif key == "확대레벨" and not JUDGE_ZOOM_LEVEL:            # 판정 제외 — 참고로만 남김
            notes.append(f"확대 레벨(참고·판정 제외) 정답 {val} / 시스템 {', '.join(ext['zooms']) or '없음'}")
        elif key == "확대레벨":                                    # 공간오케는 zoomLevel 을 문자열("17")로 보냄 → 숫자로 비교
            if _num(val) is None or not any(_num(z) == _num(val) for z in ext["zooms"]):
                ok2 = False
                why2.append(f"확대 레벨 불일치 (정답 {val} / 시스템 {', '.join(ext['zooms']) or '없음'})")
        else:
            vals = [str(v) for c in ext["calls"] for k, v in (c["args"] or {}).items() if norm(k) == norm(key)]
            if norm(val) not in {norm(v) for v in vals}:
                ok2 = False
                why2.append(f"{key} 불일치 (정답 {val} / 시스템 {', '.join(vals) or '없음'})")
    if "분석대상" in params:                                     # 집합 완전 일치 — 정답에 없는 분석 종류·레이어가 붙어도 ② 오류
        toks = [t.strip() for t in params["분석대상"].split(",") if t.strip()]
        known = {norm(k): o for k, o in TARGET_OPS.items()}
        gold_ops = set().union(*[known[norm(t)] for t in toks if norm(t) in known])
        gold_layers = {norm(t) for t in toks}                     # 레이어로 쓴 경우도 허용(위와 같음)
        extra = sorted({op for op in ext["ops"] if op in ALL_TARGET_OPS} - gold_ops) + \
            sorted({x for x in sys_layers if norm(x) not in gold_layers and norm(x.split("/")[-1]) not in gold_layers})
        if extra:
            ok2 = False
            why2.append(f"분석대상 초과 {', '.join(extra)} (정답 {params['분석대상']})")
    if "반경" not in params and ext.get("radius"):
        ok2 = False
        why2.append(f"정답에 없는 반경 {', '.join(f'{d:g}m' for d in ext['radius'])}")
    if not ok2:
        miss.append("② " + "; ".join(why2))
    # ③ 지도 제어 · 표출 도형
    scen = parse_kv(gold.get("정답시나리오"))
    ok3, why3 = True, []
    calls = {c["call_id"]: c for c in ext["calls"]}
    for key, val in scen.items():
        if key == "표출대상":
            moves = [a for a in ext["actions"] if a["type"] in MOVE_ACTIONS]
            hit = [a for a in moves if a["pnu"] == gold_pnu] or \
                  ([a for a in moves if not a["pnu"]] if any(c["callee"] == "map_navigation_b" and c["operation"] == "SELECT"
                                                             and c["pnu"] == gold_pnu and call_ok(c) for c in ext["calls"]) else [])
            if REQUIRE_TARGET_GEOMETRY:
                hit = [a for a in hit if a["has_geometry"]]
            if not hit:
                ok3 = False
                why3.append(f"대상 필지 이동·선택 명령 없음 (지도 명령 {', '.join(a['type'] for a in ext['actions']) or '없음'})")
        elif key == "지도제어":                                    # 값이 여럿이면 ',' — 모두 있어야 함 (대조 규칙 밖 값은 점검에서 막음)
            for v in [t.strip() for t in val.split(",") if t.strip()]:
                if not any(_control_done(a, v) for a in ext["actions"]):
                    ok3 = False
                    why3.append(f"지도 제어 '{v}' " + ("명령 없음" if v in MAP_CONTROL else "대조 규칙 없음"))
        elif key == "분석결과표출":
            for tok in [t.strip() for t in val.split(",") if t.strip()]:
                rule = next((r for r in SCENARIO_RULES if norm(r[0]) in norm(tok)), None)
                if rule is None:
                    ok3 = False
                    why3.append(f"시나리오 규칙 없음: {tok}")
                    continue
                _, ops, keys = rule
                good = [c for c in ext["calls"] if c["operation"] in ops and call_ok(c) and any(k in c["geom"] for k in keys)]
                if not good:
                    seen = [c for c in ext["calls"] if c["operation"] in ops]
                    state = ", ".join(f"{c['operation']} 상태 {c['status']}/{c['resolve_status']} 도형 {list(c['geom']) or '없음'}"
                                      for c in seen) or "호출 없음"
                    ok3 = False
                    why3.append(f"'{tok}' 도형 없음 ({state})")
        else:
            ok3 = False
            why3.append(f"시나리오 키 '{key}' 대조 규칙 없음")
    if ext.get("asked"):
        q = (ext["asks"][0].get("question") if ext["asks"] and isinstance(ext["asks"][0], dict) else "") or ""
        why3.append(f"되묻기: {q[:80]}")
    if not ok3:
        miss.append("③ " + "; ".join(why3))
    elif ext.get("asked"):
        miss.append("되묻기 = 오류 · " + why3[-1])
    out.update({"①": ok1, "②": ok2, "③": ok3})
    asked = bool(ext.get("asked"))
    reasons = {str(a.get("reason") or "") for a in ext.get("asks") or [] if isinstance(a, dict)} - {""}
    loc = [c for c in ext["calls"] if c["callee"] == "map_navigation_b" and c["operation"] in LOCATE_OPS]
    ok_pnus = ({c["pnu"] for c in ext["calls"] if c["pnu"] and c["operation"] not in SEARCH_OPS and call_ok(c)}
               | {a["pnu"] for a in ext["actions"] if a["pnu"]})   # 실패한 SELECT 에 실려 간 PNU 는 위치 확정 근거가 아님
    unlocated = bool(loc) and not ok_pnus and not ext.get("analysis_calls")   # 위치 확정 호출(검색·SELECT)은 했으나 필지를 못 정해 분석이 시작되지 않음
    failed = [c for c in loc if str(c.get("status") or "").lower() in ("error", "unknown")
              or str(c.get("resolve_status") or "").lower() in ("failed", "error")]   # 시스템 사유 실패 — no_data(못 찾음)는 빼고
    detail = []                                                    # 세부 원인 — 공식 원인(①→②→③)과 별도로 기록
    if asked:
        detail.append("되묻기" + (f"(사유 {', '.join(sorted(reasons))})" if reasons else "(사유 없음)"))
    if unlocated:
        detail.append("위치 미확정 — " + ("위치 확정 호출 실패: " + ", ".join(f"{c['operation']} {c['status']}/{c['resolve_status'] or '-'}" for c in failed)
                                         if failed else "대상 필지를 정하지 못함") + " · 분석 호출이 시작되지 않음")
    first = ("의도 해석 오류" if not ok1 else "핵심 파라미터 추출 오류" if not ok2 else "GIS 지도 제어 실행 오류" if not ok3 else "")
    if ok1 and ok2 and ok3 and not asked:
        out["정상수행"] = True
    elif asked and (ASK_CAUSE == "param" or (ASK_CAUSE == "reason" and reasons & ASK_PARAM_REASONS)):
        out["오류원인"] = "핵심 파라미터 추출 오류"
    elif unlocated and UNLOCATED_RULE:
        out["오류원인"] = "GIS 지도 제어 실행 오류" if failed else "핵심 파라미터 추출 오류"
    else:                                                          # 규격: 여러 기준이 어긋나면 ① → ② → ③ 중 처음 어긋난 기준
        out["오류원인"] = first or "핵심 파라미터 추출 오류"           # ①②③은 맞았는데 되물은 경우 — 값을 확정하지 못한 것으로 봄
    if detail:
        miss.extend(d for d in detail if not d.startswith("되묻기"))
    out["세부원인"] = "; ".join(detail)
    out["불일치"] = miss
    out["참고"] = notes
    return out


# ---------------------------------------------------------------- 로그 행
def parse_log_row(case, res, ext):
    """command_parse_log_ai6.jsonl 한 줄 — 명령 해석 결과"""
    return OrderedDict([
        ("사례ID", case.get("사례ID")), ("명령유형", canon_type(case.get("명령유형"))), ("명령문", case.get("명령문")),
        ("thread_id", res.get("thread_id")), ("요청시각", res.get("요청시각")), ("요청", res.get("request")),
        ("소요초", res.get("elapsed")), ("첫 응답초", res.get("t_first_event")),
        ("응답종료", res.get("ended_by")), ("응답오류", res.get("error") or ""),
        ("턴상태", ext.get("status")), ("종료사유", ext.get("stop_reason")),
        ("해석의도", [i for i in ext["intents"]]), ("의도근거", ext["intent_evidence"]),
        ("대응표밖호출", ext["unmapped_calls"]), ("의도코드", ext["intent_codes"]),
        ("핵심파라미터", ext["spec_output"]["핵심파라미터"]),
        ("실행대상기능", [OrderedDict([("callee", c["callee"]), ("tool", c["tool"]), ("operation", c["operation"]),
                                   ("args", c["args"]), ("status", c["status"]), ("reason_code", c["reason_code"]),
                                   ("elapsed_ms", c["elapsed_ms"])]) for c in ext["calls"]]),
        ("되묻기", ext["asked"]), ("되묻기내용", ext["asks"]),
        ("고른레이어", ext.get("chosen_layers") or []), ("검색후보PNU", ext.get("search_pnus") or []),
        ("규격출력", ext["spec_output"]),
    ])


def gis_log_row(case, gold, res, ext, verdict):
    """gis_execution_log_ai6.jsonl 한 줄 — 지도 제어 수행 결과 + 사례별 정답 대조 결과"""
    return OrderedDict([
        ("사례ID", case.get("사례ID")), ("명령유형", canon_type(case.get("명령유형"))), ("명령문", case.get("명령문")),
        ("thread_id", res.get("thread_id")), ("소요초", res.get("elapsed")),
        ("지도제어명령", ext["actions"]),
        ("표출도형", [OrderedDict([("callee", c["callee"]), ("operation", c["operation"]), ("도형", c["geom"]),
                                ("crs", c["geom_crs"]), ("resolve_status", c["resolve_status"]), ("pnu", c["pnu"])])
                   for c in ext["calls"] if c["geom"]]),
        ("호출결과", [OrderedDict([("callee", c["callee"]), ("operation", c["operation"]), ("status", c["status"]),
                                ("reason_code", c["reason_code"]), ("resolve_status", c["resolve_status"]),
                                ("errors", c["errors"] or c["error"] or [])]) for c in ext["calls"]]),
        ("최종응답", ext["answer"][:2000]),
        ("정답", OrderedDict([("정답의도", gold.get("정답의도")), ("정답파라미터", gold.get("정답파라미터")),
                            ("정답시나리오", gold.get("정답시나리오")), ("참조PNU", gold.get("참조PNU"))])),
        ("①의도", verdict["①"]), ("②파라미터", verdict["②"]), ("③지도제어", verdict["③"]),
        ("판정", "정상 수행" if verdict["정상수행"] else "오류"), ("오류원인", verdict["오류원인"]),
        ("되묻기", verdict["되묻기"]), ("응답오류", verdict["응답오류"]), ("불일치내용", verdict["불일치"]),
        ("세부원인", verdict.get("세부원인") or ""), ("참고", verdict.get("참고") or []),
    ])


# ---------------------------------------------------------------- 집계
def score(gis_rows, testset_rows, dec=DEC):
    """판정 행(gis log) + 시험셋 → 총괄 · 유형별 · 오류 원인별 · 사례별 표. 시험셋에 있는데 결과가 없으면 '미실행'(응답 오류)"""
    by_id = {str(r.get("사례ID")): r for r in gis_rows}
    cases = []
    for t in testset_rows:
        cid = str(t.get("사례ID") or "").strip()
        if not cid:                                                # 사례ID 가 빈 줄(엑셀 CSV 끝의 ',,' 등)은 사례가 아님 — 러너도 보내지 않음
            continue
        r = by_id.get(cid)
        if r is None:
            cases.append(OrderedDict([("사례ID", cid), ("명령유형", canon_type(t.get("명령유형"))), ("명령문", t.get("명령문")),
                                      ("①의도", None), ("②파라미터", None), ("③지도제어", None), ("판정", "오류"),
                                      ("오류원인", "응답 오류"), ("되묻기", False), ("세부원인", "미실행"), ("불일치내용", "미실행"),
                                      ("소요초", None)]))
            continue
        cases.append(OrderedDict([("사례ID", cid), ("명령유형", canon_type(t.get("명령유형") or r.get("명령유형"))),
                                  ("명령문", t.get("명령문")), ("①의도", r.get("①의도")), ("②파라미터", r.get("②파라미터")),
                                  ("③지도제어", r.get("③지도제어")), ("판정", r.get("판정")), ("오류원인", r.get("오류원인")),
                                  ("되묻기", r.get("되묻기")), ("세부원인", r.get("세부원인") or ""),
                                  ("불일치내용", " | ".join(r.get("불일치내용") or [])), ("소요초", r.get("소요초"))]))
    n = len(cases)
    ok = sum(1 for c in cases if c["판정"] == "정상 수행")
    value = floor_pct(ok, n, dec)
    total = [OrderedDict([("전체 테스트 건수", n), ("정상 수행 건수", ok), ("오류 건수", n - ok),
                          ("자연어 기반 지도 제어 정확도(%)", value), ("목표", f"{TARGET:g} % 이상"),
                          ("달성 여부", None if value is None else ("달성" if value >= TARGET else "미달성"))])]
    by_type = []
    for t in TYPES + sorted({c["명령유형"] for c in cases} - set(TYPES)):
        rows = [c for c in cases if c["명령유형"] == t]
        if not rows and t not in TYPES:
            continue
        good = sum(1 for c in rows if c["판정"] == "정상 수행")
        by_type.append(OrderedDict([("명령 유형", t), ("건수", len(rows)), ("정상 수행", good), ("오류", len(rows) - good),
                                    ("정확도(%)", floor_pct(good, len(rows), dec))]))
    cnt = Counter(c["오류원인"] for c in cases if c["판정"] != "정상 수행")
    by_cause = [OrderedDict([("오류 원인", k), ("건수", cnt.get(k, 0))]) for k in CAUSES]
    asked = sum(1 for c in cases if c["되묻기"])
    return OrderedDict([("value", value), ("passed", None if value is None else value >= TARGET), ("n", n), ("ok", ok),
                        ("되묻기", asked), ("응답오류", cnt.get("응답 오류", 0)),
                        ("총괄", total), ("유형별", by_type), ("오류원인별", by_cause), ("사례별", cases)])


# ---------------------------------------------------------------- 시험 데이터 점검
def validate(testset_rows, answer_rows, rules=None):
    """실행 전 데이터 점검 → [문제 문자열]. 비어 있으면 통과"""
    probs = []
    rules = rules if rules is not None else load_rules()
    vocab = {norm(r["intent"]) for r in rules if r["intent"] != "(무시)"}
    ids = [str(r.get("사례ID", "")).strip() for r in testset_rows]
    if not ids:
        return ["시험셋이 비어 있습니다"]
    for k, v in Counter(ids).items():
        if v > 1:
            probs.append(f"사례ID 중복: {k} ({v}건)")
    for r in testset_rows:
        cmd = str(r.get("명령문", ""))
        if not cmd.strip():
            probs.append(f"{r.get('사례ID')}: 명령문 없음")
            continue
        if cmd != cmd.strip() or "\n" in cmd or "\r" in cmd or "  " in cmd or "\t" in cmd:   # 그대로 보내므로 미리 고쳐 둠
            probs.append(f"{r.get('사례ID')}: 명령문에 앞뒤 공백·줄바꿈·연속 공백·탭이 있음(문장을 그대로 보내므로 데이터에서 고칠 것)")
        if re.search(r"(?<!\d)\d{19}(?!\d)", cmd):
            probs.append(f"{r.get('사례ID')}: 명령문에 19자리 숫자(PNU)가 있음 — 시스템에는 자연어 명령만 입력")
    cnt = Counter(canon_type(r.get("명령유형")) for r in testset_rows)
    for t, want in TYPE_COUNTS.items():
        if cnt.get(t, 0) != want:
            probs.append(f"명령유형 '{t}' {cnt.get(t, 0)}건 (규격 {want}건)")
    for t in set(cnt) - set(TYPE_COUNTS):
        probs.append(f"규격에 없는 명령유형 '{t}' {cnt[t]}건")
    ans = {str(r.get("사례ID", "")).strip(): r for r in answer_rows}
    for cid in ids:
        a = ans.get(cid)
        if a is None:
            probs.append(f"{cid}: 정답셋에 없음")
            continue
        for col in ("정답의도", "정답파라미터", "정답시나리오"):              # 빈 칸이면 ①은 시스템 오류로, ②③은 그냥 통과로 셈
            if not str(a.get(col) or "").strip():
                probs.append(f"{cid}: {col} 없음")
        p_ref, p_kv = pnu_of(a.get("참조PNU")), pnu_of(parse_kv(a.get("정답파라미터")).get("PNU"))
        if not p_ref and not p_kv:
            probs.append(f"{cid}: 참조PNU(19자리) 없음" + (" — 엑셀이 숫자로 바꿨을 수 있음(셀 서식을 텍스트로)"
                                                       if "E+" in str(a.get("참조PNU", "")).upper() else ""))
        elif p_ref and p_kv and p_ref != p_kv:
            probs.append(f"{cid}: 참조PNU {p_ref} ≠ 정답파라미터 PNU {p_kv} — 엑셀이 19자리 숫자를 15자리로 잘랐을 수 있음 "
                         "(판정은 정답파라미터 쪽을 씀)")
        for i in split_list(a.get("정답의도")):
            if norm(i) not in vocab:
                probs.append(f"{cid}: 의도 대응표에 없는 정답 의도 '{i}'")
        for k in parse_kv(a.get("정답파라미터")):
            if k not in ("위치", "PNU", "분석대상", "반경", "확대레벨"):
                probs.append(f"{cid}: 대조 규칙이 없는 파라미터 키 '{k}'")
        scen = parse_kv(a.get("정답시나리오"))
        for k in scen:
            if k not in ("표출대상", "지도제어", "분석결과표출"):
                probs.append(f"{cid}: 대조 규칙이 없는 시나리오 키 '{k}'")
        for tok in [t.strip() for t in str(scen.get("분석결과표출") or "").split(",") if t.strip()]:
            if not any(norm(rk) in norm(tok) for rk, _, _ in SCENARIO_RULES):
                probs.append(f"{cid}: 대조 규칙이 없는 표출 도형 '{tok}' (대조 기준의 낱말을 쓸 것)")
        for v in [t.strip() for t in str(scen.get("지도제어") or "").split(",") if t.strip()]:
            if v not in MAP_CONTROL:
                probs.append(f"{cid}: 대조 규칙이 없는 지도제어 값 '{v}' ({' · '.join(MAP_CONTROL)} 중에서 쓸 것)")
    for cid in set(ans) - set(ids):
        probs.append(f"{cid}: 시험셋에 없는 정답")
    if answer_rows and not any(str(r.get("검증자") or "").strip() for r in answer_rows):   # 규격: GIS 전문가가 정답 라벨을 검증
        probs.append(NOTE + "정답셋에 검증자(GIS 전문가) 칸이 비어 있음 — 공식 시험 전 검증자·검증일을 적을 것")
    return probs


NOTE = "(참고) "                                                    # 실행을 막지 않는 알림 — 나머지 점검 결과는 공식 실행을 막음


def blocking(probs):
    """공식 실행을 막는 점검 결과만"""
    return [p for p in probs if not str(p).startswith(NOTE)]
