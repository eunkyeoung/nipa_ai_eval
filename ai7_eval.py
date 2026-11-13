"""
[AI 7] 자연어 기반 3D 시뮬레이션 명령 정확도 — 응답 해석 · ①② 판정 · 집계 · 로그
------------------------------------------------------------------------------
3D 명령도 AI6 와 같은 공간오케(115:17006 /api/chat)가 처리합니다(2026-11-13 실측 — map_3d 에이전트 · map-3d-mcp).
ai6_client.gather() 로 묶은 응답(turn)을 받아 규격서 6.2.7 기준으로 판정합니다.

  ① 의도 추출   : 응답의 3D 명령(actions 의 type — slope·build·sunvolume·buildingVisible …)과 지도 명령을
                  의도 대응표로 의도 이름으로 바꿔 정답 의도와 집합 비교 (빠져도·남아도 오류)
                  · 공간오케 workflow 의도 코드는 기본 제어에서 UNKNOWN 으로 자주 나와(11-13 실측) 로그에만 남김
                  · '위치 이동'·'필지 표시'는 위치 보조 의도 — 주소가 든 명령이면 시스템이 위치 검색·이동(·필지 강조)을 붙이고,
                    규격서 정답 예시에도 없으므로 3D 의도가 있는 명령에서는 세지 않음. 이동·표시만 있는 기본 제어에서는 판정
                    (정답이 '필지 표시'면 이동은 세지 않고, 정답이 '위치 이동'뿐이면 그대로) (LOCATE_INTENTS · AUX_INTENTS)
  ② 파라미터    : 정답파라미터의 키마다 시스템 값(3D 호출 인자 · 3D 명령 parameters)과 대조
                  (PNU 집합 · 층수 · 용도 · 건축면적 · 건폐율 · 기준일(월-일) · 시간대(시) · 계획고 · 지형유형 · 기상 · 줌레벨 …)
  ③ 3D 실행     : 판정하지 않음(JUDGE_EXEC=False) — 이 시험은 AIPS 사전진단 성능테스트로, 3D 분석에 쓸 명령·파라미터가
                  응답에 제대로 들어오는지(①②)만 봄. 3D 화면 실행 상태·캡처는 시험 범위 밖(사용자 결정 11-13, 변경내역 106절)
  - 되묻기 = 오류 (규격: 명령문 1회 입력). 무응답·대기 상한 초과·시스템 오류 = '응답 오류'
  - 오류 원인 = ① → ② → ③ 중 처음 어긋난 것 (응답 오류는 그 앞)
  - 정확도(%) = 정상 수행 / 전체 × 100, 소수점 둘째자리 이하 버림 (DEC=1 — 2/3 → 66.6)

의도 대응표(intent_map_ai7.csv)가 없으면 DEFAULT_RULES 를 씁니다. 표준 라이브러리만 씁니다(pandas 없이 시험 가능).
"""
import csv
import hashlib
import io
import json
import re
from collections import Counter, OrderedDict

import ai6_eval as E6
from ai6_eval import (NOTE, blocking, call_ok, call_summary, canon_dot, floor_pct as _floor_pct, norm, parse_kv,  # noqa: F401
                      pnu_of, read_rows, response_error, sheet_names, split_list)

ITEM = "AI7"
TYPES = ["3D 지도 기본 제어", "필지 경사도 분석", "필지 토공량 산출", "지형 생성", "가상건물 생성",
         "일조 시뮬레이션 실행", "조망 시뮬레이션 실행"]
TYPE_COUNTS = {"3D 지도 기본 제어": 15, "필지 경사도 분석": 5, "필지 토공량 산출": 5, "지형 생성": 5, "가상건물 생성": 5,
               "일조 시뮬레이션 실행": 7, "조망 시뮬레이션 실행": 8}
CAUSES = ["의도 해석 오류", "핵심 파라미터 추출 오류", "3D 시뮬레이션 명령 실행 오류", "응답 오류"]
TARGET = 90.0
DEC = 1                                          # 규격서 6.2.7: 소수점 둘째자리 이하 버림
PARSE_LOG = "command_parse_log_ai7.jsonl"        # 명령 해석 로그 (의도·핵심 파라미터·실행 대상 기능)
SIM_LOG = "sim_execution_log_ai7.jsonl"          # 3D 실행 결과 로그 (3D 명령·호출 결과·정답 대조·판정)
JUDGE_EXEC = False                               # ③ 3D 실행 판정 안 함 — 시험 범위 밖(판정 '-', ①② 로 정상 수행)
EXEC_SCOPE = "③ 3D 실행은 시험 범위 밖 — ① 의도 · ② 파라미터로 판정(3D 분석 파라미터 확인)"
AUX_INTENTS = {"위치 이동"}                       # 보조 의도 — 정답 의도가 이것뿐일 때만 판정
# 위치 보조 의도 — 3D 의도가 있는 명령이면 '위치 이동'·'필지 표시' 모두 빼고 비교(시스템이 분석 대상 필지를 강조하는 일이 잦음,
# 11-13 예비 실행 0024·0040·0050). 이동·표시만 있는 기본 제어 명령에서는 그대로 판정
LOCATE_INTENTS = {"위치 이동", "필지 표시"}
ASK_PARAM_REASONS = {"ambiguous_candidates", "missing_required_value"}
SYSTEM_FAIL_RULE = True                          # 위치 확정 호출이 시스템 사유로 실패하고 3D 호출이 없으면 '응답 오류'(AI6 와 같은 규칙)
NUM_EPS = 1e-6

