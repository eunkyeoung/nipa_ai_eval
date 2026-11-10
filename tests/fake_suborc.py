"""가짜 공간오케(17006) — 실제 서버 없이 AI6 실행기·화면을 끝까지 돌려 보는 용도 (시험 결과가 아님)

  python tests/fake_suborc.py --answer spec_data/answer_ai6.csv --port 17906 [--mode mixed]
  python spec_runner_ai6.py --base http://127.0.0.1:17906 --no-precheck --testset ... --answer ... --deadline 5

정답셋의 정답 의도·파라미터를 보고 공간오케 응답 모양(call.started · call.finished · actions · workflow · turn.finished)을
흉내 냅니다. 모양은 공간오케 plugin/coreplug/jsonapi.py · workflow/mapactions.py 기준입니다.
--mode mixed 면 일부 사례에 도형 없음(no_data) · 되묻기 · 대기 상한 초과 · 남는 호출을 섞습니다.
"""
import argparse
import http.server
import json
import os
import socketserver
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai6_eval as E  # noqa: E402

POLY = {"type": "Polygon", "coordinates": [[[196100.0, 532700.0], [196200.0, 532700.0], [196200.0, 532800.0], [196100.0, 532700.0]]],
        "crs": {"type": "name", "properties": {"name": "EPSG:5186"}}}
LINE = {"type": "MultiLineString", "coordinates": [[[196100.0, 532700.0], [196150.0, 532700.0]]],
        "crs": {"type": "name", "properties": {"name": "EPSG:5186"}}}
GOLD = {}
MODE = "ok"


def plan(tool, **args):
    return {"_metadata": {"mode": "workflow", "plan": [{"tool": tool, "args": args}]}}


def call(cid, callee, tool, args, resolve, actions=None, status="ok"):
    pay = {"status": "ok", "resolve": resolve}
    if actions is not None:
        pay["actions"] = actions
    ev = [("call.started", {"call_id": cid, "callee": callee, "tool": tool, "args": plan(tool, **args)}),
          ("call.finished", {"call_id": cid, "callee": callee, "tool": tool, "status": status, "reason_code": None,
                             "elapsed_ms": 700, "payload": pay})]
    if actions:
        ev.append(("actions", {"call_id": cid, "items": actions}))
    return ev


