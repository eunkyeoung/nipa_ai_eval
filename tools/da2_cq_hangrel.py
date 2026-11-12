# [DA 2-1] 혼합 기준용: 동결 온톨로지에서 '고유 관계가 있는 항' 목록을 뽑아 고정 (변경내역 91절)
# 고유 관계 = 항 자신이나 그 하위 호·목에 붙은 구조 외 관계(위임 implementedBy·예외 exemptsArticle·제한 restrictedBy 등, 들어오고 나가는 쪽 모두)
#   구조 관계(hasUnit·hasArticle·hasAppendix·unitType·unitNo·label·type)는 뺌
# 혼합 기준: 이 목록에 있는 항은 항 단위, 없는 항은 조 단위로 정답·결과를 똑같이 맞춤
#   python tools/da2_cq_hangrel.py   → spec_data/da2/da2_cq_hang_rel.json (항 키 목록 + 온톨로지 sha256)
import hashlib, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.da2_cq import TTL, norm                                     # noqa: E402

OUT = "spec_data/da2/da2_cq_hang_rel.json"
STRUCT = "aip:hasUnit, aip:hasArticle, aip:hasAppendix, aip:unitType, aip:unitNo, rdfs:label, rdf:type"
P = ("PREFIX aip:<https://onto.go.kr/aip/> PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#> "
     "PREFIX rdf:<http://www.w3.org/1999/02/22-rdf-syntax-ns#> ")
Q = P + f"""SELECT DISTINCT ?l WHERE {{ ?h aip:unitType ?t . FILTER(STR(?t) = "항") ?h rdfs:label ?l . ?h aip:hasUnit* ?d .
  {{ ?d ?p ?x . FILTER(isIRI(?x)) }} UNION {{ ?x ?p ?d . }} FILTER(?p NOT IN ({STRUCT})) }}"""


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def build(store=None):
    import pyoxigraph as ox
    st = store
    if st is None:
        st = ox.Store()
        for k in ("schema", "instance"):
            st.bulk_load(path=TTL[k], format=ox.RdfFormat.TURTLE)
    return sorted({norm(r["l"].value) for r in st.query(Q)})


def load():
    return set(json.load(open(OUT, encoding="utf-8"))["hang"])


if __name__ == "__main__":
    t = time.time()
    keys = build()
    json.dump(dict(made=time.strftime("%Y-%m-%d %H:%M:%S"), rule="항(또는 하위 호·목)에 구조 외 관계가 있으면 항 단위",
                   instance_sha256=sha(TTL["instance"]), schema_sha256=sha(TTL["schema"]), n=len(keys), hang=keys),
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(OUT, len(keys), f"{time.time() - t:.0f}s")