# ---------------------------------------------------------------- 3D 명령 · 호출 이름
# 3D 명령 key = action type 을 대문자 밑줄로(ai6_eval.action_type) + 켜기/끄기 구분
#   buildingVisible{visible:false} → BUILDING_VISIBLE:OFF · weather{type:off} → WEATHER:OFF · weather{type:snow} → WEATHER:ON
ENGINE_3D = {"SLOPE", "TERRAIN", "GROUND", "BUILD", "SUNVOLUME", "LANDSCAPE", "BUILDING_VISIBLE", "WEATHER", "RESET_CAMERA"}
LOCATE_OPS = {"INTEGRATED_LOCATION_SEARCH", "LEGAL_BOUNDARY_SEARCH", "ROAD_SEARCH", "SELECT", "GEOCODE", "GEOCODE_NORMALIZE",
              "PNU_RESOLVE", "HIGHLIGHT", "NORMAL"}
SEARCH_OPS = {"INTEGRATED_LOCATION_SEARCH", "LEGAL_BOUNDARY_SEARCH", "ROAD_SEARCH", "GEOCODE", "GEOCODE_NORMALIZE"}

# 의도 대응표 — 근거: 동작 = 3D·지도 명령 key · 호출 = 'callee:KEY'(map_3d 는 도구 이름 끝, 나머지는 operation) · '(무시)' = 보조
DEFAULT_RULES = [
    ("위치 이동", "동작", "MOVE_TO|SELECT|CENTER|FIT_BOUNDS|MOVE_AND_ZOOM", "주소가 있으면 공간오케가 위치 확정을 붙임 — 보조 의도(AUX_INTENTS)"),
    ("필지 표시", "동작", "HIGHLIGHT", "대상 필지 강조"),
    ("지도 확대/축소", "동작", "SET_ZOOM|ZOOM_IN|ZOOM_OUT|MOVE_AND_ZOOM", "11-13 실측: 3D 에이전트가 확대·줌 레벨을 거절함"),
    ("LOD 건물 끄기", "동작", "BUILDING_VISIBLE:OFF", "buildingVisible{visible:false}"),
    ("LOD 건물 켜기", "동작", "BUILDING_VISIBLE:ON", "buildingVisible{visible:true}"),
    ("기상 효과 켜기", "동작", "WEATHER:ON", "weather{type: auto·rain·snow …}"),
    ("기상 효과 끄기", "동작", "WEATHER:OFF", "weather{type:off}"),
    ("카메라 방위 초기화", "동작", "RESET_CAMERA", "resetCamera — 정북"),
    ("높이 계산", "동작", "HEIGHT|MEASURE_HEIGHT", "11-13 실측: WKT 를 되묻고 명령이 나오지 않음 — 명령 이름 미확인"),
    ("경사도 분석", "동작", "SLOPE", "tool.map3d.slope"),
    ("경사도 분석", "호출", "map_3d:SLOPE", ""),
    ("토공량 산출", "동작", "TERRAIN", "tool.map3d.terrain (targetHeight = 계획고)"),
    ("토공량 산출", "호출", "map_3d:TERRAIN", ""),
    ("지형 생성", "동작", "GROUND", "tool.map3d.ground (stairType)"),
    ("지형 생성", "호출", "map_3d:GROUND", ""),
    ("가상건물 생성", "동작", "BUILD", "tool.map3d.build"),
    ("가상건물 생성", "호출", "map_3d:BUILD", ""),
    ("일조 시뮬레이션 실행", "동작", "SUNVOLUME", "tool.map3d.sunvolume (date·fromHour·toHour)"),
    ("일조 시뮬레이션 실행", "호출", "map_3d:SUNVOLUME", ""),
    ("조망 시뮬레이션 실행", "동작", "LANDSCAPE", "tool.map3d.landscape"),
    ("조망 시뮬레이션 실행", "호출", "map_3d:LANDSCAPE", ""),
    ("(무시)", "호출", "land_regulation:SPLIT_FOR_BUILDING|spatial_regulation:AREA",
     "보조 호출 — 건물 자리 추천(가상건물 앞 단계) · 필지 면적 계산(지형 생성 앞 단계)"),
    ("(무시)", "동작", "UI.FOCUS|ADD_MARKER|GEOCODE", "화면 전환·마커·좌표 변환"),
]

FUNCTION_LABEL = {                               # 규격 출력 '실행대상기능' 표기 (규격서 예시: 주소검색 Tool · 가상건물 생성 Tool …)
    "INTEGRATED_LOCATION_SEARCH": "주소검색 Tool", "GEOCODE": "주소검색 Tool", "SELECT": "필지 선택 Tool",
    "HIGHLIGHT": "필지 강조 Tool", "SPLIT_FOR_BUILDING": "건물 자리 추천 Tool", "AREA": "면적 계산 Tool",
    "SLOPE": "경사도 분석 Tool", "TERRAIN": "토공량 산출 Tool", "GROUND": "지형 생성 Tool", "BUILD": "가상건물 생성 Tool",
    "SUNVOLUME": "일조 시뮬레이션 Tool", "LANDSCAPE": "조망 시뮬레이션 Tool",
    "BUILDING_VISIBLE": "LOD 건물 표시 Tool", "WEATHER": "기상 효과 Tool", "RESET_CAMERA": "카메라 초기화 Tool",
}
BUILDING_TYPE = {"detached": "단독주택"}          # build.buildingType → 용도 (실측한 값만 — 나머지는 원래 값으로 대조)
STAIR_TYPE = {"stairfloor": "계단형"}             # ground.stairType → 지형유형
WEATHER_TYPE = {"auto": "실황", "snow": "눈", "rain": "비", "off": "끔"}

