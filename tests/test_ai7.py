"""[AI 7] 판정기(①②)·실행기 시험 — 2026-11-13 공간오케(115:17006) 실측 응답 원문(tests/fixtures/ai7/*.json)으로 시험

  python -m unittest tests.test_ai7 -v

시험 자료는 실제 응답에서 도형 좌표·WKT 와 receipts 이벤트만 줄인 것입니다(판정에 쓰지 않는 값).
  slope            경산 신천동 31-1 경사도 분석                build_sun_ask      상대리 221 가상건물+일조 — 건축면적·건폐율 되물음
  build_sun_default 상대리 221 가상건물+일조(날짜 없음 → 오늘)  build_sun_date     같은 명령 + 12월 22일 9~15시
  earth            상대리 221 계획고 100m 토공량               terrain / terrain2 상대리 221 지형 생성 / 계단식
  view             상대리 221 조망(높이·방향은 무시됨)          combo              '3D 건물 숨기고 사정동 300-1 경사도' — 숨기기 누락
  lod · lodon      LOD 건물 끄기·켜기                           weather · snow · weatheroff  기상 효과 실황·눈·끄기
  north            카메라 정북 초기화                           move3d             아산 신동 532-6 이동 + 필지 강조
  zoom · zoomin    줌 레벨 18 / 확대 — 3D 에이전트가 거절        height · height2   건물 높이 — WKT 되물음
"""
import csv
import http.server
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ai6_client as C   # noqa: E402
import ai7_eval as E     # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "ai7")
SANGDAE = "4729035036102210000"
SINCHEON = "4729012300100310001"


def events(name):
    with open(os.path.join(FIX, f"{name}.json"), encoding="utf-8") as f:
        return [tuple(x) for x in json.load(f)]


def ext_of(name):
    return E.extract(C.gather(events(name)))


def gold(intent, params="", scen="x=y", pnu=""):
    return {"정답의도": intent, "정답파라미터": params, "정답시나리오": scen, "참조PNU": pnu}


def run(name, g):
    ev = events(name)
    turn = C.gather(ev)
    res = {"ended_by": "turn.finished", "events": ev}
    return E.judge(g, E.extract(turn), res, turn)


BUILD_SUN = gold("가상건물 생성; 일조 시뮬레이션 실행",
                 f"위치=경상북도 경산시 남산면 상대리 221; PNU={SANGDAE}; 건축면적=100; 건폐율=50; 층수=3; 용도=단독주택; "
                 "기준일=12-22; 시간대=09:00~15:00")


class Extract(unittest.TestCase):
    def test_intents_from_3d_commands(self):
        want = {"slope": ["위치 이동", "경사도 분석"], "earth": ["위치 이동", "토공량 산출"],
                "terrain2": ["위치 이동", "지형 생성"], "view": ["위치 이동", "조망 시뮬레이션 실행"],
                "build_sun_date": ["위치 이동", "가상건물 생성", "일조 시뮬레이션 실행"],
                "lod": ["LOD 건물 끄기"], "lodon": ["LOD 건물 켜기"], "weather": ["기상 효과 켜기"],
                "snow": ["기상 효과 켜기"], "weatheroff": ["기상 효과 끄기"], "north": ["카메라 방위 초기화"],
                "move3d": ["위치 이동", "필지 표시"]}
        for name, intents in want.items():
            with self.subTest(name):
                self.assertEqual(ext_of(name)["intents"], intents)

    def test_refused_or_asked_3d_call_is_outside_map(self):
        for name in ("zoom", "zoomin", "height", "height2"):
            with self.subTest(name):
                self.assertIn("?map_3d:ASK", ext_of(name)["intents"])

    def test_workflow_codes_logged_not_judged(self):
        ext = ext_of("north")                                    # 공간오케 분류는 UNKNOWN — 의도는 resetCamera 명령으로 정함
        self.assertEqual(ext["intent_codes"]["classified"], ["UNKNOWN"])
        self.assertEqual(ext["intents"], ["카메라 방위 초기화"])

    def test_aux_calls_ignored(self):
        self.assertNotIn("?land_regulation:SPLIT_FOR_BUILDING", ext_of("build_sun_date")["intents"])
        self.assertNotIn("?spatial_regulation:AREA", ext_of("terrain")["intents"])

    def test_values_and_pnu(self):
        ext = ext_of("build_sun_date")
        self.assertEqual({p for _, p in ext["pnus"]}, {SANGDAE})
        self.assertEqual(ext["values"]["date"][0], "2026-12-22")
        self.assertIn("가상건물 생성 Tool", ext["functions"])
        self.assertIn("일조 시뮬레이션 Tool", ext["functions"])

    def test_spec_output_shape(self):
        spec = ext_of("build_sun_date")["spec_output"]
        self.assertEqual(list(spec), ["의도", "핵심파라미터", "실행대상기능", "3D 상태"])
        cells = E.spec_cells(spec)
        self.assertEqual(list(cells), list(E.SPEC_CELLS))
        self.assertIn("sunvolume", cells["3D 지도 상태"])
        self.assertNotIn("MULTIPOLYGON", json.dumps(spec, ensure_ascii=False))     # 도형 문자열은 로그에 넣지 않음


