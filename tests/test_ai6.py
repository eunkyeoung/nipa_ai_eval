"""[AI 6] 호출기·판정기 시험 — 표준 라이브러리 + requests 만 씀 (pandas·streamlit 없이 실행)

  python -m unittest tests.test_ai6 -v

공간오케 응답 모양은 공간오케 plugin/coreplug/jsonapi.py(gather) · workflow/mapactions.py 기준으로 만든 가짜 이벤트입니다.
실제 응답으로 확인한 것이 아니므로, 예비 실측 원문(raw/*.json)을 받으면 tests/fixtures 에 넣어 같은 시험을 돌립니다.
"""
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai6_client as C   # noqa: E402
import ai6_eval as E     # noqa: E402

PNU = "4729012300100310001"
POLY = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
        "crs": {"type": "name", "properties": {"name": "EPSG:5186"}}}


def plan(tool, **args):
    return {"_metadata": {"mode": "workflow", "plan": [{"tool": tool, "args": args}]}}


def locate_events(pnu=PNU, select_ok=True):
    ev = [("turn.started", {"thread_id": "t1"}),
          ("call.started", {"call_id": "c1", "callee": "map_navigation_b", "tool": "locationSearch",
                            "args": plan("locationSearch", action="INTEGRATED_LOCATION_SEARCH", query="경산시 신천동 31-1", size="20")}),
          ("call.finished", {"call_id": "c1", "callee": "map_navigation_b", "tool": "locationSearch", "status": "ok",
                             "elapsed_ms": 800, "payload": {"status": "ok", "resolve": {"status": "ok", "result": {"results": [{"pnu": pnu}]}},
                                                            "actions": [{"type": "map.MOVE_TO", "result": {"lng": 1, "lat": 2, "crs": "EPSG:5186"}},
                                                                        {"type": "map.ADD_MARKER", "result": {"lng": 1, "lat": 2}}]}}),
          ("actions", {"call_id": "c1", "items": [{"type": "map.MOVE_TO", "result": {"lng": 1, "lat": 2, "crs": "EPSG:5186"}},
                                                  {"type": "map.ADD_MARKER", "result": {"lng": 1, "lat": 2}}]})]
    if select_ok:
        sel = {"type": "map.SELECT", "result": {"pnu": pnu, "featureRef": pnu, "crs": "EPSG:5186", "lng": 1, "lat": 2, "geometry": POLY}}
        ev += [("call.started", {"call_id": "c2", "callee": "map_navigation_b", "tool": "mapFeature",
                                 "args": plan("mapFeature", action="SELECT", featureRef=pnu)}),
               ("call.finished", {"call_id": "c2", "callee": "map_navigation_b", "tool": "mapFeature", "status": "ok", "elapsed_ms": 300,
                                  "payload": {"status": "ok", "resolve": {"status": "ok", "result": {"pnu": pnu}},
                                              "actions": [{"type": "ui.focus", "target": "map"}, sel]}}),
               ("actions", {"call_id": "c2", "items": [{"type": "ui.focus", "target": "map"}, sel]})]
    return ev


def analysis_events(callee, tool, op_key, op, pnu=PNU, geom_key="result_geojson", geom=POLY, status="ok",
                    resolve_status="ok", call_id="c3", **extra):
    args = {"pnu": pnu, op_key: op, **extra}
    pay = {"status": "ok", "resolve": {"status": resolve_status, "pnu": pnu, "geom": {geom_key: [geom]} if geom else {}}}
    return [("call.started", {"call_id": call_id, "callee": callee, "tool": tool, "args": plan(tool, **args)}),
            ("call.finished", {"call_id": call_id, "callee": callee, "tool": tool, "status": status, "elapsed_ms": 900,
                               "payload": pay})]


def finish(status="delivered", answer="결과를 지도에 표시했습니다."):
    return [("answer.delta", {"delta": answer}),
            ("workflow", {"thread_id": "t1", "skipped": False, "intents": {"classified": ["LAND_USE_PLAN"], "called": ["SEARCH_LOCATION"]}}),
            ("turn.finished", {"thread_id": "t1", "status": status, "answer": answer, "stop_reason": "model_completed",
                               "usage": {"timing": {"total_ms": 9000}}})]


GOLD_OVERLAP = {"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "정답의도": "지도 이동; 규제 저촉 분석; 분석 결과 표출",
                "정답파라미터": f"위치=경상북도 경산시 신천동 31-1; PNU={PNU}; 분석대상=용도지역·지구",
                "정답시나리오": "표출대상=대상 필지 도형; 분석결과표출=용도지역·지구 중첩 도형", "참조PNU": PNU}
OK_RES = {"ended_by": "turn.finished", "thread_id": "t1", "elapsed": 9.1}


def run(events, gold, res=OK_RES):
    turn = C.gather(events)
    ext = E.extract(turn)
    return E.judge(gold, ext, res, turn), ext


