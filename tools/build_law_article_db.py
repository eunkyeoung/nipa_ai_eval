# 법령·조례 조문 DB(spec_data/law_article_db.csv) 생성 — 규격서 v1.51 [AI 5] "조문 실재 여부 검증용 법령·조례 조문 DB"
# 원천: 법령 수집·적재 관리자(192.168.200.116:17801) 적재분 전체 (법령·조례·행정규칙). 조회만 함.
# 실행:  python tools/build_law_article_db.py "<개발 관리운영 사이트(URL) 현황 조사 엑셀 경로>"
#   - 로그인 계정은 실행할 때 엑셀에서 읽기만 하고 저장·출력하지 않음
#   - 조 단위 · 항 단위(본문의 ①②… 로 판별) · 별표 조문 ID 생성. 별표 묶음("[별표 1] ~ 별표 25]")은 범위를 펼침
import sys, re, csv, os
import openpyxl, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import spec_eval as S
B = "http://192.168.200.116:17801"
def session(xlsx):
    ws = openpyxl.load_workbook(xlsx, data_only=True).active
    cell = next(str(r[3]) for r in ws.iter_rows(values_only=True) if r[2] == B + "/")
    toks = [t for t in re.split(r"[\s/,:]+|ID|PW|PWD|아이디|비밀번호|\(|\)", cell, flags=re.I) if t]
    s = requests.Session()
    s.post(B + "/api/auth/login", json={"username": toks[0], "password": toks[1]}, timeout=10).raise_for_status()
    return s
s = session(sys.argv[1])
docs = {}
def walk(x):
    for d in x:
        docs.setdefault(d["doc_id"], d); walk(d.get("children") or [])
for t in ["law", "ordin", "admrul", None]:
    walk(s.get(B + "/api/documents/hierarchy", params={"target": t} if t else {}, timeout=300).json())
print("문서", len(docs), flush=True)
ART = re.compile(r"^\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")
BYP = re.compile(r"별표\s*(\d+)(?:\s*의\s*(\d+))?")
RANGE = re.compile(r"별표\s*(\d+)\s*\]?\s*[~∼～-]\s*\[?\s*별표\s*(\d+)")
HANG = re.compile(r"(?m)^\s*([①-⑳])")
CIRC = {c: i + 1 for i, c in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")}
rows, fail = [], []
for i, (did, d) in enumerate(sorted(docs.items()), 1):
    try:
        arts = s.get(f"{B}/api/documents/{did}/articles", params={"converted": "false"}, timeout=120).json()
    except Exception as e:
        fail.append(did); continue
    law = S.norm_law(d["law_name"]); lid = S.law_id(law)
    for a in arts if isinstance(arts, list) else []:
        no = str(a.get("article_no_full") or a.get("article_no") or "")
        if a.get("is_byp"):
            rng = RANGE.search(no)                 # "[별표 1] ~ 별표 25]" 처럼 여러 별표를 한 묶음으로 적재한 경우 → 범위를 펼침
            if rng:
                for k in range(int(rng.group(1)), int(rng.group(2)) + 1):
                    rows.append((f"{lid}.별표{k}", d["law_name"], d.get("target"), f"별표{k}", "", d.get("effective_date")))
                continue
            m = BYP.search(no)
            if m: rows.append((f"{lid}.별표{m.group(1)}" + (f"의{m.group(2)}" if m.group(2) else ""), d["law_name"], d.get("target"), "별표" + m.group(1) + (f"의{m.group(2)}" if m.group(2) else ""), "", d.get("effective_date")))
            continue
        m = ART.match(no)
        if not m: continue
        jo = m.group(1) + (f"의{m.group(2)}" if m.group(2) else "")
        rows.append((f"{lid}.{jo}", d["law_name"], d.get("target"), jo, "", d.get("effective_date")))
        txt = "\n".join(b.get("text") or "" for b in (a.get("text") or {}).get("blocks") or [])
        for h in sorted({CIRC[x] for x in HANG.findall(txt)}):
            rows.append((f"{lid}.{jo}.{h}", d["law_name"], d.get("target"), jo, str(h), d.get("effective_date")))
    if i % 200 == 0: print(f"{i}/{len(docs)} 조문행 {len(rows)}", flush=True)
seen = set(); out = []
for r in rows:
    if r[0] not in seen: seen.add(r[0]); out.append(r)
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spec_data", "law_article_db.csv"),
          "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["조문ID", "법령명", "구분", "조", "항", "시행일"]); w.writerows(out)
print("완료: 문서", len(docs), "실패", len(fail), "조문ID", len(out), flush=True)