class Judge(unittest.TestCase):
    def test_sample_build_sun_ok(self):
        v = run("build_sun_date", BUILD_SUN)
        self.assertTrue(v["①"] and v["②"], v["불일치"])
        self.assertIsNone(v["③"])                               # ③ 시험 범위 밖 — ①② 로 정상 수행
        self.assertTrue(v["정상수행"])
        self.assertIn(E.EXEC_SCOPE, v["참고"])

    def test_default_date_is_param_error(self):
        v = run("build_sun_default", BUILD_SUN)                  # 날짜를 안 쓴 명령 → 뷰어 기본값(오늘·4~20시)
        self.assertTrue(v["①"])
        self.assertFalse(v["②"])
        self.assertEqual(v["오류원인"], "핵심 파라미터 추출 오류")
        self.assertTrue(any("기준일 불일치" in m and "시간대 불일치" in m for m in v["불일치"]))

    def test_ask_is_error(self):
        v = run("build_sun_ask", BUILD_SUN)                      # 건축면적·건폐율 되물음 → 3D 명령 없음
        self.assertFalse(v["정상수행"])
        self.assertTrue(v["되묻기"])
        self.assertEqual(v["오류원인"], "의도 해석 오류")          # 규격: ① → ② → ③ 처음 어긋난 것
        self.assertIn("missing_required_value", v["세부원인"])

    def test_aux_location_intent(self):
        g = gold("경사도 분석", f"위치=경상북도 경산시 신천동 31-1; PNU={SINCHEON}")
        self.assertTrue(run("slope", g)["정상수행"])             # 시스템이 붙인 위치 이동은 보조 — 정답에 없어도 됨
        g2 = gold("위치 이동; 경사도 분석", f"PNU={SINCHEON}")
        self.assertTrue(run("slope", g2)["정상수행"])            # 정답에 적어도 됨
        only_move = gold("위치 이동", "PNU=4420011800105320006")
        v = run("move3d", only_move)                             # 정답이 '위치 이동'뿐이면 판정 — 필지 표시가 초과
        self.assertFalse(v["①"])
        self.assertTrue(run("move3d", gold("위치 이동; 필지 표시", "PNU=4420011800105320006"))["정상수행"])
        self.assertTrue(run("move3d", gold("필지 표시", "PNU=4420011800105320006"))["정상수행"])

    def test_highlight_with_analysis_is_aux(self):
        ev = events("slope") + [("actions", {"call_id": "c9", "items": [{"type": "map.HIGHLIGHT", "result": {"pnu": SINCHEON}}]})]
        turn = C.gather(ev)
        ext = E.extract(turn)
        self.assertIn("필지 표시", ext["intents"])                # 예비 실행 0024·0040·0050 처럼 분석 뒤 필지 강조가 붙음
        v = E.judge(gold("경사도 분석", f"PNU={SINCHEON}"), ext, {"ended_by": "turn.finished"}, turn)
        self.assertTrue(v["정상수행"], v["불일치"])

    def test_wrong_pnu(self):
        v = run("slope", gold("경사도 분석", f"PNU={SANGDAE}"))
        self.assertFalse(v["②"])
        self.assertIn("위치 PNU 불일치", v["불일치"][0])

    def test_combo_missing_hide(self):
        v = run("combo", gold("LOD 건물 끄기; 경사도 분석", "PNU=4729010800103000001"))
        self.assertFalse(v["①"])
        self.assertIn("누락 LOD 건물 끄기", v["불일치"][0])

    def test_refused_zoom_is_intent_error(self):
        v = run("zoomin", gold("지도 확대/축소"))
        self.assertFalse(v["①"])
        self.assertEqual(v["오류원인"], "의도 해석 오류")
        self.assertIn("초과 ?map_3d:ASK", v["불일치"][0])

    def test_basic_controls_ok(self):
        for name, intent, params in (("lod", "LOD 건물 끄기", ""), ("lodon", "LOD 건물 켜기", ""),
                                     ("weather", "기상 효과 켜기", ""), ("snow", "기상 효과 켜기", "기상=눈"),
                                     ("weatheroff", "기상 효과 끄기", ""), ("north", "카메라 방위 초기화", "")):
            with self.subTest(name):
                v = run(name, gold(intent, params))
                self.assertTrue(v["정상수행"], v["불일치"])

    def test_weather_kind_mismatch(self):
        v = run("snow", gold("기상 효과 켜기", "기상=비"))
        self.assertFalse(v["②"])

    def test_earthwork_height(self):
        self.assertTrue(run("earth", gold("토공량 산출", f"PNU={SANGDAE}; 계획고=100"))["정상수행"])
        self.assertFalse(run("earth", gold("토공량 산출", f"PNU={SANGDAE}; 계획고=120"))["②"])

    def test_terrain_type(self):
        self.assertTrue(run("terrain2", gold("지형 생성", f"PNU={SANGDAE}; 지형유형=계단형"))["정상수행"])
        v = run("terrain", gold("지형 생성", f"PNU={SANGDAE}; 지형유형=계단형"))
        self.assertFalse(v["②"])
        self.assertIn("지형유형 불일치", v["불일치"][0])

    def test_building_params(self):
        bad = dict(BUILD_SUN, 정답파라미터=BUILD_SUN["정답파라미터"].replace("층수=3", "층수=2"))
        v = run("build_sun_date", bad)
        self.assertFalse(v["②"])
        self.assertIn("층수 불일치", v["불일치"][0])

    def test_response_error(self):
        v = E.judge(BUILD_SUN, ext_of("build_sun_date"), {"ended_by": "deadline", "error": "응답 대기 상한 300초 초과"}, {})
        self.assertEqual(v["오류원인"], "응답 오류")

    def test_no_gold(self):
        self.assertEqual(run("slope", gold(""))["오류원인"], "정답 없음")