class SseParsing(unittest.TestCase):
    def test_crlf_split_across_chunks(self):
        chunks = [b"event: call.started\r", b"\ndata: {\"call_id\": \"c1\"}\r\n\r", b"\n: ping\r\n",
                  b"event: turn.finished\r\ndata: {\"status\":", b" \"delivered\"}\r\n\r\n"]
        got = list(C.iter_sse(C.iter_lines(chunks)))
        self.assertEqual(got, [("call.started", {"call_id": "c1"}), ("turn.finished", {"status": "delivered"})])

    def test_multiline_and_bad_json(self):
        lines = ["event: x", "data: {\"a\":", "data: 1}", "", "event: y", "data: not-json", ""]
        self.assertEqual(list(C.iter_sse(lines)), [("x", {"a": 1}), ("y", {"_raw": "not-json"})])

    def test_gather_actions_and_calls(self):
        turn = C.gather(locate_events() + finish())
        self.assertEqual([c["call_id"] for c in turn["calls"]], ["c1", "c2"])
        self.assertEqual([a["type"] for a in turn["actions"]], ["map.MOVE_TO", "map.ADD_MARKER", "ui.focus", "map.SELECT"])
        self.assertTrue(turn["finished"])
        self.assertEqual(turn["workflow"]["intents"]["called"], ["SEARCH_LOCATION"])


