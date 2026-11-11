# [DA 2] 검증 자산 — Fuseki(116:3030, dataset nipa)의 온톨로지를 파일 3개로 내보냄 (조회만 함, Fuseki는 바꾸지 않음)
# 실행:  python tools/export_ontology.py "<개발 관리운영 사이트(URL) 현황 조사 엑셀 경로>"
#   - 계정은 실행할 때 엑셀에서 읽기만 하고 저장·출력하지 않음
#   - 스키마 = aip/semantic 그래프, SHACL = aip/shapes 그래프, 인스턴스 = 나머지 이름 있는 그래프 전체
#   - 인스턴스는 그래프별 N-Triples를 이어 붙임(N-Triples는 Turtle의 부분집합이라 .ttl로 읽힘)
import hashlib, os, re, sys, time
import openpyxl, requests

BASE = "http://192.168.200.116:3030/nipa"
SCHEMA_G = "https://onto.go.kr/aip/semantic"
SHAPES_G = "https://onto.go.kr/aip/shapes"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spec_data", "da2")


def auth_from(xlsx):
    ws = openpyxl.load_workbook(xlsx, data_only=True).active
    cell = next(str(r[4]) for r in ws.iter_rows(values_only=True) if r and r[3] and "3030" in str(r[3]))
    t = [x for x in re.split(r"[\s/,:]+", cell) if x]
    return t[0], t[1]


def graph(s, g, fmt):
    r = s.get(BASE + "/data", params={"graph": g}, headers={"Accept": fmt}, timeout=600)
    r.raise_for_status()
    return r.content


def main():
    os.makedirs(OUT, exist_ok=True)
    s = requests.Session()
    s.auth = auth_from(sys.argv[1])
    q = s.post(BASE + "/query", data={"query": "SELECT DISTINCT ?g WHERE { GRAPH ?g { ?s ?p ?o } } ORDER BY ?g"},
               headers={"Accept": "application/sparql-results+json"}, timeout=600)
    q.raise_for_status()
    gs = [b["g"]["value"] for b in q.json()["results"]["bindings"]]
    print("그래프", len(gs), flush=True)
    files = {"landcheck_schema_v1.0.ttl": graph(s, SCHEMA_G, "text/turtle"),
             "landcheck_shapes_v1.0.ttl": graph(s, SHAPES_G, "text/turtle")}
    for name, data in files.items():
        open(os.path.join(OUT, name), "wb").write(data)
    inst = os.path.join(OUT, "landcheck_instance_v1.0.ttl")
    with open(inst, "wb") as f:
        for i, g in enumerate(gs, 1):
            if g in (SCHEMA_G, SHAPES_G):
                continue
            f.write(graph(s, g, "application/n-triples"))
            if i % 100 == 0:
                print(f"  {i}/{len(gs)}", flush=True)
    meta = [f"# Fuseki {BASE} 내보내기 {time.strftime('%Y-%m-%d %H:%M:%S')}  그래프 {len(gs)}개"]
    for name in ("landcheck_schema_v1.0.ttl", "landcheck_instance_v1.0.ttl", "landcheck_shapes_v1.0.ttl"):
        p = os.path.join(OUT, name)
        h = hashlib.sha256(open(p, "rb").read()).hexdigest()
        meta.append(f"{name}\t{os.path.getsize(p)}\t{h}")
        print(name, f"{os.path.getsize(p)/1024:,.0f} KB", h)
    open(os.path.join(OUT, "export_info.txt"), "w", encoding="utf-8").write("\n".join(meta) + "\n")


if __name__ == "__main__":
    main()