class Compare(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(E._md("2026-12-22"), "12-22")
        self.assertEqual(E._md("12월 22일"), "12-22")
        self.assertEqual(E._hours("09:00~15:00"), (9, 15))
        self.assertEqual(E._hours("9시부터 15시"), (9, 15))
        self.assertTrue(E.compare("건폐율", "50%", {"bcr": [50, 50.0]})[0])
        self.assertFalse(E.compare("층수", "3", {})[0])


class Score(unittest.TestCase):
    def test_floor_one_decimal(self):
        self.assertEqual(E.floor_pct(2, 3), 66.6)
        self.assertEqual(E.floor_pct(47, 50), 94.0)

    def test_missing_case_counts_as_error(self):
        rows = [{"사례ID": "AI7-0001", "명령유형": "필지 경사도 분석", "판정": "정상 수행", "오류원인": ""}]
        ts = [{"사례ID": "AI7-0001", "명령유형": "필지 경사도 분석"}, {"사례ID": "AI7-0002", "명령유형": "필지 경사도 분석"}]
        s = E.score(rows, ts)
        self.assertEqual((s["n"], s["ok"], s["value"]), (2, 1, 50.0))
        self.assertEqual(s["사례별"][1]["세부원인"], "미실행")
        self.assertEqual(len(s["유형별"]), 7)


class Data(unittest.TestCase):
    def test_repo_data_has_no_blocking_problem(self):
        ts = E.read_rows(os.path.join(ROOT, "spec_data", "testset_ai7.csv"))
        ans = E.read_rows(os.path.join(ROOT, "spec_data", "answer_ai7.csv"))
        self.assertEqual(len(ts), 50)
        self.assertEqual(E.blocking(E.validate(ts, ans)), [])

    def test_validate_catches(self):
        ts = [{"사례ID": "AI7-0001", "명령유형": "필지 경사도 분석", "명령문": " 앞 공백"}]
        ans = [{"사례ID": "AI7-0001", "정답의도": "없는 의도", "정답파라미터": "모름=1", "정답시나리오": "x=y"}]
        p = " ".join(E.validate(ts, ans))
        for word in ("앞뒤 공백", "의도 대응표에 없는", "대조 규칙이 없는 파라미터 키", "규격 15건"):
            self.assertIn(word, p)


# ---------------------------------------------------------------- 실행기 끝까지 (실측 원문을 돌려주는 가짜 공간오케)
REPLAY = {}                                                      # 명령문 → 시험 자료 이름


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        name = REPLAY.get(body.get("message"), "zoomin")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for ev, data in events(name):
            self.wfile.write(f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()


class Runner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        rows = {r["사례ID"]: r for r in E.read_rows(os.path.join(ROOT, "spec_data", "testset_ai7.csv"))}
        REPLAY.update({rows["AI7-0036"]["명령문"]: "build_sun_date", rows["AI7-0016"]["명령문"]: "slope",
                       rows["AI7-0004"]["명령문"]: "lod", rows["AI7-0013"]["명령문"]: "zoomin"})

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_then_rescore(self):
        import spec_runner_ai7 as R7
        ts = os.path.join(ROOT, "spec_data", "testset_ai7.csv")
        an = os.path.join(ROOT, "spec_data", "answer_ai7.csv")
        argv = sys.argv
        sys.argv = ["spec_runner_ai7.py", "--testset", ts, "--answer", an, "--cases", "AI7-0036,AI7-0016,AI7-0004,AI7-0013",
                    "--base", f"http://127.0.0.1:{self.port}", "--deadline", "20", "--no-precheck", "--run-dir", "run"]
        old_gap = R7.GAP
        R7.GAP = 0
        try:
            R7.main()
        finally:
            sys.argv = argv
            R7.GAP = old_gap
        sim = R7.read_jsonl(os.path.join("run", E.SIM_LOG))
        by = {r["사례ID"]: r for r in sim}
        self.assertEqual(set(by), {"AI7-0036", "AI7-0016", "AI7-0004", "AI7-0013"})
        self.assertEqual(by["AI7-0036"]["판정"], "정상 수행")
        self.assertEqual(by["AI7-0016"]["판정"], "정상 수행")
        self.assertEqual(by["AI7-0004"]["판정"], "정상 수행")
        self.assertEqual(by["AI7-0013"]["오류원인"], "의도 해석 오류")
        self.assertTrue(os.path.exists(os.path.join("run", E.PARSE_LOG)))
        with open(os.path.join("run", "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertFalse(meta["공식"])                            # 일부 ID 실행·동결 전 → 예비 (③ 때문이 아님)
        self.assertIn("일부 ID만 실행", meta["예비사유"])
        self.assertFalse(any("③" in w for w in meta["예비사유"]))
        with open(os.path.join("run", "summary.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["value"], 75.0)
        new, s = R7.rescore("run", None, None, None)              # 원문으로 다시 판정 — 새 폴더, 같은 결과
        self.assertTrue(os.path.basename(new).startswith("run_재판정_"))
        self.assertEqual((s["n"], s["ok"]), (4, 3))


if __name__ == "__main__":
    unittest.main()