# ② 정답파라미터 키 → (시스템 값 이름들, 비교 방식). 위치·PNU 는 따로(PNU 집합)
PARAM_RULES = OrderedDict([
    ("층수", (("floorCount",), "num")),
    ("높이", (("height", "buildingHeight", "floorHeight"), "num")),
    ("용도", (("buildingType",), "use")),
    ("건축면적", (("floorArea", "building_area"), "num")),
    ("건폐율", (("bcr", "building_coverage"), "num")),
    ("기준일", (("date",), "md")),
    ("시간대", (("fromHour", "toHour"), "hours")),
    ("계획고", (("targetHeight",), "num")),
    ("지형유형", (("stairType",), "stair")),
    ("기상", (("type",), "weather")),
    ("줌레벨", (("zoomLevel", "zoom", "level"), "num")),
    ("조망점높이", (("viewHeight", "eyeHeight", "observerHeight"), "num")),
    ("조망방향", (("direction", "heading", "azimuth"), "text")),
])
PARAM_KEYS = ("위치", "PNU") + tuple(PARAM_RULES)


def floor_pct(n, d, dec=DEC):
    return _floor_pct(n, d, dec)


def canon_type(s):
    s = re.sub(r"\s+", " ", canon_dot(s))
    for t in TYPES:
        if norm(t) == norm(s):
            return t
    return s


# ---------------------------------------------------------------- 의도 대응표
RULE_KINDS = ("호출", "동작", "인텐트")


def load_rules(path=None):
    """의도 대응표 → [{intent, kind, values(set), note}] (AI6 와 같은 양식: 정답의도 · 근거 · 값 · 비고)"""
    raw = read_rows(path) if path else [dict(정답의도=a, 근거=b, 값=c, 비고=d) for a, b, c, d in DEFAULT_RULES]
    rules = []
    for r in raw:
        intent = canon_dot(r.get("정답의도") or r.get("정답 의도") or "")
        kind = (r.get("근거") or "").strip()
        vals = {_rule_val(kind, v) for v in str(r.get("값") or "").split("|") if v.strip()}
        if intent and kind and vals:
            rules.append(dict(intent=intent, kind=kind, values=vals, note=r.get("비고", "")))
    if path:
        if not rules:
            raise ValueError("의도 대응표에서 규칙을 하나도 읽지 못했어요 — '정답의도 · 근거 · 값 · 비고' 칸이 필요해요.")
        bad = sorted({r["kind"] for r in rules} - set(RULE_KINDS))
        if bad:
            raise ValueError(f"의도 대응표 '근거' 값은 {' · '.join(RULE_KINDS)} 중 하나여야 해요 (지금: {', '.join(bad)})")
    return rules


def _rule_val(kind, v):
    v = str(v).strip()
    if kind == "호출":
        callee, _, key = v.partition(":")
        return f"{callee.strip()}:{key.strip().upper()}"
    return v.upper()


def rules_csv(rules=None):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["정답의도", "근거", "값", "비고"])
    for a, b, c, d in (rules or DEFAULT_RULES):
        w.writerow([a, b, c, d])
    return buf.getvalue()


# ---------------------------------------------------------------- 응답 해석
def _plan_tool(raw_call):
    args = raw_call.get("args") if isinstance(raw_call.get("args"), dict) else {}
    meta = args.get("_metadata") if isinstance(args.get("_metadata"), dict) else {}
    step = next((s for s in meta.get("plan") or [] if isinstance(s, dict)), {})
    return str(step.get("tool") or raw_call.get("tool") or "")


def call_key(cs, tool):
    """호출 이름 — map_3d 는 도구 이름 끝(tool.map3d.slope → SLOPE, 일반 모드 ask → ASK), 나머지는 operation"""
    if cs.get("callee") == "map_3d":
        return "map_3d:" + (tool.rsplit(".", 1)[-1] if tool else "ASK").upper()
    return f"{cs.get('callee')}:{cs.get('operation')}"


def _params(item):
    p = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
    r = item.get("result") if isinstance(item.get("result"), dict) else {}
    return {**r, **p, **{k: v for k, v in item.items() if k not in ("type", "parameters", "result", "_call_id", "engine")}}


def action_key(item):
    """3D·지도 명령 → 의도 대응 key (켜기/끄기 구분 포함)"""
    t = E6.action_type(item)
    p = _params(item)
    if t == "BUILDING_VISIBLE":
        return "BUILDING_VISIBLE:" + ("ON" if p.get("visible") in (True, "true", "True", 1) else "OFF")
    if t == "WEATHER":
        return "WEATHER:" + ("OFF" if str(p.get("type") or "").lower() in ("off", "none", "clear") else "ON")
    return t


BIG_KEYS = ("sitePolygon", "footprint", "driveway", "roadContact", "geometry")


