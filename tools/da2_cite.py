# [DA 2-1] CQ 답변의 조문 표기("건축법 시행령 제28조 제1항 제2항", "[별표 20]" 등) → 비교용 키('건축법시행령.28.1')
# AI1~AI5 공통 변환(spec_eval.parse_citations)은 건드리지 않고 DA2 전용으로 둠
# 규칙(엑셀 작성 규칙·규격서 정규화): 항까지 비교, 호·목은 뺌. 항이 없으면 '법령.조', 별표는 '법령.별표N'
#   - "제28조 제1항 제2항" / "제28조제1항제2항" → 제28조 제1항, 제28조 제2항 (항 뒤의 항 = 같은 조의 다른 항)
#   - "제45조 제1항 제2호" → 제45조 제1항 (호는 뺌)
#   - "같은 법", "같은 시행령", "같은 조례", "영", "법"(앞 법령 기준) 등 앞의 법령을 가리키는 표현 처리
#   - 법령명은 공백을 빼고 약칭으로: 국토의 계획 및 이용에 관한 법률 → 국토계획법
import re

ALIAS = [("국토의계획및이용에관한법률", "국토계획법")]
LAW_RX = r"[가-힣A-Za-z0-9·ㆍ\s]*?(?:법률|법|령|규칙|조례)(?:\s*시행령|\s*시행규칙)?"
TOKEN = re.compile(
    r"(?P<law>(?:같은\s*(?:법|시행령|시행규칙|조례|영|규칙))|" + LAW_RX + r")?\s*"
    r"(?:(?P<jo>제\s*\d+\s*조(?:\s*의\s*\d+)?)(?P<rest>(?:\s*제\s*\d+\s*(?:항|호|목))*)"
    r"|\[?\s*(?P<byp>별표\s*\d+(?:\s*의\s*\d+)?)\s*\]?)")


def _law_key(name):
    k = re.sub(r"\s", "", name)
    for a, b in ALIAS:
        k = k.replace(a, b)
    return k


def parse(text, default_law=""):
    """조문 표기 문자열 → 비교용 키 목록(순서 유지, 중복 제거)"""
    out, cur_law = [], _law_key(default_law) if default_law else ""
    base_law = cur_law
    for m in TOKEN.finditer(str(text)):
        law = (m.group("law") or "").strip(" ,·ㆍ")
        if law:
            if law.startswith("같은"):
                kind = re.sub(r"같은\s*", "", law)
                if kind in ("시행령", "영"):
                    cur_law = re.sub(r"(시행령|시행규칙)$", "", base_law) + "시행령"
                elif kind in ("시행규칙", "규칙"):
                    cur_law = re.sub(r"(시행령|시행규칙)$", "", base_law) + "시행규칙"
                # "같은 법"·"같은 조례"는 앞 법령 그대로
            else:
                cur_law = _law_key(law)
                base_law = cur_law
        if not cur_law:
            continue
        if m.group("byp"):
            byp = re.sub(r"\s", "", m.group("byp"))
            out.append(f"{cur_law}.{byp}")
            continue
        jm = re.match(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", m.group("jo"))
        jo = jm.group(1) + (f"의{jm.group(2)}" if jm.group(2) else "")
        hangs = re.findall(r"제\s*(\d+)\s*항", m.group("rest") or "")
        if hangs:
            out += [f"{cur_law}.{jo}.{h}" for h in hangs]
        else:
            out.append(f"{cur_law}.{jo}")
    seen = []
    for k in out:
        if k not in seen:
            seen.append(k)
    return seen


if __name__ == "__main__":
    for t in ["건축법 시행령 제28조 제1항 제2항", "건축법 시행령 제28조제1항제2항", "건축법 제45조 제1항 제2호",
              "건축법 제2조 제1항 제11호, 건축법 시행령 제3조의3",
              "국토의 계획 및 이용에 관한 법률 시행령 제71조 제1항 제19호, 같은 시행령 [별표 20], 국토의 계획 및 이용에 관한 법률 시행규칙 [별표 2]",
              "건축법 제58조, 건축법 시행령 제80조의2, 같은 시행령 별표2, 음성군 건축 조례 제30조, 같은 조례 [별표 3]",
              "건축법 제44조 제1항 단서, 건축법 시행령 제28조 제1항 제2항"]:
        print(t, "\n   →", parse(t))
