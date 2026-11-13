# 통합 브랜치 반영(merge·배포) 확인 — 다른 시험항목과 섞였는지 (Claude가 PC에서 실행해 대화창에 보고, QA 화면과 무관)
#   .venv/Scripts/python tools/check_merge.py [기준커밋] [대상커밋]
#   기준 생략: 서버 배포 기록의 직전 배포 전 커밋(없으면 대상~1), 대상 생략: origin/feature/spec-test-ai-da
# 확인: ① 바뀐 파일 ② 다른 항목 시험 데이터 ③ 공통 코드 ④ AI1~AI5 공식 결과 폴더를 기준·대상 코드로 채점해 비교 ⑤ 전 항목 화면(AppTest)
# 운영환경_설치가이드 3-6 절차를 자동으로 한 것
import os, subprocess, sys, tempfile, json, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
BR = "origin/feature/spec-test-ai-da"
ITEMS = ["AI1", "AI2", "AI3", "AI4", "AI5"]
COMMON = {"spec_page.py", "spec_eval.py", "qa_app.py", "qa_runner.py", "spec_runner.py", "qa_eval.py"}

SCORE = r'''
import sys, json, hashlib, os, re
sys.path.insert(0, sys.argv[1]); os.chdir(sys.argv[2])
import spec_page as P, pandas as pd
out = {}
for it in %s:
    rd = P.runs(it)[0]
    r, _, _ = P.score_run(it, rd)
    c = r["cases"].copy()
    for col in c.columns:                       # 집합 나열 순서(실행마다 바뀜)는 비교에서 뺌
        c[col] = c[col].map(lambda v: "".join(sorted(v)) if isinstance(v, str) else v)   # 글자 구성 비교 — 집합 나열 순서만 다른 것은 같게
    out[it] = dict(dir=rd.replace("\\", "/"), value=r["value"], n=r["n"],
                   cases=hashlib.md5(c.to_csv().encode()).hexdigest(),
                   tables={k: hashlib.md5(v.to_csv().encode()).hexdigest() for k, v in (r.get("tables") or {}).items()
                           if isinstance(v, pd.DataFrame)})
print("JSON" + json.dumps(out, ensure_ascii=False))
''' % ITEMS

SCREEN = r'''
import sys, os
sys.path.insert(0, sys.argv[1]); os.chdir(sys.argv[2])
from streamlit.testing.v1 import AppTest
bad = []
for item in ["AI1","AI2","AI3","AI4","AI5","AI6","AI7","DA2"]:
    def app(item):
        import streamlit as st, spec_page
        spec_page.render(item, lambda k: st.container())
    at = AppTest.from_function(app, default_timeout=400, args=(item,)).run()
    if at.exception:
        bad.append(f"{item}: {str(at.exception[0].value)[:120]}")
print("BAD" + "|".join(bad))
'''


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def last_deploy_base():
    try:
        h = subprocess.run(["ssh", "app", "tail -1 /opt/bi/nipa_ai_eval/deploy/deploy_history.log"],
                           capture_output=True, text=True, encoding="utf-8", timeout=30).stdout.strip()
        return h.split("\t")[1] if "\t" in h else None              # 시각 \t 이전 \t 이후 \t 결과
    except Exception:                                                  # noqa: BLE001
        return None


def run_py(code, codedir):
    f = os.path.join(os.path.dirname(codedir), f"_chk_{abs(hash(code))}.py")   # AppTest 는 함수 소스를 파일에서 읽음 → -c 대신 파일
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(code)
    p = subprocess.run([PY, f, codedir, ROOT], capture_output=True, text=True, encoding="utf-8",
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=1800)
    return p.stdout


def main():
    git("fetch", "-q", "origin")
    head = sys.argv[2] if len(sys.argv) > 2 else BR
    base = sys.argv[1] if len(sys.argv) > 1 else (last_deploy_base() or f"{head}~1")
    hs, bs = git("rev-parse", "--short", head), git("rev-parse", "--short", base)
    print(f"확인 범위: {bs} → {hs}")
    print(git("log", "--oneline", f"{bs}..{hs}") or "(새 커밋 없음)")
    files = [f for f in git("-c", "core.quotepath=off", "diff", "--name-only", bs, hs).splitlines() if f]
    print(f"\n① 바뀐 파일 {len(files)}개: " + ", ".join(files[:30]) + (" …" if len(files) > 30 else ""))

    other = [f for f in files if f.startswith("spec_data/") and not any(k in f.lower() for k in ("ai6", "ai7"))]
    print("② 다른 항목 시험 데이터(AI1~AI5·DA2): " + ("✅ 바뀌지 않음" if not other else "❌ 바뀜 — " + ", ".join(other)))
    com = [f for f in files if f in COMMON]
    print("③ 공통 코드: " + ("바뀌지 않음" if not com else "바뀜 — " + ", ".join(com) + " (아래 ④⑤로 영향 확인)"))

    tmp = tempfile.mkdtemp(prefix="chk_")
    try:
        res = {}
        for tag, rev in (("기준", bs), ("대상", hs)):
            d = os.path.join(tmp, tag)
            git("worktree", "add", "-q", "--detach", d, rev)
            out = run_py(SCORE, d)
            res[tag] = json.loads(out.split("JSON", 1)[1]) if "JSON" in out else {}
            if tag == "대상":
                scr = run_py(SCREEN, d)
        line = []
        ok_all = True
        for it in ITEMS:
            a, b = res["기준"].get(it), res["대상"].get(it)
            same = a is not None and a == b
            ok_all &= same
            line.append(f"{it} {b['value'] if b else '?'} " + ("✅" if same else "❌"))
        print("④ 채점(공식 결과 폴더, 기준 코드 ↔ 대상 코드): " + " · ".join(line))
        if not ok_all:
            for it in ITEMS:
                a, b = res["기준"].get(it), res["대상"].get(it)
                if a != b:
                    print(f"   {it} 기준 {a and {k: a[k] for k in ('value', 'n', 'cases')}} / 대상 {b and {k: b[k] for k in ('value', 'n', 'cases')}}")
        bad = scr.split("BAD", 1)[1].strip() if "BAD" in scr else "실행 실패"
        print("⑤ 화면(AI1~AI7·DA2): " + ("✅ 오류 없음" if not bad else "❌ " + bad))
        print("\n→ " + ("섞인 것 없음" if not other and ok_all and not bad else "확인 필요 (❌ 항목)"))
    finally:
        for tag in ("기준", "대상"):
            git("worktree", "remove", "--force", os.path.join(tmp, tag))
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