def _small(d):
    """로그용 — 도형 문자열(WKT 등)은 길이만 남김"""
    return OrderedDict((k, (f"<{k} {len(str(v))}자>" if k in BIG_KEYS and v else v)) for k, v in (d or {}).items())


def extract(turn, rules=None):
    """gather() 결과 → 판정 재료 + 규격 출력 JSON(의도·핵심파라미터·실행대상기능·3D 상태)"""
    rules = rules if rules is not None else load_rules()
    by_kind = {}
    for r in rules:
        by_kind.setdefault(r["kind"], []).append(r)
    raw_calls = [c for c in turn.get("calls") or [] if c.get("callee")]
    calls = []
    for raw in raw_calls:
        cs = call_summary(raw)
        tool = _plan_tool(raw)
        cs["tool"] = tool or cs.get("tool")
        cs["key"] = call_key(cs, tool)
        calls.append(cs)
    actions = []
    for a in turn.get("actions") or []:
        key = action_key(a)
        actions.append(OrderedDict([("key", key), ("type", E6.action_type(a)), ("raw_type", a.get("type")),
                                    ("engine", a.get("engine")), ("call_id", a.get("_call_id")),
                                    ("pnu", pnu_of(_params(a).get("pnu") or _params(a).get("featureRef"))),
                                    ("params", _small(_params(a)))]))

    intents, evidence, unmapped = [], [], []

    def add(intent, kind, value):
        if intent == "(무시)":
            return True
        evidence.append({"의도": intent, "근거": kind, "값": value})
        if intent not in intents:
            intents.append(intent)
        return True

    explained = set()                                             # 의도로 설명된 호출(call_id) — 대응표 밖 호출 판단용
    for a in actions:
        hit = [r for r in by_kind.get("동작", []) if a["key"] in r["values"] or a["type"] in r["values"]]
        for r in hit:
            add(r["intent"], "동작", a["key"])
        if hit:
            explained.add(a["call_id"])
        elif a["key"] not in ("UI.FOCUS",):
            unmapped.append(f"동작:{a['key']}")
    for cs in calls:
        if cs["callee"] == "map_navigation_b":                    # 위치 확정·이동 호출은 지도 명령(동작)으로 셈
            continue
        hit = [r for r in by_kind.get("호출", []) if cs["key"] in r["values"]]
        for r in hit:
            add(r["intent"], "호출" if call_ok(cs) else "호출(실패)", cs["key"])
        if not hit and cs["call_id"] not in explained:             # 명령을 내지 못한 호출(예: 3D 에이전트가 거절·되물음)
            unmapped.append(cs["key"])
    wf = turn.get("workflow") or {}
    codes = (wf.get("intents") or {}) if isinstance(wf.get("intents"), dict) else {}
    for r in by_kind.get("인텐트", []):
        for code in set(codes.get("classified") or []) | set(codes.get("called") or []):
            if str(code).upper() in r["values"]:
                add(r["intent"], "인텐트", str(code))
    for key in dict.fromkeys(unmapped):
        intents.append(f"?{key}")

    # ② 재료 — 3D 호출 인자 + 3D 명령 parameters (같은 이름이면 둘 다 모음)
    values = OrderedDict()
    for cs in calls:
        if cs["callee"] == "map_navigation_b":
            continue
        for k, v in (cs["args"] or {}).items():
            if v not in (None, "") and k not in BIG_KEYS:
                values.setdefault(k, []).append(v)
    for a in actions:
        if a["engine"] == "3d" or a["type"] in ENGINE_3D:
            for k, v in a["params"].items():
                if v not in (None, "") and k not in BIG_KEYS and not str(v).startswith("<"):
                    values.setdefault(k, []).append(v)
    pnus, search_pnus = [], []
    for c in calls:
        if c["pnu"] and c["operation"] in SEARCH_OPS:
            search_pnus.append(c["pnu"])
        elif c["pnu"]:
            pnus.append(("호출 " + c["key"], c["pnu"]))
    for a in actions:
        if a["pnu"]:
            pnus.append(("명령 " + a["key"], a["pnu"]))
    functions = []
    for c in calls:
        k = c["key"].split(":", 1)[1]
        if c["callee"] == "map_3d" and k == "ASK":
            got = [a for a in actions if a["call_id"] == c["call_id"] and a["key"] != "UI.FOCUS"]
            label = ", ".join(dict.fromkeys(FUNCTION_LABEL.get(a["type"], a["type"]) for a in got)) or "3D 에이전트(명령 없음)"
        else:
            label = FUNCTION_LABEL.get(k, c["key"])
        label += "" if call_ok(c) else " (실패)"
        if label not in functions:
            functions.append(label)
    asked = bool(turn.get("asks")) or str(turn.get("status") or "").lower() == "asking"
    cmds = [a for a in actions if a["engine"] == "3d" or a["type"] in ENGINE_3D]
    spec_output = OrderedDict([
        ("의도", [f"(대응표 밖) {i[1:]}" if i.startswith("?") else i for i in intents]),
        ("핵심파라미터", OrderedDict([("위치", next((str((c["args"] or {}).get("query")) for c in calls
                                                   if c["operation"] in SEARCH_OPS and (c["args"] or {}).get("query")), "")),
                                     ("PNU", sorted({p for _, p in pnus})),
                                     *[(k, v if len(v) > 1 else v[0]) for k, v in values.items()
                                       if k not in ("pnu", "operation", "query", "siteLabel")]])),
        ("실행대상기능", functions),
        ("3D 상태", OrderedDict([("3D명령", [OrderedDict([("명령", a["raw_type"]), *[(k, v) for k, v in a["params"].items()
                                                                                if v not in (None, "")]]) for a in cmds]),
                                ("실행상태", "미확인(3D 화면 상태 연동 전 — 명령 발행까지 확인)" if cmds else "명령 없음")])),
    ])
    return OrderedDict([
        ("calls", calls), ("actions", actions), ("intents", intents), ("intent_evidence", evidence),
        ("unmapped", list(dict.fromkeys(unmapped))), ("intent_codes", codes), ("values", values), ("pnus", pnus),
        ("search_pnus", search_pnus), ("functions", functions), ("commands_3d", [a["key"] for a in cmds]),
        ("asked", asked), ("asks", turn.get("asks") or []), ("status", turn.get("status")),
        ("stop_reason", turn.get("stop_reason")), ("answer", turn.get("answer") or ""), ("spec_output", spec_output),
    ])