class Judge(unittest.TestCase):
    def test_overlap_pass(self):
        ev = locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
        v, ext = run(ev, GOLD_OVERLAP)
        self.assertTrue(v["정상수행"], v["불일치"])
        self.assertEqual(set(ext["intents"]), {"지도 이동", "규제 저촉 분석", "분석 결과 표출"})
        self.assertIn("용도지역·지구 중첩 조회", ext["spec_output"]["실행대상기능"]["Function"])
        self.assertEqual(ext["spec_output"]["실행대상기능"]["명령유형"], "공간규제 분석")

    def test_no_data_is_gis_error_not_intent_error(self):
        gold = dict(GOLD_OVERLAP, 정답의도="지도 이동; 개발가능지 추출; 분석 결과 표출",
                    정답파라미터=f"PNU={PNU}; 분석대상=개발가능지", 정답시나리오="표출대상=대상 필지 도형; 분석결과표출=개발가능 영역 도형")
        ev = locate_events() + analysis_events("land_regulation", "tool.land.regulation", "operation", "DEVELOPABLE_AREA",
                                               geom=None, resolve_status="no_data") + finish()
        v, _ = run(ev, gold)
        self.assertEqual((v["①"], v["②"], v["③"]), (True, True, False), v["불일치"])
        self.assertEqual(v["오류원인"], "GIS 지도 제어 실행 오류")

    def test_wrong_parcel_is_param_error(self):
        other = "4729012300100310002"
        ev = locate_events(pnu=other) + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY", pnu=other) + finish()
        v, _ = run(ev, GOLD_OVERLAP)
        self.assertFalse(v["②"])
        self.assertEqual(v["오류원인"], "핵심 파라미터 추출 오류")

    def test_extra_unmapped_call_breaks_intent(self):
        ev = (locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY")
              + analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "NEARBY_PARCELS", call_id="c4",
                                distance="300") + finish())
        v, ext = run(ev, GOLD_OVERLAP)
        self.assertFalse(v["①"])
        self.assertIn("?spatial_regulation:NEARBY_PARCELS", ext["intents"])
        self.assertEqual(v["오류원인"], "의도 해석 오류")

    def test_missing_display_intent_partial_match(self):
        gold = dict(GOLD_OVERLAP, 정답의도="지도 이동; 규제 저촉 분석")       # 규격 예시의 '부분 일치' — 시스템이 하나 더 냄
        ev = locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
        v, _ = run(ev, gold)
        self.assertFalse(v["①"])

    def test_ask_is_error_param_cause(self):
        ev = locate_events(select_ok=False) + [("ask", {"key": "user:1", "kind": "user", "question": "'신천동 31'에 해당하는 곳이 3곳입니다. 어느 곳인가요?",
                                                        "options": ["a", "b", "c"], "reason": "ambiguous_candidates"})] + finish(status="asking", answer="")
        v, ext = run(ev, GOLD_OVERLAP)
        self.assertTrue(ext["asked"])
        self.assertFalse(v["정상수행"])
        self.assertEqual(v["오류원인"], "의도 해석 오류")              # 규격: ①→②→③ 처음 불일치 — 분석이 시작되지 않아 ①부터 어긋남
        self.assertIn("되묻기(사유 ambiguous_candidates)", v["세부원인"])

    def test_deadline_is_response_error(self):
        ev = locate_events()
        v, _ = run(ev, GOLD_OVERLAP, res={"ended_by": "deadline", "error": "응답 대기 상한 300초 초과"})
        self.assertEqual(v["오류원인"], "응답 오류")
        self.assertIsNone(v["①"])

    def test_eof_without_turn_finished(self):
        v, _ = run(locate_events(), GOLD_OVERLAP, res={"ended_by": "eof"})
        self.assertEqual(v["오류원인"], "응답 오류")

    def test_radius_and_layer(self):
        gold = {"사례ID": "AI6-0013", "명령유형": "공간규제 분석", "정답의도": "지도 이동; 주변 규제 분석; 분석 결과 표출",
                "정답파라미터": f"PNU={PNU}; 분석대상=개발제한구역; 반경=500m",
                "정답시나리오": "표출대상=대상 필지 도형; 분석결과표출=반경 500m 내 개발제한구역 도형", "참조PNU": PNU}
        base = locate_events() + analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "LAYER_FIND",
                                                 call_id="c3", lyr_query="개발제한구역", geom=None)
        good = base + analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "LAYER_NEARBY", call_id="c4",
                                      lyr_id="lsmd_cont_ud801", distance="500") + finish()
        v, ext = run(good, gold)
        self.assertTrue(v["정상수행"], v["불일치"])
        bad = base + analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "LAYER_NEARBY", call_id="c4",
                                     lyr_id="lsmd_cont_ud801", distance="0.3km") + finish()
        v, _ = run(bad, gold)
        self.assertFalse(v["②"])

    def test_zoom_compound(self):
        gold = dict(GOLD_OVERLAP, 정답의도="지도 이동; 지도 확대/축소; 규제 저촉 분석; 분석 결과 표출",
                    정답파라미터=f"PNU={PNU}; 분석대상=용도지역·지구; 확대레벨=17",
                    정답시나리오="표출대상=대상 필지 도형; 지도제어=확대; 분석결과표출=용도지역·지구 중첩 도형")
        zoom = [("call.started", {"call_id": "cz", "callee": "map_navigation_b", "tool": "mapControl",
                                  "args": plan("mapControl", action="SET_ZOOM", zoomLevel="17")}),
                ("call.finished", {"call_id": "cz", "callee": "map_navigation_b", "tool": "mapControl", "status": "ok",
                                   "payload": {"status": "ok", "resolve": {"status": "ok"},
                                               "actions": [{"type": "map.SET_ZOOM", "result": {"zoomLevel": 17}}]}}),
                ("actions", {"call_id": "cz", "items": [{"type": "map.SET_ZOOM", "result": {"zoomLevel": 17}}]})]
        ev = locate_events() + zoom + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
        v, _ = run(ev, gold)
        self.assertTrue(v["정상수행"], v["불일치"])

    def test_zoom_level_numeric(self):                          # 문자열 "17" · 숫자 17 · "17.0" 모두 같은 레벨
        gold = dict(GOLD_OVERLAP, 정답의도="지도 이동; 지도 확대/축소; 규제 저촉 분석; 분석 결과 표출",
                    정답파라미터=f"PNU={PNU}; 분석대상=용도지역·지구; 확대레벨=17",
                    정답시나리오="표출대상=대상 필지 도형; 지도제어=확대; 분석결과표출=용도지역·지구 중첩 도형")
        zoom = [("call.started", {"call_id": "cz", "callee": "map_navigation_b", "tool": "mapControl",
                                  "args": plan("mapControl", action="SET_ZOOM", zoomLevel="17.0")}),
                ("call.finished", {"call_id": "cz", "callee": "map_navigation_b", "tool": "mapControl", "status": "ok",
                                   "payload": {"status": "ok", "resolve": {"status": "ok"}}}),
                ("actions", {"call_id": "cz", "items": [{"type": "map.SET_ZOOM", "result": {"zoomLevel": 17}}]})]
        ev = locate_events() + zoom + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
        v, _ = run(ev, gold)
        self.assertTrue(v["②"], v["불일치"])
        ev18 = [(e, {**d, "items": [{"type": "map.SET_ZOOM", "result": {"zoomLevel": 18}}]} if e == "actions" and d.get("call_id") == "cz" else
                 ({**d, "args": plan("mapControl", action="SET_ZOOM", zoomLevel="18")} if e == "call.started" and d.get("call_id") == "cz" else d))
                for e, d in ev]
        v, _ = run(ev18, gold)                                  # 규격: 줌 레벨은 판정에 쓰지 않음 → 18 이어도 ② 통과, 참고로만 기록
        self.assertTrue(v["②"], v["불일치"])
        self.assertTrue(any("확대 레벨" in x for x in v["참고"]))
        E.JUDGE_ZOOM_LEVEL = True                                 # 판정에 넣기로 정하면 숫자로 비교
        try:
            self.assertTrue(run(ev, gold)[0]["②"])
            self.assertFalse(run(ev18, gold)[0]["②"])
        finally:
            E.JUDGE_ZOOM_LEVEL = False

    def test_highlight_absorbed_into_move(self):                # SELECT_FEATURE → mapFeature(HIGHLIGHT) 가 붙어도 '의도 초과'가 아님
        hl = [("call.started", {"call_id": "ch", "callee": "map_navigation_b", "tool": "mapFeature",
                                "args": plan("mapFeature", action="HIGHLIGHT", featureRef=PNU)}),
              ("call.finished", {"call_id": "ch", "callee": "map_navigation_b", "tool": "mapFeature", "status": "ok",
                                 "payload": {"status": "ok", "resolve": {"status": "ok", "result": {"pnu": PNU}}}})]
        ev = locate_events() + hl + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
        v, ext = run(ev, GOLD_OVERLAP)
        self.assertTrue(v["정상수행"], v["불일치"])
        self.assertFalse(ext["unmapped_calls"])

    def test_road_plan_geom_counts_as_regulation(self):
        gold = {"사례ID": "AI6-0021", "명령유형": "도로 접근성 분석", "참조PNU": PNU,
                "정답의도": "지도 이동; 도로 접근성 분석; 규제도로 분석; 분석 결과 표출",
                "정답파라미터": f"PNU={PNU}; 분석대상=접도, 규제도로", "정답시나리오": "표출대상=대상 필지 도형; 분석결과표출=접도선"}
        ev = (locate_events()
              + analysis_events("road_access", "tool.road.contact", "operation", "CONTACT_SUMMARY", geom_key="road_contact_geojson")
              + analysis_events("road_access", "tool.road.contact", "operation", "ROAD_PLAN_GEOM", call_id="c4", geom=None)
              + finish())
        v, _ = run(ev, gold)
        self.assertTrue(v["정상수행"], v["불일치"])

    def test_action_names_old_and_new(self):
        self.assertEqual(E.action_type({"type": "map.flyTo"}), "MOVE_TO")
        self.assertEqual(E.action_type({"type": "map.focusParcel"}), "SELECT")
        self.assertEqual(E.action_type({"type": "map.zoomBy", "result": {"delta": -1}}), "ZOOM_OUT")
        self.assertEqual(E.action_type({"type": "map.moveLeft"}), "PAN")
        self.assertEqual(E.action_type({"type": "map.showLayer"}), "SHOW_LAYER")
        self.assertEqual(E.action_type({"type": "SELECT"}), "SELECT")
        self.assertEqual(E.action_type({"type": "ui.focus"}), "UI.FOCUS")