def events_for(message):
    g = next((v for k, v in GOLD.items() if v.get("_cmd") == message), None)
    if g is None:
        return [("turn.finished", {"status": "delivered", "answer": "모르는 명령", "stop_reason": "model_completed"})], 0
    n = int(g["사례ID"][-4:])
    pnu = E.pnu_of(g.get("참조PNU"))
    params = E.parse_kv(g.get("정답파라미터"))
    intents = E.split_list(g.get("정답의도"))
    ev = [("turn.started", {"thread_id": "x"})]
    ev += call("c1", "map_navigation_b", "locationSearch", {"action": "INTEGRATED_LOCATION_SEARCH", "query": params.get("위치"), "size": "20"},
               {"status": "ok", "result": {"results": [{"pnu": pnu}]}},
               [{"type": "map.MOVE_TO", "result": {"lng": 196150.0, "lat": 532750.0, "crs": "EPSG:5186"}},
                {"type": "map.ADD_MARKER", "result": {"lng": 196150.0, "lat": 532750.0}}])
    if MODE == "mixed" and n % 11 == 0:                    # 되묻기 — 위치 후보 여럿
        ev.append(("ask", {"key": "user:" + uuid.uuid4().hex[:12], "kind": "user", "options": ["후보 1", "후보 2"],
                           "question": f"'{params.get('위치')}'에 해당하는 곳이 2곳입니다 (1–2번째). 어느 곳인가요?",
                           "reason": "ambiguous_candidates", "call_id": ""}))
        ev.append(("turn.finished", {"status": "asking", "answer": "", "stop_reason": ""}))
        return ev, 0
    sel = {"type": "map.SELECT", "result": {"pnu": pnu, "featureRef": pnu, "crs": "EPSG:5186", "lng": 196150.0, "lat": 532750.0, "geometry": POLY}}
    ev += call("c2", "map_navigation_b", "mapFeature", {"action": "SELECT", "featureRef": pnu},
               {"status": "ok", "result": {"pnu": pnu}}, [{"type": "ui.focus", "target": "map"}, sel])
    k = 3
    targets = [t.strip() for t in params.get("분석대상", "").split(",") if t.strip()]
    dist = E.meters(params.get("반경"))
    no_data = MODE == "mixed" and n % 7 == 0
    geo = (lambda key, shape: {"status": "no_data", "pnu": pnu, "geom": {}} if no_data else
           {"status": "ok", "pnu": pnu, "geom": {key: [shape]}})
    for intent in intents:
        cid = f"c{k}"
        k += 1
        if intent == "지도 확대/축소":
            z = params.get("확대레벨", "17")
            ev += call(cid, "map_navigation_b", "mapControl", {"action": "SET_ZOOM", "zoomLevel": z}, {"status": "ok"},
                       [{"type": "map.SET_ZOOM", "result": {"zoomLevel": int(z)}}])
        elif intent == "규제 저촉 분석":
            if "용도지역·지구" in targets:
                ev += call(cid, "land_attr_b", "parcelInfo", {"pnu": pnu, "action": "OVERLAP_GEOMETRY"}, geo("result_geojson", POLY))
            elif "지구단위계획구역" in targets:
                ev += call(cid, "land_attr_b", "parcelInfo", {"pnu": pnu, "action": "OVERLAP_PLAN"}, geo("result_geojson", POLY))
            else:
                layer = targets[0] if targets else "개발제한구역"
                ev += call(cid, "spatial_regulation", "tool.spatial.analysis", {"operation": "LAYER_FIND", "pnu": pnu, "lyr_query": layer},
                           {"status": "ok", "result": {"lyr_id": "lsmd_cont_ud801"}})
                ev += call(cid + "b", "spatial_regulation", "tool.spatial.analysis",
                           {"operation": "INTERSECTIONLAYER", "pnu": pnu, "lyr_id": "lsmd_cont_ud801"}, geo("result_geojson", POLY))
        elif intent == "주변 규제 분석":
            layer = targets[0] if targets else "개발제한구역"
            ev += call(cid, "spatial_regulation", "tool.spatial.analysis", {"operation": "LAYER_FIND", "pnu": pnu, "lyr_query": layer},
                       {"status": "ok", "result": {"lyr_id": "lsmd_cont_ud801"}})
            ev += call(cid + "b", "spatial_regulation", "tool.spatial.analysis",
                       {"operation": "LAYER_NEARBY", "pnu": pnu, "lyr_id": "lsmd_cont_ud801", "distance": f"{dist:g}"}, geo("result_geojson", POLY))
        elif intent == "버퍼 분석":
            ev += call(cid, "spatial_regulation", "tool.spatial.analysis", {"operation": "BUFFER", "pnu": pnu, "distance": f"{dist:g}"},
                       {"status": "ok", "pnu": pnu, "geom": {"result_geojson": [POLY]}})
        elif intent == "도로 접근성 분석":
            ev += call(cid, "road_access", "tool.road.contact", {"operation": "CONTACT_SUMMARY", "pnu": pnu}, geo("road_contact_geojson", LINE))
        elif intent == "규제도로 분석":
            ev += call(cid, "road_access", "tool.road.contact", {"operation": "REGULATION", "pnu": pnu}, {"status": "ok", "pnu": pnu})
        elif intent == "산지전용 검토":
            ev += call(cid, "land_regulation", "tool.land.regulation", {"operation": "FOREST_CONVERSION", "pnu": pnu}, geo("result_geojson", POLY))
        elif intent == "개발가능지 추출":
            ev += call(cid, "land_regulation", "tool.land.regulation", {"operation": "DEVELOPABLE_AREA", "pnu": pnu}, geo("result_geojson", POLY))
    if MODE == "mixed" and n % 13 == 0:                    # 계획에 없던 분석이 하나 더 붙음('면적' → MEASURE_AREA 가 샌 경우 흉내)
        ev += call(f"c{k}", "spatial_regulation", "tool.spatial.analysis", {"operation": "AREA", "pnu": pnu},
                   {"status": "ok", "pnu": pnu, "result": {"area_m2": 1234.5}})
    ans = "요청하신 분석 결과를 지도에 표시했습니다."
    ev += [("answer.delta", {"delta": ans}),
           ("workflow", {"skipped": False, "skip_reason": None, "reused_pnu": None, "intents": {"classified": [], "called": []}}),
           ("turn.finished", {"status": "delivered", "answer": ans, "stop_reason": "model_completed",
                              "usage": {"timing": {"total_ms": 8000}}})]
    slow = 30 if (MODE == "mixed" and n == 17) else 0
    return ev, slow


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = {"/health": {"status": "ok"}, "/ready": {"ready": True, "vllm_diag": {"model": "openai/gpt-oss-120b"}},
                "/v1/models": {"data": [{"id": "openai/gpt-oss-120b"}]}, "/v3/api-docs": {"openapi": "3.0.1"}}.get(self.path, {})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        if self.path.endswith("/cancel"):
            self.send_response(200)
            self.end_headers()
            return
        events, slow = events_for(req.get("message", ""))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for i, (name, data) in enumerate(events):
                if name == "turn.finished":
                    data = {**data, "thread_id": req.get("thread_id")}
                    if slow:
                        time.sleep(slow)
                self.wfile.write(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
        except OSError:
            pass


def main():
    global MODE
    ap = argparse.ArgumentParser()
    ap.add_argument("--answer", required=True)
    ap.add_argument("--testset", required=True)
    ap.add_argument("--port", type=int, default=17906)
    ap.add_argument("--mode", default="ok", choices=["ok", "mixed"])
    a = ap.parse_args()
    MODE = a.mode
    cmds = {r["사례ID"]: r["명령문"] for r in E.read_rows(a.testset)}
    for r in E.read_rows(a.answer):
        GOLD[r["사례ID"]] = {**r, "_cmd": cmds.get(r["사례ID"])}
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", a.port), H)
    srv.daemon_threads = True
    print(f"fake 17006 on http://127.0.0.1:{a.port} ({MODE}, {len(GOLD)} cases)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
