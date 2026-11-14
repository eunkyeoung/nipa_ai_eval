# [DA 2-1] 위임 관계 항 단위 보완 — 동결 온톨로지 '사본'에 더하는 추가 파일(패치) 생성 (2026-11-14 사용자 지시, 변경내역 110절)
#
# 배경: 의뢰기업 요청(11-14) — CQ-005·CQ-010 원래 질문 유지, 관계 데이터 문제면 테스트용 복제본에서 보완 후 새 평가 데이터로 동결.
#   동결 온톨로지는 위임(implementedBy)을 '위임하는 쪽 항 → 받는 쪽 조'로만 기록 → 받는 조의 어느 항인지 모름.
#   예) 건축법 제20조 제1·2·3·5항 → 건축법 시행령 제15조 (원문: 시행령 제15조 ⑤ "법 제20조제3항에서 …")
#
# 원칙
#   - Fuseki(116:3030)·법령 수집 사이트(116:17801)·동결 원본 ttl 은 읽기만 함. 결과는 별도 파일
#     spec_data/da2/landcheck_schema_patch_v1.1.ttl(새 속성 선언) · landcheck_instance_patch_v1.1.ttl(더한 관계)
#   - 개발자가 만든 기존 관계만 정밀하게 함: 기존 엣지 U(항·호) implementedBy A(조) 가 있고, A 의 원문 항 H 가 U 를 직접 인용할 때만
#     U implementedBy H 를 더함(기존 엣지는 지우지 않음). 없던 법령 사이 관계는 만들지 않음
#   - 인용 단서: 받는 쪽이 위임하는 쪽 법령의 시행령·시행규칙이면 '법 제N조제M항'(시행규칙에서 시행령은 '영 제N조제M항'),
#     그 밖에는 「법령명」 제N조제M항. 인용 바로 뒤가 '단서'이면 단서 위임으로 aip:provisoImplementedBy 도 더함
#   - aip:provisoImplementedBy 는 스키마에 없어 패치 파일에 선언(rdfs:subPropertyOf aip:implementedBy, domain·range 같음)
#   - 정답·질의와 무관하게 전체 엣지에 같은 규칙을 적용(정답 맞춤 아님)
#
#   python tools/da2_unitlink.py edges                 → spec_data/da2/_unitlink/edges.json (동결 인스턴스에서 기존 엣지·받는 조의 항)
#   python tools/da2_unitlink.py fetch <현황 엑셀>     → 받는 쪽 법령 원문 캐시(spec_data/da2/_law_cache, 읽기만)
#   python tools/da2_unitlink.py build                 → 위 두 패치 파일 + _unitlink/report.csv
import csv, json, os, re, sys
from collections import defaultdict