# ---------------------------------------------------------------- ② 값 비교
def _num(v):
    try:
        return float(re.sub(r"[^\d.\-]", "", str(v)) or "x")
    except ValueError:
        return None


def _md(v):
    """'12-22' · '12월 22일' · '2026-12-22' · '12/22' → '12-22'"""
    nums = re.findall(r"\d+", str(v))
    if len(nums) >= 3 and len(nums[0]) == 4:
        nums = nums[1:]
    return f"{int(nums[0]):02d}-{int(nums[1]):02d}" if len(nums) >= 2 else None


def _hours(v):
    """'09:00~15:00' · '9시부터 15시' · '9-15' → (9, 15)"""
    nums = [int(x) for x in re.findall(r"(\d{1,2})(?::\d{2})?(?:시)?", str(v))]
    return (nums[0], nums[1]) if len(nums) >= 2 else None


def compare(key, gold_val, values):
    """정답 값 vs 시스템 값 → (일치, 시스템 값 글)"""
    names, how = PARAM_RULES.get(key, ((key,), "text"))
    got = [v for n in names for v in values.get(n, [])]
    if how == "hours":
        fr, to = values.get("fromHour", []), values.get("toHour", [])
        sys_v = [(int(_num(a)), int(_num(b))) for a, b in zip(fr, to) if _num(a) is not None and _num(b) is not None]
        return (_hours(gold_val) in sys_v and len(set(sys_v)) == 1), ", ".join(f"{a}~{b}시" for a, b in sys_v) or "없음"
    shown = ", ".join(str(v) for v in got) or "없음"
    if not got:
        return False, "없음"
    if how == "num":
        g = _num(gold_val)
        return g is not None and all(_num(v) is not None and abs(_num(v) - g) < NUM_EPS for v in got), shown
    if how == "md":
        return all(_md(v) == _md(gold_val) for v in got), shown
    if how == "use":
        return all(norm(BUILDING_TYPE.get(str(v).lower(), v)) == norm(gold_val) for v in got), shown
    if how == "stair":
        g = "계단형" if "계단" in str(gold_val) else str(gold_val)
        return all(norm(STAIR_TYPE.get(str(v).lower(), v)) == norm(g) for v in got), shown
    if how == "weather":
        return all(norm(WEATHER_TYPE.get(str(v).lower(), v)) == norm(gold_val) for v in got), shown
    return all(norm(v) == norm(gold_val) for v in got), shown


def gold_pnu_of(gold):
    return pnu_of(parse_kv(gold.get("정답파라미터")).get("PNU")) or pnu_of(gold.get("참조PNU"))


