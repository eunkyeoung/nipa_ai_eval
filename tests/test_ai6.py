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

    def test_map_control_values(self):                          # ③ 지도제어 = 이동·확대·축소·레이어 표시, 여럿은 ',' — 그 밖의 값은 점검에서 막음
        gold = dict(GOLD_OVERLAP, 정답의도="지도 이동; 지도 확대/축소; 규제 저촉 분석; 분석 결과 표출")
        zoom = [("call.started", {"call_id": "cz", "callee": "map_navigation_b", "tool": "mapControl",
                                  "args": plan("mapControl", action="SET_ZOOM", zoomLevel="17")}),
                ("call.finished", {"call_id": "cz", "callee": "map_navigation_b", "tool": "mapControl", "status": "ok",
                                   "payload": {"status": "ok", "resolve": {"status": "ok"}}}),
                ("actions", {"call_id": "cz", "items": [{"type": "map.SET_ZOOM", "result": {"zoomLevel": 17}}]})]
        ev = locate_events() + zoom + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
        for val, ok in (("확대", True), ("이동, 확대", True), ("이동", True), ("레이어 표시", False), ("확대(17레벨)", False)):
            g = dict(gold, 정답시나리오=f"표출대상=대상 필지 도형; 지도제어={val}; 분석결과표출=용도지역·지구 중첩 도형")
            self.assertEqual(run(ev, g)[0]["③"], ok, val)
        layer = [("actions", {"call_id": "cl", "items": [{"type": "map.showLayer", "params": {"layers": [{"id": "lt_c_uq111", "name": "도시지역"}]}}]})]
        g = dict(gold, 정답시나리오="표출대상=대상 필지 도형; 지도제어=레이어 표시; 분석결과표출=용도지역·지구 중첩 도형")
        self.assertTrue(run(ev[:-3] + layer + finish(), g)[0]["③"])
        ts = [{"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "명령문": "x"}]
        bad = dict(gold, 정답시나리오="표출대상=대상 필지 도형; 지도제어=확대(17레벨)")
        self.assertTrue(any("지도제어 값" in p for p in E.blocking(E.validate(ts, [bad]))))

    def test_move_and_zoom_counts_as_zoom(self):                # 일반 모드 map_navigation_b 는 '이동해서 17레벨로 확대'를 MOVE_AND_ZOOM 하나로 보냄
        gold = dict(GOLD_OVERLAP, 정답의도="지도 이동; 지도 확대/축소; 규제 저촉 분석; 분석 결과 표출",
                    정답시나리오="표출대상=대상 필지 도형; 지도제어=확대; 분석결과표출=용도지역·지구 중첩 도형")

        def mz(result):
            item = {"type": "map.MOVE_AND_ZOOM", "result": result}
            return [("call.started", {"call_id": "cz", "callee": "map_navigation_b", "tool": "ask", "args": {"query": "q", "_text": "q"}}),
                    ("call.finished", {"call_id": "cz", "callee": "map_navigation_b", "tool": "ask", "status": "ok",
                                       "payload": {"status": "ok", "resolve": {"success": True, "action": "MOVE_AND_ZOOM", "result": result}}}),
                    ("actions", {"call_id": "cz", "items": [{"type": "ui.focus", "target": "map"}, item]})]
        base = locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY")
        v, _ = run(base + mz({"lng": 1, "lat": 2, "zoom": 17, "crs": "EPSG:5186"}) + finish(), gold)
        self.assertTrue(v["정상수행"], v["불일치"])
        v, _ = run(base + mz({"lng": 1, "lat": 2}) + finish(), gold)    # 줌 값이 없으면 확대로 안 셈
        self.assertFalse(v["③"])

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
        self.assertNotIn("land_attr_b:attr", ext["spec_output"]["핵심파라미터"]["분석대상"])

    def test_auxiliary_call_not_shown_as_target_or_type(self):  # 실측 AI6-0042: ATTR(보조) 뒤 개발가능지 → 명령유형·분석대상은 개발가능지 기준
        gold = dict(GOLD_OVERLAP, 명령유형="산지·농지 규제 분석", 정답의도="지도 이동; 개발가능지 추출; 분석 결과 표출",
                    정답파라미터=f"PNU={PNU}; 분석대상=개발가능지", 정답시나리오="표출대상=대상 필지 도형; 분석결과표출=개발가능 영역 도형")
        ev = (locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "ATTR", call_id="c3", geom=None)
              + analysis_events("land_regulation", "tool.land.regulation", "operation", "DEVELOPABLE_AREA", call_id="c4") + finish())
        v, ext = run(ev, gold)
        self.assertTrue(v["정상수행"], v["불일치"])
        so = ext["spec_output"]
        self.assertEqual(so["실행대상기능"]["명령유형"], "산지·농지 규제 분석")
        self.assertEqual(so["핵심파라미터"]["분석대상"], ["개발가능지"])

    def test_unmapped_intent_shown_in_spec_output(self):        # 대응표 밖 호출은 규격 출력 '의도'에도 보임 — ① 판정에 쓴 집합과 같게
        ev = (locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY")
              + analysis_events("spatial_regulation", "tool.x", "operation", "NEW_OP", call_id="c9") + finish())
        v, ext = run(ev, GOLD_OVERLAP)
        self.assertFalse(v["①"])
        self.assertIn("(대응표 밖) spatial_regulation:NEW_OP", ext["spec_output"]["의도"])

    def test_intent_mismatch_text_is_stable(self):              # 불일치 문구 순서 = 정답 순서 (다시 판정해도 같은 문구)
        gold = dict(GOLD_OVERLAP, 정답의도="지도 이동; 산지전용 검토; 지도 확대/축소; 분석 결과 표출")
        ev = [("turn.started", {"thread_id": "t1"})] + finish(answer="지원하지 않는 요청입니다.")
        v = run(ev, gold)[0]
        self.assertTrue(v["불일치"][0].startswith("① 의도 누락 지도 이동, 산지전용 검토, 지도 확대/축소, 분석 결과 표출"), v["불일치"][0])

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

    def test_unlocated_system_fail_is_response_error(self):    # 위치 확정 호출이 시스템 사유로 실패 → 규격 '시스템 오류로 응답하지 못한 사례' = 응답 오류
        ev = locate_events(select_ok=False)
        ev = [(e, {**d, "status": "error", "reason_code": "not_connected"}) if e == "call.finished" else (e, d) for e, d in ev]
        v, _ = run(ev + finish(), GOLD_OVERLAP)
        self.assertEqual(v["오류원인"], "응답 오류")
        self.assertFalse(v["정상수행"])
        self.assertIn("INTEGRATED_LOCATION_SEARCH error", v["응답오류"])
        self.assertTrue(v["불일치"][0].startswith("시스템 오류로 위치를 확정하지 못함"))
        self.assertIn("위치 확정 호출 실패", v["세부원인"])
        self.assertTrue(any("위치 미확정" in m for m in v["불일치"]))
        self.assertIs(v["①"], False)                                # ①②③ 대조 결과는 그대로 남김
        E.SYSTEM_FAIL_RULE = False                                  # 끄면 규격 순서(①부터)만 — 위치 미확정은 세부 원인으로만
        try:
            v = run(ev + finish(), GOLD_OVERLAP)[0]
            self.assertEqual(v["오류원인"], "의도 해석 오류")
            self.assertEqual(v["응답오류"], "")
        finally:
            E.SYSTEM_FAIL_RULE = True

    def test_system_fail_real_shapes(self):                     # 2026-11-12 실측 2건 모양: 도구 오류 목록 · 피호출자 실패 보고(문자열)
        def failing(errors, resolve):
            pay = {"status": "error", "errors": errors}
            if resolve:
                pay["resolve"] = {"status": resolve}
            return [("turn.started", {"thread_id": "t1"}),
                    ("call.started", {"call_id": "c1", "callee": "map_navigation_b", "tool": "locationSearch",
                                      "args": plan("locationSearch", action="INTEGRATED_LOCATION_SEARCH", query="경산시 신천동 31-1")}),
                    ("call.finished", {"call_id": "c1", "callee": "map_navigation_b", "tool": "locationSearch", "status": "error",
                                       "reason_code": "rejected", "payload": pay})] + \
                finish(answer="위치 검색이 일시적으로 되지 않아 위치를 확인하지 못했습니다.")
        conn = [{"tool": "locationSearch", "kind": "tool_error", "layer": "mcp", "recoverable": False,
                 "message": "공간분석 API 요청에 실패했습니다: /api/address/location (All connection attempts failed)"}]
        v = run(failing(conn, "failed"), GOLD_OVERLAP)[0]
        self.assertEqual(v["오류원인"], "응답 오류")
        self.assertIn("All connection attempts failed", v["응답오류"])
        v = run(failing("피호출자가 실패를 보고했습니다. map_navigation_b: 응답에 task 가 없습니다.", None), GOLD_OVERLAP)[0]
        self.assertEqual(v["오류원인"], "응답 오류")
        self.assertIn("응답에 task 가 없습니다", v["응답오류"])
        rows = [{"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "판정": "오류", "오류원인": v["오류원인"], "불일치내용": v["불일치"]}]
        s = E.score(rows, [{"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "명령문": "x"}])
        self.assertEqual(s["응답오류"], 1)
        self.assertEqual({r["오류 원인"]: r["건수"] for r in s["오류원인별"]}["응답 오류"], 1)

    def test_system_fail_then_recovered_is_judged_normally(self):   # 시스템 실패 뒤 다시 찾아 끝까지 했으면 응답 오류가 아님
        fail = [("call.started", {"call_id": "c0", "callee": "map_navigation_b", "tool": "locationSearch",
                                  "args": plan("locationSearch", action="INTEGRATED_LOCATION_SEARCH", query="경산시 신천동 31-1")}),
                ("call.finished", {"call_id": "c0", "callee": "map_navigation_b", "tool": "locationSearch", "status": "error",
                                   "reason_code": "rejected", "payload": {"status": "error", "errors": [{"message": "timeout"}]}})]
        ok = locate_events()
        ev = ok[:1] + fail + ok[1:] + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
        v = run(ev, GOLD_OVERLAP)[0]
        self.assertTrue(v["정상수행"], v["불일치"])
        self.assertEqual(v["응답오류"], "")

    def test_unlocated_rule_option(self):                     # 회의에서 위치 미확정 규칙을 택하면(UNLOCATED_RULE=True)
        ev = locate_events(select_ok=False)
        ev = [(e, {**d, "status": "error", "reason_code": "not_connected"}) if e == "call.finished" else (e, d) for e, d in ev]
        E.UNLOCATED_RULE = True
        try:
            self.assertEqual(run(ev + finish(), GOLD_OVERLAP)[0]["오류원인"], "응답 오류")       # 시스템 실패는 응답 오류가 먼저
            self.assertEqual(run(locate_events(select_ok=False) + finish(), GOLD_OVERLAP)[0]["오류원인"], "핵심 파라미터 추출 오류")
            E.SYSTEM_FAIL_RULE = False
            self.assertEqual(run(ev + finish(), GOLD_OVERLAP)[0]["오류원인"], "GIS 지도 제어 실행 오류")
        finally:
            E.UNLOCATED_RULE = False
            E.SYSTEM_FAIL_RULE = True

    def test_unlocated_needs_location_call_and_ok_select(self):  # 위치 미확정 = 위치 확정 호출은 했고, 성공한 SELECT·분석 호출이 없음
        none = [("turn.started", {"thread_id": "t1"})] + finish(answer="지원하지 않는 요청입니다.")   # 호출 없음 → ① (P02)
        sel = [(e, {**d, "status": "error", "reason_code": "rejected", "payload": None})
               if e == "call.finished" and d.get("call_id") == "c2" else (e, d)
               for e, d in locate_events() if not (e == "actions" and d.get("call_id") == "c2")] + finish()

        def search(st):                                           # 예전 모양: 호출은 ok, resolve 가 failed(시스템 실패) · no_data(못 찾음)
            return [(e, {**d, "payload": {"status": "ok", "resolve": {"status": st}}}) if e == "call.finished" else (e, d)
                    for e, d in locate_events(select_ok=False) if e != "actions"] + finish()
        v = run(none, GOLD_OVERLAP)[0]
        self.assertEqual(v["오류원인"], "의도 해석 오류")
        self.assertNotIn("위치 미확정", v["세부원인"])
        self.assertIn("위치 확정 호출 실패: SELECT error", run(sel, GOLD_OVERLAP)[0]["세부원인"])
        self.assertIn("위치 확정 호출 실패", run(search("failed"), GOLD_OVERLAP)[0]["세부원인"])
        self.assertIn("대상 필지를 정하지 못함", run(search("no_data"), GOLD_OVERLAP)[0]["세부원인"])
        self.assertEqual(run(sel, GOLD_OVERLAP)[0]["오류원인"], "응답 오류")              # 기본: 시스템 사유 실패 = 응답 오류
        self.assertEqual(run(search("failed"), GOLD_OVERLAP)[0]["오류원인"], "응답 오류")
        self.assertEqual(run(search("no_data"), GOLD_OVERLAP)[0]["오류원인"], "의도 해석 오류")   # 못 찾음은 시스템 오류가 아님 → 규격 순서
        E.UNLOCATED_RULE, E.SYSTEM_FAIL_RULE = True, False
        try:
            self.assertEqual(run(none, GOLD_OVERLAP)[0]["오류원인"], "의도 해석 오류")
            self.assertEqual(run(sel, GOLD_OVERLAP)[0]["오류원인"], "GIS 지도 제어 실행 오류")
            self.assertEqual(run(search("failed"), GOLD_OVERLAP)[0]["오류원인"], "GIS 지도 제어 실행 오류")
            self.assertEqual(run(search("no_data"), GOLD_OVERLAP)[0]["오류원인"], "핵심 파라미터 추출 오류")
        finally:
            E.UNLOCATED_RULE, E.SYSTEM_FAIL_RULE = False, True

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

    def test_buffer_label_with_radius_word(self):            # '반경 100m 버퍼 도형' — '반경'이 들어 있어도 버퍼 도형으로 대조
        gold = dict(GOLD_OVERLAP, 정답의도="지도 이동; 규제 저촉 분석; 버퍼 분석; 분석 결과 표출",
                    정답파라미터=f"PNU={PNU}; 분석대상=용도지역·지구; 반경=100m",
                    정답시나리오="표출대상=대상 필지 도형; 분석결과표출=용도지역·지구 중첩 도형, 반경 100m 버퍼 도형")
        ev = (locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY")
              + analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "BUFFER", call_id="c4", distance="100")
              + finish())
        v, _ = run(ev, gold)
        self.assertTrue(v["정상수행"], v["불일치"])

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

    def test_layer_name_from_live_facts(self):              # 17006 실제 LAYER_FIND 응답(11-10 실측 모양): 이름·ID 는 resolve.facts 의 '레이어'·'레이어ID'
        gold = {"사례ID": "AI6-0011", "명령유형": "공간규제 분석", "참조PNU": PNU,
                "정답의도": "지도 이동; 규제 저촉 분석; 분석 결과 표출",
                "정답파라미터": f"PNU={PNU}; 분석대상=문화유산보호",
                "정답시나리오": "표출대상=대상 필지 도형; 분석결과표출=문화유산보호 저촉 도형"}

        def find(cid, query, ids, names):
            facts = {"찾은이름": query, "후보수": len(ids), "레이어": names, "레이어ID": ids}
            return [("call.started", {"call_id": cid, "callee": "spatial_regulation", "tool": "ask",
                                      "args": plan("tool.spatial.analysis", operation="LAYER_FIND", pnu=PNU, lyr_query=query)}),
                    ("call.finished", {"call_id": cid, "callee": "spatial_regulation", "tool": "ask", "status": "ok",
                                       "payload": {"status": "ok", "resolve": {"status": "ok", "pnu": None, "facts": facts, "geom": {},
                                                                               "input": {"operation": "LAYER_FIND", "lyr_query": query}},
                                                   "facts": {"agent_facts": {"id": "spatial_regulation", **facts}}}})]

        def ev(picked, retry=True):                          # 첫 검색어는 0건 → 다른 이름으로 다시 찾아 그 레이어로 교차
            return (locate_events() + (find("f1", "문화재보호구역", [], []) if retry else [])
                    + find("f2", "문화유산보호", ["klip_c_uo301"], ["문화유산/문화유산보호 · 국토교통부"])
                    + analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "INTERSECTIONLAYER",
                                      call_id="ci", lyr_id=picked) + finish())
        v, ext = run(ev("klip_c_uo301"), gold)
        self.assertTrue(v["정상수행"], v["불일치"])
        self.assertEqual([c["name"] for c in ext["chosen_layers"]], ["문화유산/문화유산보호 · 국토교통부"])
        self.assertFalse(any("검색어(lyr_query)로 대조" in n for n in v["참고"]))
        v, _ = run(ev("lsmd_cont_uq161", retry=False), gold)  # 목록에 없는 ID 를 골랐으면 이름을 모름 → 검색어로 대조하고 참고에 남김
        self.assertTrue(any("검색어(lyr_query)로 대조" in n for n in v["참고"]))
        self.assertFalse(v["②"])                            # 이름 없는 호출의 레이어 자료 대조(문화유산보호 ≠ lsmd_cont_uq161)
        self.assertTrue(any("자료 불일치" in m and "lsmd_cont_uq161" in m for m in v["불일치"]), v["불일치"])

    def test_layer_id_from_facts_on_core_llm_route(self):    # 계획 없는 레이어 분석(코어 LLM 경로): args 는 {query,_text} — ID 는 resolve.facts '레이어'
        gold = {"사례ID": "AI6-0014", "명령유형": "공간규제 분석", "참조PNU": PNU,
                "정답의도": "지도 이동; 주변 규제 분석; 분석 결과 표출",
                "정답파라미터": f"PNU={PNU}; 분석대상=지구단위계획구역; 반경=300m",
                "정답시나리오": "표출대상=대상 필지 도형; 분석결과표출=반경 300m 안 지구단위계획구역 도형"}

        def ev(lid):
            facts = {"교차건수": 2, "교차여부": True, "레이어": lid, "도형": "지도에 표시됨(2건)", "반경m": 300.0}
            return (locate_events()
                    + [("call.started", {"call_id": "cn", "callee": "spatial_regulation", "tool": "ask",
                                         "args": {"query": "주변 300m 지구단위계획구역", "_text": "주변 300m 지구단위계획구역"}}),
                       ("call.finished", {"call_id": "cn", "callee": "spatial_regulation", "tool": "ask", "status": "ok",
                                          "payload": {"status": "ok", "resolve": {
                                              "status": "ok", "pnu": PNU, "facts": facts, "geom": {"result_geojson": [POLY]},
                                              "input": {"operation": "LAYER_NEARBY", "pnu": PNU, "input_type": "pnu",
                                                        "distance": 300.0, "crs": 5186}}}})]
                    + finish())
        v, ext = run(ev("lsmd_cont_uq161"), gold)            # 11-10 실측 모양 — 도로 테이블이면 ② 오류
        self.assertEqual([c["lyr_id"] for c in ext["chosen_layers"]], ["lsmd_cont_uq161"])
        self.assertFalse(v["②"])
        self.assertTrue(any("자료 불일치" in m for m in v["불일치"]), v["불일치"])
        v, _ = run(ev("klip_c_uq161"), gold)
        self.assertFalse(any("자료 불일치" in m for m in v["불일치"]), v["불일치"])

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
                                lyr_id="klip_c_uq161", distance="300") + finish())
        v, _ = run(ev, gold)
        self.assertTrue(v["정상수행"], v["불일치"])
        ev2 = (locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_PLAN") + finish())
        v2, _ = run(ev2, gold)                                   # 중첩 분석으로 가면 ①(주변 규제 분석 누락)에서 걸림
        self.assertFalse(v2["①"])

    def test_layer_id_must_be_layer_data(self):               # 이름은 '지구단위계획구역'인데 ID 가 도로 테이블(11-10 BE 연결 결함) → ② 오류
        gold = {"사례ID": "AI6-0014", "명령유형": "공간규제 분석", "참조PNU": PNU,
                "정답의도": "지도 이동; 주변 규제 분석; 분석 결과 표출",
                "정답파라미터": f"PNU={PNU}; 분석대상=지구단위계획구역; 반경=300m",
                "정답시나리오": "표출대상=대상 필지 도형; 분석결과표출=반경 300m 내 지구단위계획구역 도형"}

        def ev(lid):
            find = [("call.started", {"call_id": "cf", "callee": "spatial_regulation", "tool": "ask",
                                      "args": plan("tool.spatial.analysis", operation="LAYER_FIND", pnu=PNU, lyr_query="지구단위계획구역")}),
                    ("call.finished", {"call_id": "cf", "callee": "spatial_regulation", "tool": "ask", "status": "ok",
                                       "payload": {"status": "ok", "resolve": {"status": "ok", "result": {"layers": [
                                           {"name": "지구단위계획구역 · 국토교통부", "id": lid}]}}}})]
            return (locate_events() + find + analysis_events("spatial_regulation", "tool.spatial.analysis", "operation", "LAYER_NEARBY",
                                                             call_id="c4", lyr_id=lid, distance="300") + finish())
        v, _ = run(ev("lsmd_cont_uq161"), gold)
        self.assertFalse(v["②"])
        self.assertEqual(v["오류원인"], "핵심 파라미터 추출 오류")
        self.assertTrue(any("자료 불일치" in m and "lsmd_cont_uq161" in m for m in v["불일치"]), v["불일치"])
        self.assertTrue(run(ev("klip_c_uq161"), gold)[0]["정상수행"])
        E.LAYER_ID_CHECK = False
        try:
            self.assertTrue(run(ev("lsmd_cont_uq161"), gold)[0]["정상수행"])
        finally:
            E.LAYER_ID_CHECK = True
        gold2 = dict(gold, 정답의도="지도 이동; 규제 저촉 분석; 분석 결과 표출", 정답파라미터=f"PNU={PNU}; 분석대상=지구단위계획구역",
                     정답시나리오="표출대상=대상 필지 도형; 분석결과표출=지구단위계획구역 도형")
        ev2 = locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_PLAN") + finish()
        self.assertTrue(run(ev2, gold2)[0]["정상수행"])                # 토지특성 OVERLAP_PLAN 경로는 레이어 ID 가 없어 대조 안 함

    def test_radius_from_resolve_input_on_core_llm_route(self):  # 코어 LLM 이 부른 분석은 args 가 {query,_text} 뿐 — 반경은 resolve.input 에서
        gold = dict(GOLD_OVERLAP, 정답의도="지도 이동; 규제 저촉 분석; 버퍼 분석; 분석 결과 표출",
                    정답파라미터=f"PNU={PNU}; 분석대상=용도지역·지구; 반경=100m",
                    정답시나리오="표출대상=대상 필지 도형; 분석결과표출=용도지역·지구 중첩 도형, 버퍼 도형")
        q = "반경 100m 버퍼를 만들어 보여줘"
        buf = [("call.started", {"call_id": "c4", "callee": "spatial_regulation", "tool": "ask", "args": {"query": q, "_text": q}}),
               ("call.finished", {"call_id": "c4", "callee": "spatial_regulation", "tool": "ask", "status": "ok",
                                  "payload": {"status": "ok", "resolve": {"status": "ok", "pnu": PNU, "geom": {"result_geojson": POLY},
                                                                          "input": {"operation": "BUFFER", "pnu": PNU, "distance": 100.0}}}})]
        v, ext = run(locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + buf + finish(), gold)
        self.assertEqual(ext["radius"], [100.0])
        self.assertTrue(v["정상수행"], v["불일치"])

    def test_criteria_fingerprint_tracks_options(self):
        a = E.criteria_fingerprint()
        E.ACTION_ALIAS["setzoom"] = "ZOOM_TO"                    # ③ 대조에 쓰는 지도 동작 이름 대응도 지문에 들어감
        try:
            self.assertNotEqual(a, E.criteria_fingerprint())
        finally:
            E.ACTION_ALIAS["setzoom"] = "SET_ZOOM"
        for name in ("REQUIRE_TARGET_GEOMETRY", "SYSTEM_FAIL_RULE", "UNLOCATED_RULE"):
            setattr(E, name, not getattr(E, name))
            try:
                self.assertNotEqual(a, E.criteria_fingerprint(), name)
            finally:
                setattr(E, name, not getattr(E, name))
        self.assertEqual(a, E.criteria_fingerprint())


class ReadRows(unittest.TestCase):
    def test_cp949_csv(self):                                    # 엑셀 'CSV (쉼표로 분리)' 저장(CP949)도 읽음 — 화면이 멈추지 않게
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "testset_ai6.csv")
            with open(p, "w", encoding="cp949", newline="") as f:
                f.write("사례ID,명령유형,명령문\r\nAI6-0001,공간규제 분석,경산시 신천동 31-1 용도지역 보여줘\r\n")
            rows = E.read_rows(p)
            self.assertEqual(rows, [{"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "명령문": "경산시 신천동 31-1 용도지역 보여줘"}])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


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

    def test_missing_or_blank_gold_is_not_system_error(self):  # 정답 행이 없거나 빈 칸 = 데이터 문제 — 점검에서 막고, 판정은 시스템 탓으로 안 셈
        ev = locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
        for gold in ({}, dict(GOLD_OVERLAP, 정답의도="")):
            v, _ = run(ev, gold)
            self.assertFalse(v["정상수행"])
            self.assertNotIn(v["오류원인"], E.CAUSES, v["불일치"])
        ts = [{"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "명령문": "x"}]
        for col in ("정답의도", "정답파라미터", "정답시나리오"):
            self.assertTrue(any(f"{col} 없음" in p for p in E.blocking(E.validate(ts, [dict(GOLD_OVERLAP, **{col: ""})]))), col)


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

    def test_blank_id_row_not_in_denominator(self):             # 시험셋 CSV 끝의 빈 줄(',,') — 러너가 보내지 않으니 분모에도 넣지 않음
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig", newline="") as f:
            f.write("사례ID,명령유형,명령문\nAI6-0001,공간규제 분석,x\n,,\n")
        self.addCleanup(os.remove, f.name)
        ts = E.read_rows(f.name)
        s = E.score([{"사례ID": "AI6-0001", "판정": "정상 수행", "오류원인": "", "불일치내용": []}], ts)
        self.assertEqual((s["n"], s["ok"], s["value"], s["응답오류"]), (1, 1, 100.0, 0))
        self.assertNotIn("", {r["명령 유형"] for r in s["유형별"]})

    def test_validate(self):
        ts = [{"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "명령문": "x"}]
        ans = [dict(GOLD_OVERLAP, 정답의도="지도 이동; 엉뚱한 의도")]
        probs = E.validate(ts, ans)
        self.assertTrue(any("엉뚱한 의도" in p for p in probs))
        self.assertTrue(any("15건" in p for p in probs))


SPEC_OUT = {"의도": ["지도 이동", "규제 저촉 분석", "분석 결과 표출"],
            "핵심파라미터": {"위치": "경산시 신천동 31-1", "PNU": [PNU], "분석대상": ["용도지역·지구"], "반경(m)": [300.0], "확대레벨": []},
            "실행대상기능": {"명령유형": "공간규제 분석", "Function": ["주소 검색(지오코딩)", "용도지역·지구 중첩 조회", "지도 표출"]},
            "지도상태": {"지도제어명령": ["MOVE_TO", "SELECT"], "표출대상": [f"필지 {PNU}"],
                       "분석결과표출": ["OVERLAP_GEOMETRY result_geojson×5"]}}


class SpecCells(unittest.TestCase):                                # 규격서 2-6 '출력' 4항목을 실행 중 표·사례별 판정 표에 한 줄 글로
    def test_cells(self):
        c = E.spec_cells(SPEC_OUT)
        self.assertEqual(list(c), list(E.SPEC_CELLS))
        self.assertEqual(c["해석 의도"], "지도 이동, 규제 저촉 분석, 분석 결과 표출")
        self.assertEqual(c["핵심 파라미터"], f"위치=경산시 신천동 31-1; PNU={PNU}; 분석대상=용도지역·지구; 반경=300m")   # 빈 확대레벨은 뺌
        self.assertEqual(c["실행 대상 기능"], "[공간규제 분석] 주소 검색(지오코딩) → 용도지역·지구 중첩 조회 → 지도 표출")
        self.assertEqual(c["지도 상태"], f"명령: MOVE_TO, SELECT / 표출대상: 필지 {PNU} / 분석결과표출: OVERLAP_GEOMETRY result_geojson×5")

    def test_empty(self):                                          # 응답 오류(규격 출력 없음)·로그 없음 → 빈칸
        self.assertEqual(set(E.spec_cells(None).values()), {""})
        self.assertEqual(set(E.spec_cells({}).values()), {""})

    def test_score_case_table_has_spec_cells(self):
        ts = [{"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "명령문": "x"},
              {"사례ID": "AI6-0002", "명령유형": "공간규제 분석", "명령문": "y"}]
        logs = [{"사례ID": "AI6-0001", "판정": "정상 수행", "오류원인": "", "불일치내용": []}]
        cases = E.score(logs, ts, parse_rows=[{"사례ID": "AI6-0001", "규격출력": SPEC_OUT}])["사례별"]
        self.assertEqual(cases[0]["해석 의도"], "지도 이동, 규제 저촉 분석, 분석 결과 표출")
        self.assertEqual(cases[1]["해석 의도"], "")                     # 미실행 사례는 빈칸
        cols = list(cases[0])
        self.assertEqual(cols[cols.index("명령문") + 1: cols.index("명령문") + 5], list(E.SPEC_CELLS))   # 명령문 바로 뒤
        self.assertEqual(list(cases[1]), cols)                          # 실행·미실행 사례의 칸이 같음
        self.assertEqual(E.score(logs, ts)["사례별"][0]["해석 의도"], "")    # 해석 로그를 안 주면 빈칸(예전 호출 그대로 됨)

    def test_progress_row(self):                                   # 실행 중 화면 표 한 줄
        import spec_runner_ai6 as R
        gr = {"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "명령문": "x", "①의도": True, "②파라미터": False,
              "③지도제어": None, "판정": "오류", "오류원인": "핵심 파라미터 추출 오류", "불일치내용": ["② 반경 다름", "② 위치 다름"],
              "소요초": 12.3}
        row = R.progress_row(gr, {"규격출력": SPEC_OUT})
        self.assertEqual((row["①"], row["②"], row["③"]), ("O", "X", "-"))
        self.assertEqual((row["판정"], row["오류원인"], row["불일치"], row["소요초"]),
                         ("오류", "핵심 파라미터 추출 오류", "② 반경 다름 | ② 위치 다름", 12.3))
        self.assertEqual(row["질의"], "x")
        self.assertEqual(row["해석 의도"], "지도 이동, 규제 저촉 분석, 분석 결과 표출")
        self.assertEqual(R.progress_row(gr)["지도 상태"], "")            # 해석 로그 행이 없으면 빈칸

    def test_default_deadline_180(self):                           # 규격서 2-6 시험조건 '사례별 응답 대기 상한(180초)'
        import importlib
        old = os.environ.pop("AI6_DEADLINE", None)
        try:
            self.assertEqual(importlib.reload(C).DEADLINE, 180.0)
        finally:
            if old is not None:
                os.environ["AI6_DEADLINE"] = old
            importlib.reload(C)


class ConnFailStop(unittest.TestCase):                             # 연결 실패 3회 연속으로 멈춤 → 이어서 실행 안 내고, 공식 아님
    def test_stop_is_not_resumable_and_not_official(self):
        import csv
        import shutil
        import tempfile
        import unittest.mock as um
        import spec_runner_ai6 as R6
        tmp, cwd = tempfile.mkdtemp(), os.getcwd()
        try:
            os.chdir(tmp)
            ts, ans = "testset_ai6.csv", "answer_ai6.csv"
            for name, rows in ((ts, [{"사례ID": f"AI6-{i:04d}", "명령유형": "공간규제 분석", "명령문": f"명령 {i}"} for i in range(1, 6)]),
                               (ans, [dict(GOLD_OVERLAP, 사례ID=f"AI6-{i:04d}") for i in range(1, 6)])):
                with open(name, "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(rows[0]))
                    w.writeheader()
                    w.writerows(rows)
            base = "http://127.0.0.1:1"
            with open(R6.FREEZE_FILE, "w", encoding="utf-8") as f:                 # 동결 → 원래는 공식 실행
                json.dump(R6.freeze_record(ts, ans, None, 30, base), f)
            res = dict(url=base + "/api/chat", request={}, thread_id="t", http_status=500, events=[], ended_by="http",
                       t_first_event=None, elapsed=0.1, deadline=30, error="HTTP 500")
            ok = [{"항목": "x", "결과": "정상", "내용": ""}]
            argv = ["spec_runner_ai6.py", "--testset", ts, "--answer", ans, "--base", base, "--deadline", "30", "--status-file", "st.json"]
            with um.patch.object(R6.C, "precheck", return_value=ok), um.patch.object(R6.C, "ready_info", return_value={}), \
                    um.patch.object(R6.C, "llm_info", return_value={}), um.patch.object(R6.C, "stream_chat", side_effect=lambda *a, **k: dict(res)), \
                    um.patch.object(R6.E, "validate", return_value=[]), um.patch.object(R6, "GAP", 0), um.patch.object(sys, "argv", argv):
                R6.main()
            with open("st.json", encoding="utf-8") as f:
                st_ = json.load(f)
            self.assertEqual((st_["state"], st_["done"]), ("error", 3))
            self.assertNotIn("argv", st_)                                          # 화면이 '↻ 이어서'를 내지 않음
            with open(os.path.join(st_["run_dir"], "meta.json"), encoding="utf-8") as f:
                m = json.load(f)
            self.assertFalse(m["공식"])
            self.assertTrue(any("연결 실패" in w for w in m["예비사유"]))
        finally:
            os.chdir(cwd)
            shutil.rmtree(tmp, ignore_errors=True)


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


class StatusBeforePrecheck(unittest.TestCase):                     # 사전 점검이 오래 걸려도 화면이 '실행 중'으로 보도록 점검 전에 상태를 씀
    def test_running_status_during_precheck(self):
        import csv
        import shutil
        import tempfile
        import unittest.mock as um
        import spec_runner_ai6 as R6
        tmp, cwd = tempfile.mkdtemp(), os.getcwd()
        try:
            os.chdir(tmp)
            for name, row in (("testset_ai6.csv", {"사례ID": "AI6-0001", "명령유형": "공간규제 분석", "명령문": "명령 1"}),
                              ("answer_ai6.csv", GOLD_OVERLAP)):
                with open(name, "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(row))
                    w.writeheader()
                    w.writerow(row)
            seen = {}

            def precheck(*a, **k):                                  # 점검하는 동안 화면이 읽는 상태 파일
                with open("st.json", encoding="utf-8") as f:
                    seen.update(json.load(f))
                return [{"항목": "x", "결과": "정상", "내용": ""}]
            argv = ["spec_runner_ai6.py", "--testset", "testset_ai6.csv", "--answer", "answer_ai6.csv", "--cases", "AI6-0001",
                    "--base", "http://127.0.0.1:1", "--deadline", "30", "--status-file", "st.json"]
            with um.patch.object(R6.C, "precheck", side_effect=precheck), um.patch.object(R6.C, "ready_info", return_value={}), \
                    um.patch.object(R6.C, "llm_info", return_value={}), \
                    um.patch.object(R6.C, "stream_chat", return_value=dict(OK_RES, events=[], http_status=200)), \
                    um.patch.object(R6, "GAP", 0), um.patch.object(sys, "argv", argv):
                R6.main()
            self.assertEqual((seen.get("state"), seen.get("pid")), ("running", os.getpid()))
        finally:
            os.chdir(cwd)
            shutil.rmtree(tmp, ignore_errors=True)


class Runner(unittest.TestCase):                                   # 실행기 main(): 상태 파일·이어서 실행·사전 점검 (보내는 곳은 닫힌 포트만)
    BASE = "http://127.0.0.1:9"

    def setUp(self):
        import tempfile
        import spec_runner_ai6 as R6
        self.R6, self.tmp = R6, tempfile.mkdtemp()
        self.sf = os.path.join(self.tmp, "st.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _csv(self, name, rows):
        import csv
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        return p

    def _data(self, n=1, freeze=True):                             # 합성 시험셋·정답셋 n건(AI6-0001…), 기본 대응표, 동결
        ts = self._csv("testset_ai6.csv", [{"사례ID": f"AI6-{i:04d}", "명령유형": "공간규제 분석",
                                            "명령문": "경산시 신천동 31-1 용도지역 보여줘"} for i in range(1, n + 1)])
        ans = self._csv("answer_ai6.csv", [{**GOLD_OVERLAP, "사례ID": f"AI6-{i:04d}"} for i in range(1, n + 1)])
        if freeze:
            with open(os.path.join(self.tmp, self.R6.FREEZE_FILE), "w", encoding="utf-8") as f:
                json.dump(self.R6.freeze_record(ts, ans, None, 5, self.BASE), f)
        return ["--testset", ts, "--answer", ans]

    def _main(self, *args, precheck=()):
        import contextlib
        import io
        import unittest.mock as um
        R6 = self.R6
        pc = precheck if callable(precheck) else (lambda *a, **k: [dict(r) for r in precheck])
        with contextlib.ExitStack() as es:
            for obj, name, val in ((sys, "argv", ["spec_runner_ai6.py", "--status-file", self.sf, "--base", self.BASE,
                                                  "--deadline", "5", *args]),
                                   (R6, "GAP", 0), (R6, "STOP_FILE", os.path.join(self.tmp, "stop.flag")),
                                   (R6.E, "blocking", lambda probs: []), (R6.C, "precheck", pc),
                                   (R6.C, "ready_info", lambda *a, **k: {}), (R6.C, "llm_info", lambda *a, **k: {})):
                es.enter_context(um.patch.object(obj, name, val))
            es.enter_context(contextlib.redirect_stdout(io.StringIO()))
            R6.main()

    def _status(self):
        try:
            with open(self.sf, encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return {}

    def _meta(self, rd):
        with open(os.path.join(rd, "meta.json"), encoding="utf-8") as f:
            return json.load(f)

    def _gis(self, rd, cid):
        with open(os.path.join(rd, E.GIS_LOG), encoding="utf-8") as f:
            return [r for r in map(json.loads, f) if r["사례ID"] == cid][0]

    def _lost_case(self, rd, raw=False):                           # 보낸 기록(attempts)만 있고 로그 줄이 없는 사례 AI6-0001
        os.makedirs(os.path.join(rd, "raw"))
        if raw:
            ev = locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
            with open(os.path.join(rd, "raw", "AI6-0001.json"), "w", encoding="utf-8") as f:
                json.dump({"사례ID": "AI6-0001", "response": dict(OK_RES, events=[{"event": e, "data": d} for e, d in ev])}, f)
        with open(os.path.join(rd, "attempts.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"사례ID": "AI6-0001", "thread_id": "t1", "at": "2026-11-10 12:00:00"}) + "\n")
        with open(os.path.join(rd, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"공식": True, "frozen": True, "예비사유": []}, f)

    def test_bad_testset_writes_error_status(self):                # 시험셋 칸이 틀리면 상태 파일에 오류로 남김(화면이 '시작 중'에 머물지 않게)
        ts = self._csv("testset_ai6.csv", [{"사례ID": "AI6-0001", "명령유형": "지도 이동", "질의": "경산시 신천동 31-1로 이동"}])
        with self.assertRaises(SystemExit):
            self._main("--testset", ts, "--cases", "AI6-0001")
        self.assertEqual(self._status().get("state"), "error")
        self.assertIn("명령문", self._status().get("message", ""))

    def test_conn_fail_stop_not_resumable_or_official(self):       # 연결 실패 3회 연속 → 전체 재수행 대상: '↻ 이어서' 없음 · 예비
        rd = os.path.join(self.tmp, "run")
        self._main(*self._data(4), "--run-dir", rd)
        st, m = self._status(), self._meta(rd)
        self.assertEqual((st["state"], st["done"]), ("error", 3))
        self.assertNotIn("argv", st)
        self.assertFalse(m["공식"])
        self.assertTrue(any("연결 실패" in w for w in m["예비사유"]))

    def test_user_stop_is_preliminary_until_resumed_to_end(self):  # 동결한 전체 실행을 중지 → 예비, '↻ 이어서'로 끝까지 돌리면 공식
        import unittest.mock as um
        rd = os.path.join(self.tmp, "run")
        args = self._data(3)
        ev = locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
        stop = os.path.join(self.tmp, "stop.flag")
        first = []

        def chat(*a, **k):
            if not first:                                            # 첫 사례를 보낸 뒤 중지 버튼
                first.append(1)
                open(stop, "w").close()
            return dict(OK_RES, url=self.BASE + "/api/chat", request={}, http_status=200, t_first_event=0.1, deadline=5,
                        error=None, events=list(ev))
        with um.patch.object(self.R6.C, "stream_chat", side_effect=chat), um.patch.object(self.R6, "STOP_FILE", stop):
            self._main(*args, "--run-dir", rd)
            st, m = self._status(), self._meta(rd)
            self.assertEqual((st["state"], st["done"]), ("stopped", 1))
            self.assertIn("argv", st)                                # '↻ 이어서'를 냄
            self.assertFalse(m["공식"])
            self.assertIn(self.R6.STOP_NOTE, m["예비사유"])
            self._main(*args, "--run-dir", rd, "--resume")
        st, m = self._status(), self._meta(rd)
        self.assertEqual((st["state"], st["done"]), ("done", 3))
        self.assertTrue(m["공식"], m.get("예비사유"))
        self.assertEqual(m["예비사유"], [])
        self.assertNotIn("중지", m)

    def test_resume_with_changed_conditions_stays_preliminary(self):  # 중지 뒤 조건을 바꿔 동결을 다시 걸고 이어 돌려도 예비(처음 실행과 비교)
        import unittest.mock as um
        rd = os.path.join(self.tmp, "run")
        args = self._data(2)
        ev = locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
        stop = os.path.join(self.tmp, "stop.flag")
        first = []

        def chat(*a, **k):
            if not first:
                first.append(1)
                open(stop, "w").close()
            return dict(OK_RES, url=self.BASE + "/api/chat", request={}, http_status=200, t_first_event=0.1, deadline=5,
                        error=None, events=list(ev))
        with um.patch.object(self.R6.C, "stream_chat", side_effect=chat), um.patch.object(self.R6, "STOP_FILE", stop):
            self._main(*args, "--run-dir", rd)
            with open(os.path.join(self.tmp, self.R6.FREEZE_FILE), "w", encoding="utf-8") as f:   # 대기 상한 7초로 다시 동결
                json.dump(self.R6.freeze_record(args[1], args[3], None, 7, self.BASE), f)
            self._main(*args, "--run-dir", rd, "--resume", "--deadline", "7")
        m = self._meta(rd)
        self.assertEqual(self._status()["state"], "done")
        self.assertFalse(m["공식"])
        self.assertTrue(any("처음 실행과 조건이 다름" in w and "응답 대기 상한" in w for w in m["예비사유"]), m["예비사유"])

    def test_user_stop_then_unfreeze_stays_preliminary(self):      # 중지 뒤 동결을 풀고 이어 돌리면 끝까지 돌려도 예비
        import unittest.mock as um
        rd = os.path.join(self.tmp, "run")
        args = self._data(2)
        ev = locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()
        stop = os.path.join(self.tmp, "stop.flag")
        first = []

        def chat(*a, **k):
            if not first:
                first.append(1)
                open(stop, "w").close()
            return dict(OK_RES, url=self.BASE + "/api/chat", request={}, http_status=200, t_first_event=0.1, deadline=5,
                        error=None, events=list(ev))
        with um.patch.object(self.R6.C, "stream_chat", side_effect=chat), um.patch.object(self.R6, "STOP_FILE", stop):
            self._main(*args, "--run-dir", rd)
            os.remove(os.path.join(self.tmp, self.R6.FREEZE_FILE))
            self._main(*args, "--run-dir", rd, "--resume")
        m = self._meta(rd)
        self.assertEqual(self._status()["state"], "done")
        self.assertFalse(m["공식"])
        self.assertTrue(any("동결" in w for w in m["예비사유"]), m["예비사유"])

    def test_status_written_before_precheck(self):                 # 사전 점검이 20초를 넘어도 화면이 '비정상 종료'로 보지 않게 먼저 씀
        seen = {}

        def pc(*a, **k):
            seen.update(self._status())
            return []
        self._main(*self._data(1), "--run-dir", os.path.join(self.tmp, "run"), precheck=pc)
        self.assertEqual((seen.get("state"), seen.get("pid")), ("running", os.getpid()))

    def test_refused_start_leaves_no_folder(self):                 # 사전 점검 실패로 시작 못 한 실행 → 폴더를 남기지 않고 '↻ 이어서'도 안 냄
        rd = os.path.join(self.tmp, "run")
        with self.assertRaises(SystemExit):
            self._main(*self._data(1), "--run-dir", rd, precheck=[{"항목": "공간분석 API", "결과": "실패", "내용": "ConnectionError"}])
        self.assertEqual(self._status().get("state"), "error")
        self.assertNotIn("argv", self._status())
        self.assertFalse(os.path.exists(rd))

    def test_resume_judges_saved_raw(self):                        # 원문은 저장했는데 기록 전에 멈춘 사례 → 저장된 원문으로 판정(응답 오류 아님)
        rd = os.path.join(self.tmp, "run")
        args = self._data(1)
        self._lost_case(rd, raw=True)
        self._main(*args, "--run-dir", rd, "--resume")
        self.assertEqual(self._gis(rd, "AI6-0001")["판정"], "정상 수행")
        m = self._meta(rd)
        self.assertTrue(m["공식"], m.get("예비사유"))
        self.assertNotIn("완료전", m)

    def _chat_ok(self, hook=None):
        ev = locate_events() + analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY") + finish()

        def chat(*a, **k):
            if hook:
                hook()
            return dict(OK_RES, url=self.BASE + "/api/chat", request={}, http_status=200, t_first_event=0.1, deadline=5,
                        error=None, events=list(ev))
        return chat

    def test_official_only_after_finishing(self):              # 공식 조건 실행 — 도는 동안은 예비(끝까지 실행하기 전), 정상으로 끝나야 공식
        import unittest.mock as um
        rd = os.path.join(self.tmp, "run")
        seen = []
        hook = lambda: seen.append(self._meta(rd)) if os.path.exists(os.path.join(rd, "meta.json")) else None
        with um.patch.object(self.R6.C, "stream_chat", side_effect=self._chat_ok(hook)):
            self._main(*self._data(2), "--run-dir", rd)
        self.assertTrue(seen)
        self.assertFalse(seen[0]["공식"])
        self.assertTrue(seen[0]["완료전"])
        self.assertIn(self.R6.RUN_NOTE, seen[0]["예비사유"])
        m = self._meta(rd)
        self.assertTrue(m["공식"], m.get("예비사유"))
        self.assertEqual(m["예비사유"], [])
        self.assertNotIn("완료전", m)

    def test_crash_leaves_preliminary_then_resume_makes_official(self):   # 실행 중 예외 → 예비(비정상 종료) · ↻ 이어서로 끝까지 → 공식
        import unittest.mock as um
        rd = os.path.join(self.tmp, "run")
        args = self._data(3)
        real, calls = self.R6.judge_one, []

        def boom(*a, **k):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("판정 중 예외(시험)")
            return real(*a, **k)
        with um.patch.object(self.R6.C, "stream_chat", side_effect=self._chat_ok()), um.patch.object(self.R6, "judge_one", side_effect=boom):
            with self.assertRaises(RuntimeError):
                self._main(*args, "--run-dir", rd)
        m, st = self._meta(rd), self._status()
        self.assertEqual(st["state"], "error")
        self.assertFalse(m["공식"])
        self.assertTrue(any(str(w).startswith(self.R6.CRASH_NOTE) for w in m["예비사유"]), m["예비사유"])
        with um.patch.object(self.R6.C, "stream_chat", side_effect=self._chat_ok()):
            self._main(*args, "--run-dir", rd, "--resume")         # 원문은 저장됐으므로 그 사례는 원문으로 판정, 나머지는 보냄
        m = self._meta(rd)
        self.assertEqual(self._status()["state"], "done")
        self.assertTrue(m["공식"], m.get("예비사유"))
        self.assertEqual(m["예비사유"], [])

    def test_resume_with_lost_case_is_preliminary_from_start(self):   # 응답을 기다리다 멈춘 사례(원문·기록 없음) → 이어서 돌리는 동안에도 예비
        import unittest.mock as um
        rd = os.path.join(self.tmp, "run")
        args = self._data(2)
        self._lost_case(rd)                                        # AI6-0001: 보낸 기록만 있음 (옛 기록 모양: 공식 True)
        self.assertEqual(self.R6.resume_outlook(rd), ["AI6-0001"])
        seen = []
        with um.patch.object(self.R6.C, "stream_chat", side_effect=self._chat_ok(lambda: seen.append(self._meta(rd)))):
            self._main(*args, "--run-dir", rd, "--resume")
        self.assertTrue(seen)                                      # AI6-0002 를 보내는 동안
        self.assertFalse(seen[0]["공식"])
        self.assertNotIn("완료전", seen[0])                         # 화면이 '공식 조건으로 실행 중'으로 보이지 않게
        m = self._meta(rd)
        self.assertFalse(m["공식"])
        self.assertTrue(any("QA 중단" in w for w in m["예비사유"]), m["예비사유"])
        self.assertFalse(any(w in self.R6.PENDING_NOTES for w in m["예비사유"]), m["예비사유"])
        self.assertEqual(set(m["resume_precheck"][-1]), {"at", "rows", "ready", "llm"})

    def test_resume_outlook_counts_only_unrecorded_sends(self):  # 원문이 있으면 이어서 때 원문으로 판정 — 기록·원문 둘 다 없는 사례만
        rd = os.path.join(self.tmp, "run")
        self._lost_case(rd, raw=True)
        self.assertEqual(self.R6.resume_outlook(rd), [])
        with open(os.path.join(rd, "attempts.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"사례ID": "AI6-0002", "thread_id": "t2"}) + "\n")
        self.assertEqual(self.R6.resume_outlook(rd), ["AI6-0002"])

    def test_rescore_of_stopped_run_drops_resume_notes(self):    # 중지한 폴더를 다시 판정 → '이어서 돌리면 공식' 문구는 빼고 '원본이 끝까지 돌지 않음'
        import unittest.mock as um
        rd = os.path.join(self.tmp, "run")
        args = self._data(3)
        stop = os.path.join(self.tmp, "stop.flag")
        hook = lambda: open(stop, "w").close()
        with um.patch.object(self.R6.C, "stream_chat", side_effect=self._chat_ok(hook)), um.patch.object(self.R6, "STOP_FILE", stop):
            self._main(*args, "--run-dir", rd)
        self.assertIn(self.R6.STOP_NOTE, self._meta(rd)["예비사유"])
        new, _ = self.R6.rescore(rd, args[3], args[1], None)
        m = self._meta(new)
        self.assertFalse(m["공식"])
        self.assertNotIn(self.R6.STOP_NOTE, m["예비사유"])
        self.assertIn(self.R6.INCOMPLETE_NOTE, m["예비사유"])
        self.assertNotIn("완료전", m)

    def test_unfrozen_run_stays_preliminary(self):              # 동결 안 한 실행은 끝까지 돌아도 예비
        import unittest.mock as um
        rd = os.path.join(self.tmp, "run")
        with um.patch.object(self.R6.C, "stream_chat", side_effect=self._chat_ok()):
            self._main(*self._data(1, freeze=False), "--run-dir", rd)
        m = self._meta(rd)
        self.assertFalse(m["공식"])
        self.assertEqual(m["예비사유"], ["동결 전 실행"])

    def test_resume_uses_run_snapshot(self):                       # 이어서 실행은 처음 실행 때 사본(data/)으로 판정 — 그 사이 고친 정답이 섞이지 않게
        import shutil
        rd = os.path.join(self.tmp, "run")
        args = self._data(1, freeze=False)
        self._lost_case(rd)
        os.makedirs(os.path.join(rd, "data"))
        for n in ("testset_ai6.csv", "answer_ai6.csv"):
            shutil.copy2(os.path.join(self.tmp, n), os.path.join(rd, "data"))
        self._csv("answer_ai6.csv", [{**GOLD_OVERLAP, "정답의도": "지도 이동"}])          # 멈춘 뒤 정답을 고침
        self._main(*args, "--run-dir", rd, "--resume", "--no-precheck")
        self.assertEqual(self._gis(rd, "AI6-0001")["정답"]["정답의도"], GOLD_OVERLAP["정답의도"])


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


class _LlmHandler(http.server.BaseHTTPRequestHandler):            # vLLM 흉내 — /v1/models 는 키가 맞아야 목록을 줌, /version 은 키 없이
    model = "gpt-oss-120b"
    version = "0.24.0"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/version":
            self.send_response(200 if _LlmHandler.version else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"version": _LlmHandler.version} if _LlmHandler.version else {"detail": "Not Found"}).encode())
            return
        if self.headers.get("Authorization") != "Bearer good":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Unauthorized"}')
            return
        body = {"object": "list", "data": [{"id": _LlmHandler.model, "root": "openai/" + _LlmHandler.model, "max_model_len": 128000}]}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


class LlmCheck(unittest.TestCase):                                 # 사전 점검: LLM 서버가 띄운 모델 = 시험 조건(gpt-oss-120b)?
    @classmethod
    def setUpClass(cls):
        cls.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _LlmHandler)
        cls.srv.daemon_threads = True
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/v1/models"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        _LlmHandler.model, _LlmHandler.version = "gpt-oss-120b", "0.24.0"

    def test_vllm_version_checked(self):                          # 규격서 동결 버전 'vLLM 0.24.0' — /version 으로 확인, 다르면 실패
        ok, detail, info, _ = C.llm_check(self.url, "good")
        self.assertTrue(ok, detail)
        self.assertEqual(info["vLLM"], "0.24.0")
        self.assertIn("vLLM 0.24.0", detail)
        _LlmHandler.version = "0.11.0"
        ok, detail, _, _ = C.llm_check(self.url, "good")
        self.assertFalse(ok)
        self.assertIn("시험 조건(vLLM 0.24.0)과 다름", detail)
        ok, _, _, _ = C.llm_check(self.url, "")                    # 키가 없어도 버전은 봄
        self.assertFalse(ok)
        _LlmHandler.version = None                                  # /version 이 없으면 '미확인'으로 기록만
        ok, detail, info, _ = C.llm_check(self.url, "good")
        self.assertTrue(ok, detail)
        self.assertEqual(info["vLLM"], "미확인")
        self.assertIn("vLLM 버전 미확인", detail)

    def test_no_key_still_checks_suborc_setting(self):           # 키가 없어도 공간오케 /ready 설정 모델·주소는 시험 조건과 대조
        ok, detail, _, _ = C.llm_check(self.url, "", {"model": "gpt-oss-20b"})
        self.assertFalse(ok)
        self.assertIn("공간오케 설정 모델(gpt-oss-20b)이 시험 조건(gpt-oss-120b)과 다름", detail)
        ok, detail, _, _ = C.llm_check(self.url, "", {"model": "openai/gpt-oss-120b", "base": "http://10.0.0.9:8003/v1"})
        self.assertFalse(ok)
        self.assertIn("다른 LLM 서버", detail)
        ok, detail, _, _ = C.llm_check(self.url, "", {"model": "gpt-oss-120b", "base": self.url.rsplit("/", 1)[0]})
        self.assertTrue(ok, detail)

    def test_key_and_same_model(self):
        ok, detail, info, _ = C.llm_check(self.url, "good", {"model": "gpt-oss-120b", "base": self.url.rsplit("/", 1)[0]})
        self.assertTrue(ok, detail)
        self.assertIn("openai/gpt-oss-120b", detail)
        self.assertIn("시험 조건(gpt-oss-120b)과 같음", detail)
        self.assertEqual(info["확인"], "키로 확인")
        self.assertNotIn("good", json.dumps(info, ensure_ascii=False))          # 키는 기록에 안 남김

    def test_other_model_fails(self):
        _LlmHandler.model = "qwen3-32b"
        ok, detail, _, _ = C.llm_check(self.url, "good")
        self.assertFalse(ok)
        self.assertIn("시험 조건 모델(gpt-oss-120b)이 목록에 없음", detail)

    def test_no_key_only_checks_server(self):
        ok, detail, info, _ = C.llm_check(self.url, "")
        self.assertTrue(ok)
        self.assertIn("확인 못 함", detail)
        self.assertEqual(info["확인"], "키 없음")

    def test_wrong_key_fails(self):
        ok, detail, info, _ = C.llm_check(self.url, "bad")
        self.assertFalse(ok)
        self.assertEqual(info["확인"], "키 오류")

    def test_suborc_uses_other_server_or_model(self):
        ok, detail, _, _ = C.llm_check(self.url, "good", {"model": "gpt-oss-120b", "base": "http://10.0.0.9:8003/v1"})
        self.assertFalse(ok)
        self.assertIn("다른 LLM 서버", detail)
        ok, detail, _, _ = C.llm_check(self.url, "good", {"model": "gpt-oss-20b"})
        self.assertFalse(ok)
        self.assertIn("공간오케 설정 모델(gpt-oss-20b)", detail)

    def test_precheck_row_uses_ready_diag(self):                  # /ready 의 설정 모델과 LLM 서버 목록을 함께 대조
        import unittest.mock as um
        def fake_get(url, timeout=8, headers=None):
            if url.endswith("/ready"):
                return dict(ok=True, status=200, ms=1, body={"ready": True, "vllm_diag": {"model": "gpt-oss-120b"}})
            return dict(ok=True, status=200, ms=1, body={"status": "ok"})
        real = C._get
        with um.patch.object(C, "_get", side_effect=lambda url, timeout=8, headers=None:
                             real(url, timeout, headers) if url == self.url else fake_get(url, timeout, headers)):
            rows = C.precheck("http://127.0.0.1:1", checks={"공간오케 (/health)": "http://127.0.0.1:1/health",
                                                           "공간오케 준비 상태 (/ready)": "http://127.0.0.1:1/ready",
                                                           "LLM (GPT-OSS 120B)": self.url}, llm_key="good")
        llm = next(r for r in rows if r["항목"].startswith("LLM"))
        self.assertEqual(llm["결과"], "정상", llm["내용"])
        self.assertIn("공간오케 설정 모델과 같음", llm["내용"])


class GisVersion(unittest.TestCase):
    def _pre(self, version):
        import unittest.mock as um
        info = {"title": "GIS API", **({"version": version} if version else {})}
        doc = dict(ok=True, status=200, ms=1, body={"openapi": "3.0.1", "info": info})
        with um.patch.object(C, "_get", return_value=doc):
            return C.precheck("http://127.0.0.1:1", checks={"공간분석 API (GIS API Server)": "http://127.0.0.1:1/v3/api-docs"})

    def test_precheck_checks_gis_api_version(self):             # 동결 버전 확인 — 공간분석 API 버전(info.version)이 시험 조건(v0.1)과 다르면 실패
        self.assertEqual((self._pre("v0.1")[0]["결과"], self._pre("v0.1")[0]["내용"]), ("정상", "GIS API 버전 v0.1"))
        self.assertEqual(self._pre("0.1")[0]["결과"], "정상")                    # 앞의 v 는 있어도 없어도 같음
        r = self._pre("1.4.2")[0]
        self.assertEqual(r["결과"], "실패")
        self.assertIn("시험 조건(v0.1)과 다름", r["내용"])
        self.assertEqual((self._pre(None)[0]["결과"], self._pre(None)[0]["내용"]), ("정상", "GIS API 버전 미제공"))   # 못 읽으면 기록만

    def test_version_rows(self):                                 # 규격 시험방법 (2) 동결 버전 확인 표 — 사전 점검·실행 기록 값으로(네트워크 없음)
        pre = [{"항목": "공간오케 준비 상태 (/ready)", "결과": "정상", "내용": "ready=True · 모델=gpt-oss-120b"},
               {"항목": "LLM (GPT-OSS 120B)", "결과": "정상", "내용": "서버 응답 401 · vLLM 0.24.0 — 키가 없어 띄운 모델 목록은 확인 못 함"},
               {"항목": "공간분석 API (GIS API Server)", "결과": "정상", "내용": "공간 분석 API 버전 v0.1"}]
        got = {r["항목"]: r["결과"] for r in C.version_rows(pre)}
        self.assertEqual(got, {"AI 모델": "일치", "추론 서버 (vLLM)": "일치", "지도제어·공간분석 Sub 오케스트레이터": "선언값",
                               "지도제어·공간분석 에이전트": "선언값", "공간분석 API": "일치", "지도 데이터 기준일": "선언값"})
        self.assertEqual([r["항목"] for r in C.version_rows([])], [k for k, _ in C.SPEC_VERSIONS])
        rows = {r["항목"]: r for r in C.version_rows([], {"LLM 모델": "other-model"}, {"vLLM": "0.25.0", "모델": []})}
        self.assertEqual((rows["AI 모델"]["결과"], rows["추론 서버 (vLLM)"]["결과"], rows["공간분석 API"]["결과"]),
                         ("불일치", "불일치", "확인 못 함"))
        bad = [{"항목": "LLM (GPT-OSS 120B)", "결과": "실패", "내용": "서버 응답 401 — LLM 키가 맞지 않아요"}]
        self.assertEqual({r["항목"]: r["결과"] for r in C.version_rows(bad, {"LLM 모델": "gpt-oss-120b"})}["AI 모델"], "확인 못 함")
        served = {r["항목"]: r for r in C.version_rows([], {}, {"모델": ["gpt-oss-120b"], "vLLM": "0.24.0"})}
        self.assertEqual(served["AI 모델"]["결과"], "일치")
        self.assertIn("/v1/models", served["AI 모델"]["확인 방법"])


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