DA2 = "spec_data/da2"
INST = f"{DA2}/landcheck_instance_v1.0.ttl"
OUT = f"{DA2}/_unitlink"
PATCH = {"schema": f"{DA2}/landcheck_schema_patch_v1.1.ttl", "instance": f"{DA2}/landcheck_instance_patch_v1.1.ttl"}   # 스키마·인스턴스 따로(OOPS!엔 스키마만)
A = "https://onto.go.kr/aip/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
NT = re.compile(r'^<([^>]+)> <([^>]+)> (?:<([^>]+)>|"((?:[^"\\]|\\.)*)"(?:\^\^<[^>]+>|@[a-zA-Z-]+)?) \.\s*$')
CIRC = {c: i + 1 for i, c in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")}
WANT = {A + p for p in ("implementedBy", "hasUnit", "hasArticle", "lawName", "articleNo", "unitType", "unitNo")} | {RDF_TYPE, LABEL}


def edges():
    """동결 인스턴스(N-Triples 형식)를 줄 단위로 읽어 기존 '단위 → 조' 위임과 받는 조의 항 노드를 뽑음"""
    impl, has_unit, law_of, prop = [], defaultdict(list), {}, defaultdict(dict)
    typ = defaultdict(set)
    with open(INST, encoding="utf-8") as f:
        for ln in f:
            m = NT.match(ln)
            if not m or m.group(2) not in WANT:
                continue
            s, p, o = m.group(1), m.group(2), m.group(3) if m.group(3) is not None else m.group(4)
            if p == RDF_TYPE:
                typ[s].add(o.replace(A, ""))
            elif p == A + "implementedBy":
                impl.append((s, o))
            elif p == A + "hasUnit":
                has_unit[s].append(o)
            elif p == A + "hasArticle":
                law_of[o] = s
            else:
                prop[s][re.split(r"[/#]", p)[-1]] = o
    parent = {c: s for s, cs in has_unit.items() for c in cs}

    def article_of(n):
        while n in parent and "Article" not in typ[n]:
            n = parent[n]
        return n if "Article" in typ[n] else None

    def chain(n):
        """단위 n 의 (조 노드, [항번호, 호번호, ...])"""
        nums = []
        while n and "ArticleUnit" in typ[n]:
            nums.append(prop[n].get("unitNo", ""))
            n = parent.get(n)
        return n, nums[::-1]

    out = []
    for u, a in impl:
        if "ArticleUnit" not in typ[u] or "Article" not in typ[a]:
            continue
        ua, nums = chain(u)
        if not ua or ua not in law_of or a not in law_of:
            continue
        hangs = {prop[h].get("unitNo"): h for h in has_unit.get(a, []) if prop[h].get("unitType") == "항"}
        out.append(dict(u=u, u_label=prop[u].get("label", ""), u_law=prop[law_of[ua]].get("lawName", ""), u_art=prop[ua].get("articleNo", ""),
                        u_nums=nums,
                        a=a, a_label=prop[a].get("label", ""), a_law=prop[law_of[a]].get("lawName", ""), a_art=prop[a].get("articleNo", ""),
                        a_hangs=hangs))
    os.makedirs(OUT, exist_ok=True)
    json.dump(out, open(f"{OUT}/edges.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("단위→조 위임", len(out), "/ 받는 쪽 법령", len({e["a_law"] for e in out}), "/ 받는 조에 항 있음", sum(bool(e["a_hangs"]) for e in out))


CACHE = f"{DA2}/_law_cache"


def _edges():
    return json.load(open(f"{OUT}/edges.json", encoding="utf-8"))


def fetch(xlsx):
    """받는 쪽 법령 원문을 법령 수집 사이트에서 조회(읽기만)해 캐시. 계정은 엑셀에서 읽기만 함(tools/law_text.py)"""
    sys.path.insert(0, ".")
    from tools.law_text import LawText
    lt = LawText(xlsx)
    laws = sorted({e["a_law"] for e in _edges() if e["a_hangs"]})
    miss = []
    for i, n in enumerate(laws, 1):
        try:
            lt.arts(n)
        except KeyError:
            miss.append(n)
        if i % 50 == 0:
            print(i, "/", len(laws), flush=True)
    json.dump(miss, open(f"{OUT}/fetch_missing.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("법령", len(laws), "/ 사이트에 없음", len(miss), miss[:10])


def _arts(name):
    docs = json.load(open(f"{CACHE}/docs.json", encoding="utf-8"))
    n = re.sub(r"\s", "", name)
    hit = [k for k in docs if re.sub(r"\s", "", k) == n]
    p = f"{CACHE}/{docs[hit[0]]}.json" if hit else None
    return json.load(open(p, encoding="utf-8")) if p and os.path.exists(p) else None


def _art_key(a):
    """사이트 조문 → 온톨로지 articleNo 표기('15', '29-2')"""
    no = re.sub(r"\s", "", str(a.get("article_no_full") or a.get("article_no") or ""))
    m = re.match(r"^제(\d+)조(?:의(\d+))?", no)
    return (m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")) if m and not a.get("is_byp") in (True, "True") else None


def _hang_texts(a):
    """조 본문 → {항번호: 항 본문} (본문의 ①②… 로 나눔)"""
    t = "\n".join(b.get("text") or "" for b in (a.get("text") or {}).get("blocks") or [])
    parts = re.split(r"(?m)^\s*([①-⑳])", t)
    return {CIRC[parts[i]]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


SEP = r"(?:\s*(?:,|·|및|또는|와|과|부터|까지|ㆍ)\s*|\s+)"
ITEM = re.compile(r"제(\d+)항(?:\s*제(\d+)호)?(\s*단서)?")


def _refs(text, prefixes):
    """본문에서 '<접두> 제N조(의B) 제M항(제K호)( 단서) …' 인용을 찾아 [(조키, 항, 호, 단서)]"""
    out = []
    for pre in prefixes:
        for m in re.finditer(rf"{pre}\s*제(\d+)조(?:의(\d+))?((?:{SEP}?제\d+항(?:\s*제\d+호)?(?:\s*(?:단서|본문))?)+)", text):
            art = m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")
            items = list(ITEM.finditer(m.group(3)))
            for j, it in enumerate(items):
                out.append((art, it.group(1), it.group(2), bool(it.group(3))))
                # '제1항부터 제3항까지' → 사이 항도
                if j + 1 < len(items) and "부터" in m.group(3)[it.end():items[j + 1].start()] and not it.group(2):
                    for h in range(int(it.group(1)) + 1, int(items[j + 1].group(1))):
                        out.append((art, str(h), None, False))
    return out


def _prefixes(up, low, doc_text):
    """받는 쪽(low) 원문에서 위임하는 쪽(up) 법령을 부르는 표기"""
    pre = [re.escape(f"「{up}」")]
    if low in (f"{up} 시행령", f"{up} 시행규칙") and not up.endswith(("시행령", "시행규칙")):
        pre.append(r"(?<![가-힣])법")
    if up.endswith(" 시행령") and low == up[:-4] + " 시행규칙":
        pre.append(r"(?<![가-힣])영")
    for ab in ("법", "영"):                                              # 조례 등: 「건축법」(이하 "법"이라 한다)
        if re.search(rf"「{re.escape(up)}」\s*\(\s*이하\s*[\"“”']{ab}[\"“”']", doc_text) and rf"(?<![가-힣]){ab}" not in pre:
            pre.append(rf"(?<![가-힣]){ab}")
    return pre


def build():
    E = _edges()
    cache, add, rep = {}, set(), []
    for e in E:
        if not e["a_hangs"]:
            continue
        if e["a_law"] not in cache:
            arts = _arts(e["a_law"]) or []
            doc_text = "\n".join("\n".join(b.get("text") or "" for b in (a.get("text") or {}).get("blocks") or []) for a in arts)
            cache[e["a_law"]] = ({_art_key(a): a for a in arts if _art_key(a)}, doc_text)
        arts, doc_text = cache[e["a_law"]]
        a = arts.get(e["a_art"])
        if not a:
            continue
        pre = _prefixes(e["u_law"], e["a_law"], doc_text)
        hang_u, ho_u = e["u_nums"][0], (e["u_nums"][1] if len(e["u_nums"]) > 1 else None)
        for hn, ht in _hang_texts(a).items():
            h = e["a_hangs"].get(str(hn))
            if not h:
                continue
            for art, hg, ho, prov in _refs(ht, pre):
                if art != e["u_art"] or hg != hang_u or (ho_u and ho != ho_u):
                    continue
                add.add((e["u"], "implementedBy", h))
                if prov and not ho_u:
                    add.add((e["u"], "provisoImplementedBy", h))
                rep.append(dict(위임하는쪽=e["u_label"], 받는조=e["a_label"], 받는항=f"{e['a_label']}제{hn}항", 단서=prov,
                                원문=re.sub(r"\s+", " ", ht)[:160]))
    # 기존에 같은 엣지가 이미 있으면 빼기
    have = set()
    with open(INST, encoding="utf-8") as f:
        for ln in f:
            if "/implementedBy>" in ln:
                m = NT.match(ln)
                have.add((m.group(1), m.group(3)))
    add = sorted(t for t in add if not (t[1] == "implementedBy" and (t[0], t[2]) in have))
    head = ("# [QA 보완 패치 {k}] 동결 온톨로지(v1.0, 2026-11-12 17:46 내보내기) 사본에 더함 — tools/da2_unitlink.py (변경내역 110절)\n"
            "# 기존 '단위 → 조' 위임 중, 받는 조의 원문 항이 위임하는 단위를 직접 인용한 경우만 '단위 → 항'으로 더함. 기존 관계는 지우지 않음.\n"
            "# Fuseki 원본에는 반영하지 않음(개발자 반영 필요).\n"
            "@prefix aip: <https://onto.go.kr/aip/> .\n@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
            "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n\n")
    with open(PATCH["schema"], "w", encoding="utf-8", newline="\n") as f:
        f.write(head.format(k="스키마") +
                "aip:provisoImplementedBy a owl:ObjectProperty ;\n"
                "    rdfs:subPropertyOf aip:implementedBy ;\n"
                "    rdfs:label \"단서 위임\"@ko ;\n"
                "    rdfs:comment \"이 (상위) 단위의 단서(다만 …)가 하위 법령의 어느 단위로 구체화되는가 — 원문 '법 제N조제M항 단서에서'. QA 보완 패치(2026-11-14).\"@ko ;\n"
                "    rdfs:domain [ a owl:Class ; owl:unionOf ( aip:Article aip:ArticleUnit ) ] ;\n"
                "    rdfs:range [ a owl:Class ; owl:unionOf ( aip:Article aip:ArticleUnit aip:Appendix ) ] .\n")
    with open(PATCH["instance"], "w", encoding="utf-8", newline="\n") as f:
        f.write(head.format(k="인스턴스"))
        for s, p, o in add:
            f.write(f"<{s}> aip:{p} <{o}> .\n")
    with open(f"{OUT}/report.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["위임하는쪽", "받는조", "받는항", "단서", "원문"])
        w.writeheader()
        w.writerows(rep)
    print("더한 관계", len(add), "(항 위임", sum(p == "implementedBy" for _, p, _ in add), "/ 단서", sum(p == "provisoImplementedBy" for _, p, _ in add),
          ") / 대상 위임", sum(bool(e["a_hangs"]) for e in E), "/ 원문 없는 법령", sum(1 for v in cache.values() if not v[0]))


if __name__ == "__main__":
    {"edges": edges, "fetch": fetch, "build": build}[sys.argv[1]](*sys.argv[2:])