class Rules2(unittest.TestCase):                                  # 설계 결정 반영분
    def test_attr_is_auxiliary(self):                           # 토지이용계획 조회(ATTR)는 보조 호출 — 의도 초과 아님
        ev = (locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY")
              + analysis_events("land_attr_b", "parcelInfo", "action", "attr", call_id="c4", geom=None) + finish())
        v, ext = run(ev, GOLD_OVERLAP)
        self.assertTrue(v["정상수행"], v["불일치"])
        self.assertFalse(ext["unmapped_calls"])

    def _ask(self, reason):
        ev = locate_events(select_ok=False) + [("ask", {"key": "user:1", "kind": "user", "question": "어느 곳인가요?",
                                                        "options": ["1", "2"], "reason": reason})] + finish(status="asking")
        return run(ev, GOLD_OVERLAP)[0]

    def test_ask_cause_by_reason_option(self):                  # 회의에서 '사유 기준'을 택하면(ASK_CAUSE='reason')
        E.ASK_CAUSE = "reason"
        try:
            self.assertEqual(self._ask("ambiguous_candidates")["오류원인"], "핵심 파라미터 추출 오류")
            self.assertEqual(self._ask("missing_required_value")["오류원인"], "핵심 파라미터 추출 오류")
            self.assertEqual(self._ask("")["오류원인"], "의도 해석 오류")     # 결산 LLM 되묻기는 사유가 비어 옴 → 규격 순서
        finally:
            E.ASK_CAUSE = "order"
        v = self._ask("ambiguous_candidates")                    # 기본(규격 순서)
        self.assertEqual(v["오류원인"], "의도 해석 오류")
        self.assertTrue(v["세부원인"].startswith("되묻기"))

    def test_unlocated_default_is_spec_order(self):            # 기본: 규격 순서(①부터) — 위치 미확정은 세부 원인으로만
        ev = locate_events(select_ok=False)
        ev = [(e, {**d, "status": "error", "reason_code": "not_connected"}) if e == "call.finished" else (e, d) for e, d in ev]
        v, _ = run(ev + finish(), GOLD_OVERLAP)
        self.assertEqual(v["오류원인"], "의도 해석 오류")
        self.assertIn("위치 확정 호출 실패", v["세부원인"])
        self.assertTrue(any("위치 미확정" in m for m in v["불일치"]))

    def test_unlocated_rule_option(self):                     # 회의에서 위치 미확정 규칙을 택하면(UNLOCATED_RULE=True)
        E.UNLOCATED_RULE = True
        try:
            ev = locate_events(select_ok=False)
            ev = [(e, {**d, "status": "error", "reason_code": "not_connected"}) if e == "call.finished" else (e, d) for e, d in ev]
            self.assertEqual(run(ev + finish(), GOLD_OVERLAP)[0]["오류원인"], "GIS 지도 제어 실행 오류")
            self.assertEqual(run(locate_events(select_ok=False) + finish(), GOLD_OVERLAP)[0]["오류원인"], "핵심 파라미터 추출 오류")
        finally:
            E.UNLOCATED_RULE = False

    def test_search_candidate_pnu_not_used(self):             # 검색 호출의 첫 후보 PNU 는 ② 대조에 쓰지 않음
        ev = locate_events()
        ev = [(e, {**d, "payload": {**d["payload"], "resolve": {**d["payload"]["resolve"], "pnu": "1111111111111111111"}}})
              if e == "call.finished" and d.get("call_id") == "c1" else (e, d) for e, d in ev]
        v, ext = run(ev + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish(), GOLD_OVERLAP)
        self.assertTrue(v["정상수행"], v["불일치"])
        self.assertEqual(ext["search_pnus"], ["1111111111111111111"])

    def test_step_level_operation_and_names(self):             # 현행 계획 항목 {tool, operation, args} · clearMap = RESET_MAP · 마커 호출
        call = {"call_id": "x", "callee": "spatial_regulation", "tool": "ask",
                "args": {"_metadata": {"plan": [{"tool": "tool.spatial.analysis", "operation": "BUFFER", "args": {"pnu": PNU}}]}}}
        self.assertEqual(E.call_op(call), "BUFFER")
        self.assertEqual(E.call_op({"call_id": "y", "callee": "land_attr_b", "args": {"_text": "x"}}), "NORMAL")
        self.assertEqual(E.action_type({"type": "map.clearMap"}), "RESET_MAP")
        mk = [("call.started", {"call_id": "cm", "callee": "map_navigation_b", "tool": "ask",
                                "args": plan("mapMarker", action="ADD_MARKER", lng=1, lat=2)}),
              ("call.finished", {"call_id": "cm", "callee": "map_navigation_b", "tool": "ask", "status": "ok",
                                 "payload": {"status": "ok", "resolve": {"status": "ok"}}})]
        v, ext = run(locate_events() + mk + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish(),
                     GOLD_OVERLAP)
        self.assertTrue(v["정상수행"], v["불일치"])

    def test_spec_wording_scenario(self):                     # 규격 예시 '저촉 용도지역·지구 도형' → 용도지역·지구 중첩 분석으로 대조
        gold = dict(GOLD_OVERLAP, 정답시나리오="표출대상=대상 필지 도형; 분석결과표출=저촉 용도지역·지구 도형")
        ev = locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
        self.assertTrue(run(ev, gold)[0]["정상수행"])

    def test_radius_exact_and_set(self):                      # 반경은 단위만 맞추고 값은 같아야 함, 남는 반경도 오류
        gold = {"사례ID": "AI6-0013", "명령유형": "공간규제 분석", "참조PNU": PNU,
                "정답의도": "지도 이동; 주변 규제 분석; 분석 결과 표출",
                "정답파라미터": f"PNU={PNU}; 분석대상=토지거래계약에관한허가구역; 반경=500m",
                "정답시나리오": "표출대상=대상 필지 도형; 분석결과표출=반경 500m 내 토지거래계약에관한허가구역 도형"}

        def ev(*dist):
            out = locate_events() + analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "LAYER_FIND",
                                                    geom=None, lyr_query="토지거래계약에관한허가구역")
            for i, d in enumerate(dist):
                out += analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "LAYER_NEARBY",
                                       call_id=f"n{i}", lyr_id="klip_c_uq175", distance=d)
            return out + finish()
        self.assertTrue(run(ev("0.5km"), gold)[0]["②"])
        self.assertFalse(run(ev("500.4"), gold)[0]["②"])
        self.assertFalse(run(ev("500", "300"), gold)[0]["②"])

    def test_layer_judged_by_chosen_layer(self):             # 검색어가 맞아도 공간오케가 다른 레이어를 골랐으면 ② 오류
        gold = {"사례ID": "AI6-0012", "명령유형": "공간규제 분석", "참조PNU": PNU,
                "정답의도": "지도 이동; 규제 저촉 분석; 분석 결과 표출",
                "정답파라미터": f"PNU={PNU}; 분석대상=문화유산보호",
                "정답시나리오": "표출대상=대상 필지 도형; 분석결과표출=문화유산보호 저촉 도형"}

        def ev(picked):
            find = [("call.started", {"call_id": "cf", "callee": "spatial_regulation", "tool": "ask",
                                      "args": plan("tool.spatial.analysis", operation="LAYER_FIND", pnu=PNU, lyr_query="문화유산보호")}),
                    ("call.finished", {"call_id": "cf", "callee": "spatial_regulation", "tool": "ask", "status": "ok",
                                       "payload": {"status": "ok", "resolve": {"status": "ok", "result": {"layers": [
                                           {"name": "문화유산/문화유산보호 · 문화유산", "id": "klip_c_uo301"},
                                           {"name": "문화유산보호구역 · 문화유산", "id": "lt_c_xx999"}]}}}})]
            return (locate_events() + find
                    + analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "INTERSECTIONLAYER",
                                      call_id="ci", lyr_id=picked) + finish())
        v, ext = run(ev("klip_c_uo301"), gold)
        self.assertTrue(v["정상수행"], v["불일치"])
        self.assertEqual(ext["spec_output"]["핵심파라미터"]["분석대상"], ["문화유산/문화유산보호"])
        v, _ = run(ev("lt_c_xx999"), gold)
        self.assertFalse(v["②"])

    def test_extra_target_and_radius_fail_param(self):
        gold = dict(GOLD_OVERLAP, 정답의도="지도 이동; 규제 저촉 분석; 분석 결과 표출")
        ev = (locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY")
              + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_PLAN", call_id="c4") + finish())
        v, _ = run(ev, gold)                                     # 지구단위계획까지 붙임: ①도 맞고(같은 의도) ②에서 걸러야 함
        self.assertTrue(v["①"])
        self.assertFalse(v["②"])
        self.assertTrue(any("분석대상 초과" in m for m in v["불일치"]))
        ev = (locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY")
              + analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "BUFFER", call_id="c4", distance="100")
              + finish())
        v, _ = run(ev, dict(GOLD_OVERLAP, 정답의도="지도 이동; 규제 저촉 분석; 버퍼 분석; 분석 결과 표출"))
        self.assertFalse(v["②"])
        self.assertTrue(any("정답에 없는 반경" in m for m in v["불일치"]))

    def test_target_parcel_geometry_required(self):           # 대상 필지 도형이 안 오면 ③ 오류
        ev = locate_events()
        ev = [(e, {**d, "items": [{**i, "result": {k: v for k, v in i["result"].items() if k != "geometry"}}
                                  if i.get("type") == "map.SELECT" else i for i in d["items"]]}) if e == "actions" else (e, d)
              for e, d in ev]
        ev = [(e, {**d, "payload": {**d["payload"], "actions": [{**i, "result": {k: v for k, v in i["result"].items() if k != "geometry"}}
                                                               if i.get("type") == "map.SELECT" else i for i in d["payload"]["actions"]]}})
              if e == "call.finished" and d.get("call_id") == "c2" else (e, d) for e, d in ev]
        v, _ = run(ev + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish(), GOLD_OVERLAP)
        self.assertFalse(v["③"], v["불일치"])
        self.assertEqual(v["오류원인"], "GIS 지도 제어 실행 오류")

    def test_layer_named_like_target(self):                   # 반경 300m 안 '지구단위계획구역' 레이어 — 중첩 분석이 아니라 레이어 분석
        gold = {"사례ID": "AI6-0014", "명령유형": "공간규제 분석", "참조PNU": PNU,
                "정답의도": "지도 이동; 주변 규제 분석; 분석 결과 표출",
                "정답파라미터": f"PNU={PNU}; 분석대상=지구단위계획구역; 반경=300m",
                "정답시나리오": "표출대상=대상 필지 도형; 분석결과표출=반경 300m 내 지구단위계획구역 도형"}
        ev = (locate_events()
              + analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "LAYER_FIND", geom=None,
                                lyr_query="지구단위계획구역")
              + analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "LAYER_NEARBY", call_id="c4",
                                lyr_id="lsmd_cont_uq161", distance="300") + finish())
        v, _ = run(ev, gold)
        self.assertTrue(v["정상수행"], v["불일치"])
        ev2 = (locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_PLAN") + finish())
        v2, _ = run(ev2, gold)                                   # 중첩 분석으로 가면 ①(주변 규제 분석 누락)에서 걸림
        self.assertFalse(v2["①"])

    def test_criteria_fingerprint_tracks_options(self):
        a = E.criteria_fingerprint()
        E.REQUIRE_TARGET_GEOMETRY = not E.REQUIRE_TARGET_GEOMETRY
        try:
            self.assertNotEqual(a, E.criteria_fingerprint())
        finally:
            E.REQUIRE_TARGET_GEOMETRY = not E.REQUIRE_TARGET_GEOMETRY
        self.assertEqual(a, E.criteria_fingerprint())


class GoldPnu(unittest.TestCase):
    def test_excel_mangled_pnu(self):                           # 엑셀이 19자리를 15자리로 자른 참조PNU → 점검에서 잡고, 판정은 정답파라미터 PNU
        gold = dict(GOLD_OVERLAP, 참조PNU="4729012300100310000")
        self.assertEqual(E.gold_pnu_of(gold), PNU)
        ts = [{"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "명령문": "x"}]
        self.assertTrue(any("엑셀" in p for p in E.validate(ts, [gold])))
        v, _ = run(locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish(), gold)
        self.assertTrue(v["정상수행"], v["불일치"])
        sci = dict(GOLD_OVERLAP, 참조PNU="4.72901E+18", 정답파라미터="위치=경산시 신천동 31-1; 분석대상=용도지역·지구")
        self.assertTrue(any("엑셀이 숫자로" in p for p in E.validate(ts, [sci])))


class Score(unittest.TestCase):
    def test_floor_pct(self):
        self.assertEqual(E.floor_pct(2, 3), 66.66)
        self.assertEqual(E.floor_pct(45, 50), 90.0)
        self.assertEqual(E.floor_pct(1, 3, dec=1), 33.3)

    def test_score_tables_and_missing_case(self):
        ts = [{"사례ID": f"AI6-{i:04d}", "명령유형": t, "명령문": "x"} for i, t in
              [(1, "공간규제 분석"), (2, "도로 접근성 분석"), (3, "산지ㆍ농지 규제 분석")]]
        logs = [{"사례ID": "AI6-0001", "판정": "정상 수행", "오류원인": "", "불일치내용": []},
                {"사례ID": "AI6-0002", "판정": "오류", "오류원인": "GIS 지도 제어 실행 오류", "불일치내용": ["③ 접도선 도형 없음"]}]
        s = E.score(logs, ts)
        self.assertEqual((s["n"], s["ok"], s["value"]), (3, 1, 33.33))
        causes = {r["오류 원인"]: r["건수"] for r in s["오류원인별"]}
        self.assertEqual(causes["응답 오류"], 1)                       # AI6-0003 미실행
        types = {r["명령 유형"]: r for r in s["유형별"]}
        self.assertEqual(types["산지·농지 규제 분석"]["오류"], 1)       # 가운뎃점 변형(ㆍ)도 같은 유형

    def test_validate(self):
        ts = [{"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "명령문": "x"}]
        ans = [dict(GOLD_OVERLAP, 정답의도="지도 이동; 엉뚱한 의도")]
        probs = E.validate(ts, ans)
        self.assertTrue(any("엉뚱한 의도" in p for p in probs))
        self.assertTrue(any("15건" in p for p in probs))


class Rescore(unittest.TestCase):                                  # 다시 판정: 원본 폴더는 그대로, 새 폴더는 한 겹 이름
    def test_rescore_twice_keeps_one_suffix_and_first_source(self):
        import shutil
        import tempfile
        import spec_runner_ai6 as R6
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "20261110_120000")
            os.makedirs(os.path.join(src, "raw"))
            os.makedirs(os.path.join(src, "data"))
            ev = locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
            with open(os.path.join(src, "raw", "AI6-0001.json"), "w", encoding="utf-8") as f:
                json.dump({"사례ID": "AI6-0001", "response": dict(OK_RES, events=[{"event": e, "data": d} for e, d in ev])}, f)
            def put(name, rows):
                import csv
                with open(os.path.join(src, "data", name), "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(rows[0]))
                    w.writeheader()
                    w.writerows(rows)
            put("answer_ai6.csv", [GOLD_OVERLAP])
            put("testset_ai6.csv", [{"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "명령문": "경산시 신천동 31-1 용도지역 보여줘"}])
            with open(os.path.join(src, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({"공식": True, "started": "2026-11-10 12:00:00"}, f)
            with open(os.path.join(src, "raw", "AI6-0001.json"), "rb") as f:
                before = f.read()
            new1, s1 = R6.rescore(src, None, None, None)
            new2, s2 = R6.rescore(new1, None, None, None)
            self.assertEqual(s1["value"], 100.0)
            self.assertEqual(s2["value"], 100.0)
            self.assertEqual(os.path.basename(new2).count("재판정"), 1)          # '_재판정_…_재판정_…' 로 겹치지 않음
            self.assertNotEqual(new1, new2)
            with open(os.path.join(new2, "meta.json"), encoding="utf-8") as f:
                m2 = json.load(f)
            self.assertEqual(m2["원본"], os.path.normpath(src))                   # 처음 실행한 폴더를 가리킴
            self.assertFalse(m2["공식"])
            self.assertEqual(sum(str(w).startswith("다시 판정(") for w in m2["예비사유"]), 1)
            with open(os.path.join(src, "raw", "AI6-0001.json"), "rb") as f:
                self.assertEqual(f.read(), before)
            self.assertFalse(os.path.exists(os.path.join(src, E.GIS_LOG)))       # 원본 폴더에는 판정 기록을 쓰지 않음
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class _Handler(http.server.BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path.endswith("/cancel"):
            _Handler.cancels = getattr(_Handler, "cancels", 0) + 1
            fail = _Handler.mode == "cancel_fail_once" and _Handler.cancels == 1
            self.send_response(503 if fail else 200)
            self.end_headers()
            return
        if _Handler.mode == "500":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        tid = body.get("thread_id")
        self.wfile.write(f"event: turn.started\r\ndata: {json.dumps({'thread_id': tid})}\r\n\r\n".encode())
        self.wfile.flush()
        if _Handler.mode == "slow":
            for _ in range(30):
                time.sleep(0.1)
                try:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                except OSError:
                    return
        msg = {"thread_id": tid, "status": "delivered", "answer": body.get("message"), "stop_reason": "model_completed"}
        self.wfile.write(f"event: turn.finished\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"event: after\ndata: {}\n\n")
        self.wfile.flush()


class Rules(unittest.TestCase):
    def _csv(self, text):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig")
        f.write(text)
        f.close()
        self.addCleanup(os.remove, f.name)
        return f.name

    def test_wrong_format_is_rejected(self):                       # 설명용 표(다른 칸 이름)를 넣으면 조용히 0% 가 아니라 막음
        p = self._csv("정답 의도,공간오케 인텐트 코드,실행 신호(호출)\n지도 이동,MAP_MOVE,map_navigation_b mapFeature\n")
        with self.assertRaises(ValueError):
            E.load_rules(p)

    def test_unknown_kind_is_rejected(self):
        p = self._csv("정답의도,근거,값,비고\n지도 이동,신호,SELECT,\n")
        with self.assertRaises(ValueError):
            E.load_rules(p)

    def test_template_roundtrip(self):                              # 기본 대응표를 CSV 로 내보낸 것 = 기본 대응표
        p = self._csv(E.rules_csv())
        self.assertEqual(sorted((r["intent"], r["kind"], sorted(r["values"])) for r in E.load_rules(p)),
                         sorted((r["intent"], r["kind"], sorted(r["values"])) for r in E.load_rules()))


class Client(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        cls.srv.daemon_threads = True
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_ok_stops_at_turn_finished(self):
        _Handler.mode = "ok"
        r = C.stream_chat("명령문 그대로", thread_id="qa-1", base=self.base, deadline=5)
        self.assertEqual(r["ended_by"], "turn.finished")
        self.assertEqual([e for e, _ in r["events"]], ["turn.started", "turn.finished"])
        self.assertEqual(r["events"][1][1]["answer"], "명령문 그대로")
        self.assertEqual(r["request"], {"message": "명령문 그대로", "thread_id": "qa-1"})

    def test_deadline(self):
        _Handler.mode = "slow"
        t0 = time.time()
        r = C.stream_chat("x", base=self.base, deadline=0.8)
        self.assertEqual(r["ended_by"], "deadline")
        self.assertLess(time.time() - t0, 2.5)
        self.assertIn("대기 상한", r["error"])
        self.assertEqual(r["cancel"], 200)

    def test_deadline_without_urllib3_shutdown(self):            # urllib3 < 2.3 (shutdown() 없음) 에서도 상한을 지킴
        import urllib3.response as R
        _Handler.mode = "slow"
        had = hasattr(R.HTTPResponse, "shutdown")
        orig = getattr(R.HTTPResponse, "shutdown", None)

        def gone(self):
            raise AttributeError("shutdown")
        R.HTTPResponse.shutdown = gone
        try:
            t0 = time.time()
            r = C.stream_chat("x", base=self.base, deadline=0.8)
            self.assertEqual(r["ended_by"], "deadline")
            self.assertLess(time.time() - t0, 2.5)
        finally:
            if had:
                R.HTTPResponse.shutdown = orig
            else:
                del R.HTTPResponse.shutdown

    def test_cancel_retried_once(self):                        # 취소 요청이 실패하면 한 번 더 보냄
        _Handler.mode, _Handler.cancels = "cancel_fail_once", 0
        self.assertEqual(C.cancel("t-x", base=self.base), 200)
        self.assertEqual(_Handler.cancels, 2)

    def test_http_error(self):
        _Handler.mode = "500"
        r = C.stream_chat("x", base=self.base, deadline=5)
        self.assertEqual((r["ended_by"], r["http_status"]), ("http", 500))

    def test_connection_refused(self):
        r = C.stream_chat("x", base="http://127.0.0.1:9", deadline=2)
        self.assertEqual(r["ended_by"], "exception")


if __name__ == "__main__":
    unittest.main()
