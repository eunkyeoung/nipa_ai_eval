"""[AI 6] 결과 화면 시험 — 오류 사례(오류 원인·사유) · 결과 파일 다시 저장. streamlit·pandas 가 있을 때만 돎(서버 컨테이너·개발 venv)

  python -m unittest tests.test_ai6_screen -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

try:
    import pandas as pd
    from streamlit.testing.v1 import AppTest
except ImportError:                                                # pandas·streamlit 없는 PC 에서는 건너뜀
    AppTest = None

import ai6_eval as E          # noqa: E402
import test_ai6 as T         # noqa: E402

RUN = "20990101_000000"


def _app(work, root):
    import os
    import sys
    import streamlit as st
    sys.path.insert(0, root)
    os.chdir(work)
    import spec_page
    spec_page.render("AI6", lambda k: st.container(key=k))


def _busy_app(work, root, run):
    import os
    import sys
    import streamlit as st
    sys.path.insert(0, root)
    os.chdir(work)
    import spec_page
    spec_page.render("AI6", lambda k: st.container(key=k), live=os.path.relpath(run, work))


def _row(case_id, events, gold, res=T.OK_RES):
    """가짜 공간오케 이벤트 → 실제 러너와 같은 해석 로그 행·실행 결과 로그 행"""
    turn = T.C.gather(events)
    ext = E.extract(turn)
    v = E.judge(gold, ext, res, turn)
    case = {"사례ID": case_id, "명령유형": gold.get("명령유형") or "공간규제 분석", "명령문": f"{case_id} 명령문"}
    res = dict(res, thread_id="t1", elapsed=1.0)
    return E.parse_log_row(case, res, ext), E.gis_log_row(case, gold, res, ext, v)


@unittest.skipIf(AppTest is None, "pandas·streamlit 없음")
class ResultScreen(unittest.TestCase):
    def setUp(self):                                               # 화면 스크립트가 작업 폴더를 옮김(같은 프로세스) — 끝나면 되돌림
        self.addCleanup(os.chdir, os.getcwd())

    def _folder(self, rows):
        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        run = os.path.join(work, "spec", "AI6", RUN)
        os.makedirs(os.path.join(run, "data"))
        with open(os.path.join(run, "data", "testset_ai6.csv"), "w", encoding="utf-8-sig") as f:
            f.write("사례ID,명령유형,명령문\n" + "".join(f"{g['사례ID']},공간규제 분석,{g['명령문']}\n" for _, g in rows))
        for name, k in ((E.PARSE_LOG, 0), (E.GIS_LOG, 1)):
            with open(os.path.join(run, name), "w", encoding="utf-8") as f:
                f.write("".join(json.dumps(r[k], ensure_ascii=False, default=str) + "\n" for r in rows))
        meta = dict(item="AI6", testset="spec_data/testset_ai6.csv", 공식=True, 예비사유=[], frozen=True,
                    started="2099-01-01 00:00:00", cases=[])
        with open(os.path.join(run, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        return work, run

    def _run(self, work):
        at = AppTest.from_function(_app, args=(work, ROOT), default_timeout=120).run()
        self.assertFalse(at.exception, [str(e.value)[:300] for e in at.exception])
        return at

    def _errors(self):
        other = "4729012300100310002"
        return [_row("AI6-0001", T.locate_events() + T.analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY")
                     + T.finish(), T.GOLD_OVERLAP),
                _row("AI6-0002", T.locate_events() + T.finish(), T.GOLD_OVERLAP),
                _row("AI6-0003", T.locate_events(pnu=other) + T.analysis_events("land_attr_b", "parcelInfo", "action",
                                                                                 "OVERLAP_GEOMETRY", pnu=other) + T.finish(), T.GOLD_OVERLAP),
                _row("AI6-0004", T.locate_events(), T.GOLD_OVERLAP, {"ended_by": "deadline", "error": "응답 대기 상한 180초 초과"})]

    def test_error_cases_and_resave(self):
        work, run = self._folder(self._errors())
        at = self._run(work)
        heads = [m.value for m in at.markdown]
        self.assertIn("#### 오류 사례 (3건) — 시험 대상(AIPS)이 정답과 다르게 수행한 명령", heads)
        err = next(d.value for d in at.dataframe if "사유" in d.value.columns and "오류 주체" in d.value.columns
                   and len(d.value) == 3)
        self.assertEqual(list(err["사례ID"]), ["AI6-0002", "AI6-0003", "AI6-0004"])
        self.assertEqual(list(err.columns), ["사례ID", "명령 유형", "명령문", "오류 원인", "사유", "오류 주체"])
        self.assertEqual(set(err["오류 주체"]), {E.WHO_AIPS})
        self.assertEqual(err.iloc[0]["사유"], "AIPS가 '규제 저촉 분석' 의도를 잡지 못하고 대상 필지 표시(지도 이동)만 함")
        self.assertTrue(err.iloc[1]["사유"].startswith("AIPS가 다른 필지(PNU 4729012300100310002)를 대상으로 잡음"))
        self.assertEqual(err.iloc[2]["사유"], "AIPS가 응답 대기 상한(180초) 안에 답하지 않음")
        self.assertTrue(any("시험 도구의 오류가 아니에요" in c.value for c in at.caption))
        cases = next(d.value for d in at.dataframe if "①의도" in d.value.columns)     # 질의별 결과: 대조 내역 칸 이름 · 사유 칸
        self.assertIn("대조 내역 (정답 ↔ AIPS)", cases.columns)
        self.assertNotIn("불일치내용", cases.columns)
        self.assertIn("사유", cases.columns)
        help_txt = next(m.help for m in at.metric if m.label == "오류 건수")
        self.assertIn("오류 주체(AIPS·연결 실패·시험 데이터·시험 도구)", help_txt)
        # 결과 파일 다시 저장 — AI6 에도 있고, 판정 그대로 result.xlsx(오류 사례 시트) · summary.json 을 만듦
        btn = at.button(key="resave_ai6")
        self.assertEqual(btn.label, "💾 결과 파일 다시 저장")
        self.assertFalse(btn.disabled)
        self.assertFalse(os.path.exists(os.path.join(run, "result.xlsx")))
        at = btn.click().run()
        self.assertFalse(at.exception, [str(e.value)[:300] for e in at.exception])
        sheets = pd.read_excel(os.path.join(run, "result.xlsx"), sheet_name=None)
        self.assertEqual(list(sheets), ["총괄 결과", "명령 유형 3종별 세부 결과", "오류 원인 유형별 건수", "오류 사례", "사례별 판정"])
        self.assertEqual(list(sheets["오류 사례"]["사례ID"]), ["AI6-0002", "AI6-0003", "AI6-0004"])
        self.assertEqual(list(sheets["오류 사례"]["사유"]), list(err["사유"]))
        self.assertIn("사유", sheets["사례별 판정"].columns)
        with open(os.path.join(run, "summary.json"), encoding="utf-8") as f:
            sm = json.load(f)
        self.assertEqual((sm["value"], sm["n"], sm["ok"]), (25.0, 4, 1))
        with open(os.path.join(run, "meta.json"), encoding="utf-8") as f:
            self.assertTrue(json.load(f)["공식"])                       # 다시 저장은 공식 여부를 건드리지 않음

    def test_busy_and_other_owners(self):                          # 실행 중: 아직 끝나지 않은 명령은 오류로 안 보임 · AIPS 탓이 아닌 오류는 따로
        rows = self._errors()[:2]
        rows.append(_row("AI6-0003", T.locate_events(), T.GOLD_OVERLAP,
                         {"ended_by": "exception", "error": E.LOST_NOTE}))           # 시험 도구 중단
        work, run = self._folder(rows)
        with open(os.path.join(run, "data", "testset_ai6.csv"), "a", encoding="utf-8") as f:
            f.write("AI6-0009,공간규제 분석,아직 안 보낸 명령\n")                     # 시험셋에만 있는 사례 = 미실행
        at = self._run(work)
        heads = [m.value for m in at.markdown]
        self.assertIn("#### 오류 사례 (1건) — 시험 대상(AIPS)이 정답과 다르게 수행한 명령", heads)
        self.assertIn("**그 밖에 오류로 센 사례 (2건)** — AIPS 응답 결과가 아님", heads)
        other = next(d.value for d in at.dataframe if "오류 주체" in d.value.columns and "①의도" not in d.value.columns
                     and set(d.value["오류 주체"]) != {E.WHO_AIPS})
        self.assertEqual(dict(zip(other["사례ID"], other["오류 주체"])), {"AI6-0003": E.WHO_TOOL, "AI6-0009": E.WHO_NOT_RUN})
        import spec_page
        live = AppTest.from_function(_busy_app, args=(work, ROOT, run), default_timeout=120).run()   # 같은 폴더를 실행 중으로
        self.assertFalse(live.exception, [str(e.value)[:300] for e in live.exception])
        self.assertTrue(any("아직 끝나지 않은 명령 1건" in c.value for c in live.caption), [c.value for c in live.caption])
        busy_other = [d.value for d in live.dataframe if "오류 주체" in d.value.columns and "①의도" not in d.value.columns
                      and E.WHO_NOT_RUN in set(d.value["오류 주체"])]
        self.assertEqual(busy_other, [])                                              # 미실행(대기 중) 행은 표에 없음
        self.assertEqual(spec_page.ai6_stop_kind({"state": "error", "stop_kind": "system"}), "system")
        self.assertEqual(spec_page.ai6_stop_kind({"state": "error", "message": "연결 실패 3회 연속 (3/50건) — …"}), "environment")
        self.assertEqual(spec_page.ai6_stop_kind({"state": "error", "message": "Traceback …"}), "")
        self.assertEqual(spec_page.ai6_stop_kind({"state": "done", "stop_kind": "system"}), "")

    def test_no_error_keeps_section(self):                         # 100 % 여도 오류 사례 칸은 남고 '오류 없음'
        ok = [_row(cid, T.locate_events() + T.analysis_events("land_attr_b", "parcelInfo", "action", "OVERLAP_GEOMETRY")
                   + T.finish(), T.GOLD_OVERLAP) for cid in ("AI6-0001", "AI6-0005")]
        work, _ = self._folder(ok)
        at = self._run(work)
        self.assertIn("#### 오류 사례 (0건) — 시험 대상(AIPS)이 정답과 다르게 수행한 명령", [m.value for m in at.markdown])
        self.assertTrue(any(s.value.startswith("오류 없음 — 2건 모두 정상 수행") for s in at.success), [s.value for s in at.success])


if __name__ == "__main__":
    unittest.main()