# ---------------------------------------------------------------- 판정
def criteria_fingerprint():
    """판정 기준(의도 대응·값 비교 규칙·옵션)의 SHA-256 — 동결·기록용"""
    blob = json.dumps({"PARAM_RULES": {k: [list(a), b] for k, (a, b) in PARAM_RULES.items()},
                       "BUILDING_TYPE": BUILDING_TYPE, "STAIR_TYPE": STAIR_TYPE, "WEATHER_TYPE": WEATHER_TYPE,
                       "ENGINE_3D": sorted(ENGINE_3D), "LOCATE_OPS": sorted(LOCATE_OPS), "SEARCH_OPS": sorted(SEARCH_OPS),
                       "AUX_INTENTS": sorted(AUX_INTENTS), "LOCATE_INTENTS": sorted(LOCATE_INTENTS), "JUDGE_EXEC": JUDGE_EXEC, "ASK_PARAM_REASONS": sorted(ASK_PARAM_REASONS),
                       "SYSTEM_FAIL_RULE": SYSTEM_FAIL_RULE, "DEC": DEC, "TARGET": TARGET, "NUM_EPS": NUM_EPS,
                       "ACTION_ALIAS": dict(sorted(E6.ACTION_ALIAS.items()))}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def judge(gold, ext, res=None, turn=None):
    """정답 1행(dict) + extract() 결과 → {①, ②, ③, 정상수행, 오류원인, 되묻기, 응답오류, 불일치}"""
    out = OrderedDict([("①", None), ("②", None), ("③", None), ("정상수행", False), ("오류원인", ""),
                       ("되묻기", bool(ext.get("asked")) if ext else False), ("응답오류", ""), ("불일치", [])])
    err = response_error(res, turn) if res is not None else ""
    if err:
        out.update(응답오류=err, 오류원인="응답 오류", 불일치=[err])
        return out
    if not split_list(gold.get("정답의도")):
        out.update(오류원인="정답 없음", 불일치=["정답 없음 — 정답셋에 이 사례가 없거나 정답의도가 비어 있어 판정하지 않음"])
        return out
    miss, notes = [], []
    # ① 의도 — 보조 의도(위치 이동)는 정답이 그것뿐일 때만
    gold_list = split_list(gold.get("정답의도"))
    want = {norm(x) for x in gold_list}
    got = {norm(x) for x in ext["intents"]}
    aux, loc = {norm(x) for x in AUX_INTENTS}, {norm(x) for x in LOCATE_INTENTS}
    if want - loc:                                                 # 3D 의도가 있음 → 위치 보조 의도(이동·필지 표시)는 세지 않음
        want, got = want - loc, got - loc
    elif want - aux:                                               # 필지 표시 명령 → 이동은 세지 않음
        want, got = want - aux, got - aux
    ok1 = bool(want) and want == got
    if not ok1:
        names = {norm(x): x for x in ext["intents"]}
        gnames = {norm(x): x for x in gold_list}
        lack = [gnames[x] for x in dict.fromkeys(norm(y) for y in gold_list) if x in want and x not in got]
        extra = [names[x] for x in dict.fromkeys(norm(y) for y in ext["intents"]) if x in got and x not in want]
        miss.append("① 의도 " + " / ".join(filter(None, [f"누락 {', '.join(lack)}" if lack else "",
                                                       f"초과 {', '.join(extra)}" if extra else ""])))
    # ② 파라미터
    params = parse_kv(gold.get("정답파라미터"))
    ok2, why2 = True, []
    gold_pnu = gold_pnu_of(gold)
    sys_pnus = {p for _, p in ext["pnus"]}
    if gold_pnu or "PNU" in params:
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
        ok, shown = compare(key, val, ext["values"])
        if not ok:
            ok2 = False
            why2.append(f"{key} 불일치 (정답 {val} / 시스템 {shown})")
    if not ok2:
        miss.append("② " + "; ".join(why2))
    # ③ 3D 실행 — 시험 범위 밖(판정하지 않음)
    ok3 = None
    if not JUDGE_EXEC:
        notes.append(EXEC_SCOPE)
    asked = bool(ext.get("asked"))
    if asked:
        q = (ext["asks"][0].get("question") if ext["asks"] and isinstance(ext["asks"][0], dict) else "") or ""
        miss.append(f"되묻기 = 오류 · {q[:80]}")
    out.update({"①": ok1, "②": ok2, "③": ok3})
    reasons = {str(a.get("reason") or "") for a in ext.get("asks") or [] if isinstance(a, dict)} - {""}
    loc = [c for c in ext["calls"] if c["callee"] == "map_navigation_b" and c["operation"] in LOCATE_OPS]
    failed = [c for c in loc if str(c.get("status") or "").lower() in ("error", "unknown")
              or str(c.get("resolve_status") or "").lower() in ("failed", "error")]
    no_3d = not ext.get("commands_3d") and not any(c["callee"] == "map_3d" for c in ext["calls"])
    detail = []
    if asked:
        detail.append("되묻기" + (f"(사유 {', '.join(sorted(reasons))})" if reasons else "(사유 없음)"))
    first = "의도 해석 오류" if not ok1 else "핵심 파라미터 추출 오류" if not ok2 else "3D 시뮬레이션 명령 실행 오류" if ok3 is False else ""
    if ok1 and ok2 and ok3 is not False and not asked:
        out["정상수행"] = True
    elif failed and no_3d and SYSTEM_FAIL_RULE:
        out["오류원인"] = "응답 오류"
        out["응답오류"] = "시스템 오류로 위치를 확정하지 못함 — " + "; ".join(
            f"{c['operation']} {c.get('status') or '-'}/{c.get('resolve_status') or '-'}" for c in failed)
        miss.insert(0, out["응답오류"])
    else:
        out["오류원인"] = first or "핵심 파라미터 추출 오류"           # ①② 는 맞았는데 되물은 경우 — 값을 확정하지 못한 것으로 봄
    out["세부원인"] = "; ".join(detail)
    out["불일치"] = miss
    out["참고"] = notes
    return out


# ---------------------------------------------------------------- 로그 행
def parse_log_row(case, res, ext):
    """command_parse_log_ai7.jsonl 한 줄 — 명령 해석 결과"""
    return OrderedDict([
        ("사례ID", case.get("사례ID")), ("명령유형", canon_type(case.get("명령유형"))), ("명령문", case.get("명령문")),
        ("선행상태", case.get("선행상태") or ""),
        ("thread_id", res.get("thread_id")), ("요청시각", res.get("요청시각")), ("요청", res.get("request")),
        ("소요초", res.get("elapsed")), ("첫 응답초", res.get("t_first_event")),
        ("응답종료", res.get("ended_by")), ("응답오류", res.get("error") or ""),
        ("턴상태", ext.get("status")), ("종료사유", ext.get("stop_reason")),
        ("해석의도", list(ext["intents"])), ("의도근거", ext["intent_evidence"]),
        ("대응표밖", ext["unmapped"]), ("의도코드", ext["intent_codes"]),
        ("핵심파라미터", ext["spec_output"]["핵심파라미터"]),
        ("실행대상기능", [OrderedDict([("callee", c["callee"]), ("tool", c["tool"]), ("key", c["key"]),
                                   ("args", _small(c["args"])), ("status", c["status"]), ("reason_code", c["reason_code"]),
                                   ("elapsed_ms", c["elapsed_ms"])]) for c in ext["calls"]]),
        ("되묻기", ext["asked"]), ("되묻기내용", ext["asks"]), ("검색후보PNU", ext.get("search_pnus") or []),
        ("규격출력", ext["spec_output"]),
    ])


def sim_log_row(case, gold, res, ext, verdict):
    """sim_execution_log_ai7.jsonl 한 줄 — 3D 명령 · 호출 결과 · 정답 대조 · 판정"""
    return OrderedDict([
        ("사례ID", case.get("사례ID")), ("명령유형", canon_type(case.get("명령유형"))), ("명령문", case.get("명령문")),
        ("선행상태", case.get("선행상태") or ""), ("thread_id", res.get("thread_id")), ("소요초", res.get("elapsed")),
        ("3D명령", [a for a in ext["actions"] if a["engine"] == "3d" or a["type"] in ENGINE_3D]),
        ("지도명령", [a for a in ext["actions"] if not (a["engine"] == "3d" or a["type"] in ENGINE_3D)]),
        ("호출결과", [OrderedDict([("callee", c["callee"]), ("key", c["key"]), ("status", c["status"]),
                                ("reason_code", c["reason_code"]), ("resolve_status", c["resolve_status"]),
                                ("errors", c["errors"] or c["error"] or [])]) for c in ext["calls"]]),
        ("실행상태", ext["spec_output"]["3D 상태"]["실행상태"]),
        ("최종응답", ext["answer"][:2000]),
        ("정답", OrderedDict([("정답의도", gold.get("정답의도")), ("정답파라미터", gold.get("정답파라미터")),
                            ("정답시나리오", gold.get("정답시나리오")), ("참조PNU", gold.get("참조PNU"))])),
        ("①의도", verdict["①"]), ("②파라미터", verdict["②"]), ("③3D실행", verdict["③"]),
        ("판정", "정상 수행" if verdict["정상수행"] else "오류"), ("오류원인", verdict["오류원인"]),
        ("되묻기", verdict["되묻기"]), ("응답오류", verdict["응답오류"]), ("불일치내용", verdict["불일치"]),
        ("세부원인", verdict.get("세부원인") or ""), ("참고", verdict.get("참고") or []),
    ])


# ---------------------------------------------------------------- 화면·결과표용 칸
SPEC_CELLS = ("해석 의도", "핵심 파라미터", "실행 대상 기능", "3D 지도 상태")    # 규격서 6.2.7 '출력' 항목


def _join(v, sep=", "):
    if isinstance(v, (list, tuple)):
        return sep.join(str(x) for x in v if x not in (None, ""))
    return "" if v is None else str(v)


def spec_cells(spec):
    spec = spec if isinstance(spec, dict) else {}
    kp = spec.get("핵심파라미터") or {}
    params = [f"{k}={_join(v) if isinstance(v, (list, tuple)) else v}" for k, v in kp.items() if v not in (None, "", [])]
    st = spec.get("3D 상태") or {}
    cmds = [", ".join([str(c.get("명령"))] + [f"{k}={v}" for k, v in c.items() if k != "명령" and not str(v).startswith("<")])
            for c in st.get("3D명령") or []]
    return OrderedDict([
        ("해석 의도", _join(spec.get("의도"))),
        ("핵심 파라미터", "; ".join(params)),
        ("실행 대상 기능", _join(spec.get("실행대상기능"), " → ")),
        ("3D 지도 상태", " / ".join(cmds + ([st["실행상태"]] if st.get("실행상태") else []))),
    ])


# ---------------------------------------------------------------- 집계
def score(sim_rows, testset_rows, dec=DEC, parse_rows=None):
    """판정 행(sim log) + 시험셋 → 총괄 · 유형별 · 오류 원인별 · 사례별 표. 결과가 없는 사례는 '미실행'(응답 오류)"""
    by_id = {str(r.get("사례ID")): r for r in sim_rows}
    spec_by = {str(p.get("사례ID")): spec_cells(p.get("규격출력")) for p in (parse_rows or [])}
    blank = OrderedDict((k, "") for k in SPEC_CELLS)
    cases = []
    for t in testset_rows:
        cid = str(t.get("사례ID") or "").strip()
        if not cid:
            continue
        r = by_id.get(cid)
        if r is None:
            cases.append(OrderedDict([("사례ID", cid), ("명령유형", canon_type(t.get("명령유형"))), ("명령문", t.get("명령문")),
                                      *blank.items(), ("①의도", None), ("②파라미터", None), ("③3D실행", None),
                                      ("판정", "오류"), ("오류원인", "응답 오류"), ("되묻기", False), ("세부원인", "미실행"),
                                      ("불일치내용", "미실행"), ("소요초", None)]))
            continue
        cases.append(OrderedDict([("사례ID", cid), ("명령유형", canon_type(t.get("명령유형") or r.get("명령유형"))),
                                  ("명령문", t.get("명령문")), *spec_by.get(cid, blank).items(),
                                  ("①의도", r.get("①의도")), ("②파라미터", r.get("②파라미터")), ("③3D실행", r.get("③3D실행")),
                                  ("판정", r.get("판정")), ("오류원인", r.get("오류원인")), ("되묻기", r.get("되묻기")),
                                  ("세부원인", r.get("세부원인") or ""), ("불일치내용", " | ".join(r.get("불일치내용") or [])),
                                  ("소요초", r.get("소요초"))]))
    n = len(cases)
    ok = sum(1 for c in cases if c["판정"] == "정상 수행")
    value = floor_pct(ok, n, dec)
    total = [OrderedDict([("전체 테스트 건수", n), ("정상 수행 건수", ok), ("오류 건수", n - ok),
                          ("자연어 기반 3D 시뮬레이션 명령 정확도(%)", value), ("목표", f"{TARGET:g} % 이상"),
                          ("달성 여부", None if value is None else ("달성" if value >= TARGET else "미달성")),
                          ("비고", "" if JUDGE_EXEC else EXEC_SCOPE)])]
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
    if cnt.get("정답 없음"):
        by_cause.append(OrderedDict([("오류 원인", "정답 없음(판정 안 함)"), ("건수", cnt["정답 없음"])]))
    return OrderedDict([("value", value), ("passed", None if value is None else value >= TARGET), ("n", n), ("ok", ok),
                        ("되묻기", sum(1 for c in cases if c["되묻기"])), ("응답오류", cnt.get("응답 오류", 0)),
                        ("총괄", total), ("유형별", by_type), ("오류원인별", by_cause), ("사례별", cases)])


# ---------------------------------------------------------------- 시험 데이터 점검
def validate(testset_rows, answer_rows, rules=None):
    """실행 전 데이터 점검 → [문제 문자열]. NOTE 로 시작하는 것은 실행을 막지 않는 알림"""
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
        if cmd != cmd.strip() or "\n" in cmd or "\r" in cmd or "  " in cmd or "\t" in cmd:
            probs.append(f"{r.get('사례ID')}: 명령문에 앞뒤 공백·줄바꿈·연속 공백·탭이 있음(문장을 그대로 보내므로 데이터에서 고칠 것)")
        if re.search(r"(?<!\d)\d{19}(?!\d)", cmd):
            probs.append(f"{r.get('사례ID')}: 명령문에 19자리 숫자(PNU)가 있음 — 시스템에는 자연어 명령만 입력")
        if str(r.get("선행상태") or "").strip():
            probs.append(f"{NOTE}{r.get('사례ID')}: 선행상태 '{r.get('선행상태')}' — 기록만 함(3D 화면 실행은 시험 범위 밖)")
    cnt = Counter(canon_type(r.get("명령유형")) for r in testset_rows)
    for t, want in TYPE_COUNTS.items():
        if cnt.get(t, 0) != want:
            probs.append(f"명령유형 '{t}' {cnt.get(t, 0)}건 (규격 {want}건)")
    for t in sorted(set(cnt) - set(TYPE_COUNTS)):
        probs.append(f"규격에 없는 명령유형 '{t}' {cnt[t]}건")
    ans = {str(r.get("사례ID", "")).strip(): r for r in answer_rows}
    for cid in ids:
        a = ans.get(cid)
        if a is None:
            probs.append(f"{cid}: 정답셋에 없음")
            continue
        for col in ("정답의도", "정답파라미터", "정답시나리오"):
            if not str(a.get(col) or "").strip() and not (col == "정답파라미터" and _no_param_ok(a)):
                probs.append(f"{cid}: {col} 없음")
        p_ref, p_kv = pnu_of(a.get("참조PNU")), pnu_of(parse_kv(a.get("정답파라미터")).get("PNU"))
        if p_ref and p_kv and p_ref != p_kv:
            probs.append(f"{cid}: 참조PNU {p_ref} ≠ 정답파라미터 PNU {p_kv} — 엑셀이 19자리 숫자를 잘랐을 수 있음")
        for i in split_list(a.get("정답의도")):
            if norm(i) not in vocab:
                probs.append(f"{cid}: 의도 대응표에 없는 정답 의도 '{i}'")
        for k in parse_kv(a.get("정답파라미터")):
            if k not in PARAM_KEYS:
                probs.append(f"{cid}: 대조 규칙이 없는 파라미터 키 '{k}' ({' · '.join(PARAM_KEYS)} 중에서 쓸 것)")
    for cid in sorted(set(ans) - set(ids)):
        probs.append(f"{cid}: 시험셋에 없는 정답")
    if not JUDGE_EXEC:
        probs.append(f"{NOTE}{EXEC_SCOPE} — 정답시나리오 칸은 판정에 쓰지 않음(참고용)")
    return probs


def _no_param_ok(a):
    """위치·값이 없는 기본 제어(LOD·기상·카메라·확대)는 정답파라미터가 비어도 됨"""
    return all(norm(i) in {norm(x) for x in ("LOD 건물 끄기", "LOD 건물 켜기", "기상 효과 켜기", "기상 효과 끄기",
                                              "카메라 방위 초기화", "지도 확대/축소")}
               for i in split_list(a.get("정답의도"))) and bool(split_list(a.get("정답의도")))
