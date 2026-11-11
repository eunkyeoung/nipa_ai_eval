# 법령 수집·적재 관리자(116:17801)에서 조문 원문 조회 (조회만 함) — [DA 2] CQ 정답 작성 근거 확인용
# 사용:  from tools.law_text import LawText;  lt = LawText(xlsx);  lt.article("건축법", "44")  → 조문 원문
#   - 계정은 엑셀(개발 관리운영 사이트 URL 현황)에서 읽기만 하고 저장·출력하지 않음
import json, os, re
import openpyxl, requests

B = "http://192.168.200.116:17801"
CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spec_data", "da2", "_law_cache")


class LawText:
    def __init__(self, xlsx):
        ws = openpyxl.load_workbook(xlsx, data_only=True).active
        cell = next(str(r[4] if len(r) > 4 and "17801" in str(r[3]) else r[3]) for r in ws.iter_rows(values_only=True)
                    if r and any(c and str(c).rstrip("/") == B for c in r))
        t = [x for x in re.split(r"[\s/,:]+|ID|PW|PWD|아이디|비밀번호|\(|\)", cell, flags=re.I) if x]
        self.s = requests.Session()
        self.s.post(B + "/api/auth/login", json={"username": t[0], "password": t[1]}, timeout=15).raise_for_status()
        os.makedirs(CACHE, exist_ok=True)
        p = os.path.join(CACHE, "docs.json")
        if os.path.exists(p):
            self.docs = json.load(open(p, encoding="utf-8"))
        else:
            docs = {}

            def walk(x):
                for d in x:
                    docs.setdefault(d["law_name"], d["doc_id"])
                    walk(d.get("children") or [])
            for tg in ("law", "ordin", "admrul"):
                walk(self.s.get(B + "/api/documents/hierarchy", params={"target": tg}, timeout=300).json())
            self.docs = docs
            json.dump(docs, open(p, "w", encoding="utf-8"), ensure_ascii=False)

    def find(self, name):
        n = re.sub(r"\s", "", name)
        hit = [k for k in self.docs if re.sub(r"\s", "", k) == n]
        return hit[0] if hit else None

    def arts(self, name):
        law = self.find(name)
        if not law:
            raise KeyError(f"법령 없음: {name}")
        p = os.path.join(CACHE, f"{self.docs[law]}.json")
        if not os.path.exists(p):
            a = self.s.get(f"{B}/api/documents/{self.docs[law]}/articles", params={"converted": "false"}, timeout=120).json()
            json.dump(a, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        return json.load(open(p, encoding="utf-8"))

    @staticmethod
    def text(a):
        return "\n".join(b.get("text") or "" for b in (a.get("text") or {}).get("blocks") or [])

    def article(self, name, jo):
        """jo: '44' / '12의2' / '별표17'"""
        for a in self.arts(name):
            no = str(a.get("article_no_full") or a.get("article_no") or "")
            if jo.startswith("별표"):
                if a.get("is_byp") and re.search(rf"별표\s*{jo[2:]}(?![0-9의])", no):
                    return no + "\n" + self.text(a)
            elif re.match(rf"^\s*제\s*{jo.replace('의', r'조의') if '의' in jo else jo + '조'}(?![0-9의])", no.replace(" ", "")):
                return no + "\n" + self.text(a)
        return None
