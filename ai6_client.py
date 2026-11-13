"""
[AI 6] 지도제어·공간분석 Sub 오케스트레이터(공간오케) 호출기
---------------------------------------------------------
자연어 지도 제어 명령 1건을 공간오케에 보내고 응답(SSE)을 모읍니다. 화면(spec_page)·실행기(spec_runner_ai6)가 함께 씁니다.

  POST {AI6_BASE}/api/chat   {"message": 명령문, "thread_id": 사례마다 새 값}
  Accept: text/event-stream  →  turn.started · call.started · call.finished · actions · ask · answer.delta
                                · receipts · workflow · answer_check · turn.finished · error

- 사례마다 새 thread_id → 앞 사례의 대화 상태(마지막 필지 등)가 섞이지 않음
- 사례당 총 대기 상한(deadline) — requests 의 timeout 은 '읽기 사이 공백' 제한이라 총시간을 못 막으므로
  타이머가 연결을 끊음. 끊으면 공간오케도 그 턴을 취소함(SSE 연결 종료 = 취소). 넘긴 사례는 '응답 오류'
- turn.finished(또는 error)에서 읽기를 멈춤 — workflow·receipts·answer_check 는 turn.finished 앞에 옴
- gather() 는 공간오케 plugin/coreplug/jsonapi.py 의 gather() 와 같은 모양으로 이벤트를 한 덩어리로 묶음
  (그쪽 코드는 서버 의존성이 있어 가져오지 않고 같은 규칙으로 다시 씀)

이 파일은 표준 라이브러리 + requests 만 씁니다(pandas 없이 시험 가능).
"""
import json
import os
import re
import threading
import time
import uuid

BASE = os.environ.get("AI6_BASE", "http://192.168.200.115:17006").rstrip("/")   # 공간오케 (시험 때 설정 화면·실행 인자로 바꿈)
DEADLINE = float(os.environ.get("AI6_DEADLINE", "180"))          # 사례당 응답 대기 상한(초) — 규격서 2-6 시험조건 '180초' (2026-11-11 규격서 반영)
ORIGIN = os.environ.get("AI6_ORIGIN", "qa-ai6")                   # X-Request-Origin — 공간오케 모니터 기록에서 QA 질의를 따로 모으는 표지 (빈 값이면 안 보냄)
# 사전 점검 '주소 검색 1건' — 시험셋에 없는 필지(AI1-0037). 시험 문장이 시험 전에 공간오케 기록에 남지 않게
PROBE_MESSAGE = os.environ.get("AI6_PROBE", "충청남도 아산시 둔포면 신남리 201-3번지로 지도 이동해줘")
CONNECT_TIMEOUT = 10

# 사전 점검 대상 (실행 전 확인 — 하나라도 실패하면 실행을 막음). 값은 환경변수로 바꿀 수 있음
CHECKS = {
    "공간오케 (/health)": os.environ.get("AI6_HEALTH_URL", BASE + "/health"),
    "공간오케 준비 상태 (/ready)": os.environ.get("AI6_READY_URL", BASE + "/ready"),
    "LLM (GPT-OSS 120B)": os.environ.get("AI6_LLM_URL", "http://192.168.200.128:8003/v1/models"),
    "공간분석 API (GIS API Server)": os.environ.get("AI6_GIS_URL", "http://192.168.200.116:17002/v3/api-docs"),
}

END_EVENTS = ("turn.finished", "error")

# 시험의뢰서 시험환경 'LLM 추론 환경 (GPT-OSS 120B)' — LLM 서버가 띄운 모델이 이것과 같은지 사전 점검에서 확인
LLM_MODEL = os.environ.get("AI6_LLM_MODEL", "gpt-oss-120b")
# 규격서 동결 버전 'vLLM 0.24.0' — LLM 서버 /version 이 이것과 다르면 사전 점검 실패 (빈 값이면 기록만)
VLLM_VERSION = os.environ.get("AI6_VLLM_VERSION", "0.24.0")
GIS_API_VERSION = os.environ.get("AI6_GIS_API_VERSION", "v0.1")   # 규격서 2-6 시험조건 공간분석 API 버전 — 읽은 값이 다르면 사전 점검 실패
# 규격서 2-6 시험조건: 시험 기간 중 고정(바뀌면 전체 재수행) — 시험방법 (2) '동결 버전 확인' 표의 '시험 조건' 칸
SPEC_VERSIONS = [("AI 모델", "GPT-OSS 120B (gpt-oss-120b)"), ("추론 서버 (vLLM)", "0.24.0"),
                 ("지도제어·공간분석 Sub 오케스트레이터", "v1.0"), ("지도제어·공간분석 에이전트", "v1.0"),
                 ("공간분석 API", "v0.1"),
                 ("지도 데이터 기준일", "연속지적도 2026-10-14 · 토지이용계획 2026-05-06·05-07 · LX맵 2026-08-31·09-01"),
                 ("AI 엔진 결정성 옵션", "temperature = 0")]   # 규격 시험환경 '결정성 옵션(temperature = 0) 적용'


def llm_key_env():
    """LLM 서버(vLLM) 키 — 화면에서 넣지 않았을 때 쓰는 환경변수 (AI6_LLM_KEY, 없으면 VLLM_API_KEY). 기록에는 남기지 않음"""
    return os.environ.get("AI6_LLM_KEY") or os.environ.get("VLLM_API_KEY") or ""


def _model_name(x):
    """'openai/gpt-oss-120b' · 'GPT-OSS-120B' → 'gpt-oss-120b' (제공처 접두어·대소문자 차이는 같은 모델로 봄)"""
    return str(x or "").strip().lower().rsplit("/", 1)[-1]


def _host_port(url):
    from urllib.parse import urlparse
    u = urlparse(str(url or ""))
    return (u.hostname or "", u.port or (443 if u.scheme == "https" else 80))


def llm_version(url):
    """vLLM 서버 버전 — 같은 서버의 /version (키 없이 열림). url 은 …/v1/models 주소. 못 읽으면 None"""
    root = str(url or "").split("/v1/", 1)[0].rstrip("/")
    r = _get(root + "/version")
    v = r["body"].get("version") if r["ok"] and isinstance(r["body"], dict) else None
    return str(v) if v else None


def llm_check(url, key, ready_diag=None):
    """LLM 서버 확인 — (정상 여부, 내용, 기록용 정보, 응답).
    /version: vLLM 버전이 시험 조건(VLLM_VERSION)과 다르면 실패, 못 읽으면 '미확인'으로 기록만.
    /v1/models: 지금 띄운 모델 — 키가 있어야 목록을 줌. 키가 없어 401/403 이면 목록은 확인 못 함(그 밖의 확인은 함).
    키를 줬는데 401/403 이면 키가 틀린 것(실패).
    ready_diag(공간오케 /ready 의 vllm_diag)가 있으면 공간오케가 쓰는 LLM 주소·모델도 맞춰 봄(키가 없어도)"""
    r = _get(url, headers={"Authorization": f"Bearer {key}"} if key else None)
    ver = llm_version(url)
    info = {"주소": url, "확인": "키 없음", "응답(ms)": r["ms"], "vLLM": ver or "미확인", "vLLM 시험조건": VLLM_VERSION or None}
    vtxt = f"vLLM {ver}" if ver else "vLLM 버전 미확인(/version 응답 없음)"
    probs = []
    if VLLM_VERSION and ver and ver != VLLM_VERSION:
        probs.append(f"vLLM {ver} — 시험 조건(vLLM {VLLM_VERSION})과 다름")
    diag = ready_diag or {}
    if diag.get("base") and _host_port(diag["base"]) != _host_port(url):
        probs.append(f"공간오케는 다른 LLM 서버({diag['base']})를 씀 — 점검 주소와 다름")
    if r["status"] in (401, 403):
        if key:
            info["확인"] = "키 오류"
            return False, f"서버 응답 {r['status']} — LLM 키가 맞지 않아요(키를 고치거나 비우면 서버 응답만 확인)", info, r
        if diag.get("model") and _model_name(diag["model"]) != _model_name(LLM_MODEL):
            probs.append(f"공간오케 설정 모델({diag['model']})이 시험 조건({LLM_MODEL})과 다름")
        info["일치"] = None if not probs else False
        tail = "; ".join(probs) if probs else "키가 없어 띄운 모델 목록은 확인 못 함 (LLM 키 칸 또는 AI6_LLM_KEY)"
        return not probs, f"서버 응답 {r['status']} · {vtxt} — {tail}", info, r
    if not (r["ok"] and isinstance(r["body"], dict)):
        info["확인"] = "응답 없음"
        return False, str(r["body"])[:200], info, r
    served = [m for m in r["body"].get("data") or [] if isinstance(m, dict)]
    names = {_model_name(m.get(k)) for m in served for k in ("id", "root") if m.get(k)}
    info.update(확인="키로 확인" if key else "인증 없이 확인", 모델=[m.get("id") for m in served],
                원본=[m.get("root") for m in served], 최대길이=[m.get("max_model_len") for m in served], 시험조건=LLM_MODEL)
    desc = ", ".join(f"{m.get('id')}" + (f" ({m.get('root')}" + (f" · 최대 {m.get('max_model_len'):,} 토큰" if isinstance(m.get("max_model_len"), int) else "") + ")"
                                         if m.get("root") and m.get("root") != m.get("id") else "") for m in served) or "목록 비어 있음"
    if _model_name(LLM_MODEL) not in names:
        probs.append(f"시험 조건 모델({LLM_MODEL})이 목록에 없음")
    if diag.get("model") and _model_name(diag["model"]) not in names:
        probs.append(f"공간오케 설정 모델({diag['model']})을 이 서버가 띄우지 않음")
    info["일치"] = not probs
    tail = "; ".join(probs) if probs else (f"시험 조건({LLM_MODEL})과 같음" + (" · 공간오케 설정 모델과 같음" if diag.get("model") else ""))
    return not probs, f"{vtxt} · 모델 {desc} — {tail}", info, r

# 공간오케는 에이전트를 모니터 중계(17007) → 콘솔 /proxy(18800)로 부름 — 콘솔·중계가 내려가면 주소가 든 사례가 전부 실패(D-199)
REQUIRED_SERVICES = [x.strip() for x in os.environ.get(
    "AI6_REQUIRED_SERVICES", "map_navigation_b,land_attr_b,road_access,spatial_regulation,land_regulation,"
    "map-control-mcp,parcel-info,land-mcp,road-mcp,spatial-mcp,regulation-mcp,vllm").split(",") if x.strip()]
RELAY_AGENTS = ["map_navigation_b", "land_attr_b", "road_access", "spatial_regulation", "land_regulation"]


def _host_url(base, port, path):
    from urllib.parse import urlparse
    return f"http://{urlparse(base).hostname or '127.0.0.1'}:{port}{path}"


def new_thread_id(case_id=""):
    tag = "".join(ch for ch in str(case_id) if ch.isalnum() or ch in "-_")[:24]
    return f"qa-ai6-{tag}-{uuid.uuid4().hex[:8]}" if tag else f"qa-ai6-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------- SSE 읽기
def iter_lines(chunks):
    """바이트 조각 → 줄. '\\r\\n' 이 조각 경계에서 갈려도 빈 줄이 하나 더 생기지 않게 직접 자름"""
    buf = b""
    for chunk in chunks:
        if not chunk:
            continue
        buf += chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8")
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line.rstrip(b"\r").decode("utf-8", "replace")
    if buf:
        yield buf.rstrip(b"\r").decode("utf-8", "replace")


def iter_sse(lines):
    """줄 → (이벤트 이름, 데이터). 빈 줄에서 이벤트를 닫고, 주석(': ping')은 버림.
    데이터가 JSON 이 아니면 {'_raw': 문자열}"""
    event, data = None, []
    for line in lines:
        if line.startswith(":"):
            continue
        if line == "":
            if event or data:
                yield _event(event, data)
            event, data = None, []
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event = value.strip()
        elif field == "data":
            data.append(value)
    if event or data:
        yield _event(event, data)


def _event(event, data):
    raw = "\n".join(data)
    try:
        obj = json.loads(raw) if raw else {}
    except ValueError:
        obj = {"_raw": raw}
    if not isinstance(obj, dict):
        obj = {"value": obj}
    return (event or "message"), obj


# ---------------------------------------------------------------- 호출
def stream_chat(message, thread_id=None, base=None, deadline=None, origin=ORIGIN, cancel_on_deadline=True):
    """명령 1건을 보내고 이벤트를 모음. 예외를 밖으로 던지지 않고 결과 dict 에 적음.

    돌려주는 값: {url, request, thread_id, http_status, events: [(이벤트, 데이터)], ended_by, error,
                 t_first_event, elapsed}
      ended_by: turn.finished · error(SSE error 이벤트) · eof(끝 이벤트 없이 연결 종료) · deadline(대기 상한 초과)
                · http(200 아님) · exception(연결 실패 등)"""
    import queue

    import requests

    base = (base or BASE).rstrip("/")
    limit = float(deadline or DEADLINE)
    tid = thread_id or new_thread_id()
    out = dict(url=f"{base}/api/chat", request={"message": message, "thread_id": tid}, thread_id=tid,
               http_status=None, events=[], ended_by=None, error="", t_first_event=None, elapsed=None,
               deadline=limit)
    t0 = time.monotonic()
    r = None
    try:
        r = requests.post(out["url"], json=out["request"], stream=True,
                          headers={"Accept": "text/event-stream", **({"X-Request-Origin": origin} if origin else {})},
                          timeout=(CONNECT_TIMEOUT, limit))
        out["http_status"] = r.status_code
        if r.status_code != 200:
            out["ended_by"] = "http"
            out["error"] = f"HTTP {r.status_code}: {r.text[:300]}"
            return out
        # 읽기는 따로 돌리고 여기서 남은 시간만큼만 기다림 — 서버가 결산 중 한동안 아무것도 안 보내도(keepalive 없음) 상한을 지킴
        q = queue.Queue()

        def reader(resp):
            try:
                for item in iter_sse(iter_lines(resp.iter_content(chunk_size=None))):
                    q.put(("ev", item))
                    if item[0] in END_EVENTS:
                        return
                q.put(("eof", None))
            except Exception as e:                                  # noqa: BLE001
                q.put(("exc", e))

        threading.Thread(target=reader, args=(r,), daemon=True).start()
        while out["ended_by"] is None:
            left = limit - (time.monotonic() - t0)
            if left <= 0:
                out["ended_by"] = "deadline"
                break
            try:
                kind, val = q.get(timeout=left)
            except queue.Empty:
                out["ended_by"] = "deadline"
                break
            if kind == "ev":
                if out["t_first_event"] is None:
                    out["t_first_event"] = round(time.monotonic() - t0, 3)
                out["events"].append(val)
                if val[0] in END_EVENTS:
                    out["ended_by"] = val[0]
            elif kind == "eof":
                out["ended_by"] = "eof"
            else:
                out["ended_by"] = "exception"
                out["error"] = f"{type(val).__name__}: {str(val)[:300]}"
    except requests.exceptions.ReadTimeout:                         # 응답 머리도 상한 안에 안 옴
        out["ended_by"] = "deadline"
    except Exception as e:                                          # noqa: BLE001
        out["ended_by"] = "exception"
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        if r is not None:
            _shutdown(r)                                           # 읽는 스레드를 먼저 풀어 줌 — 안 그러면 close 가 스트림 끝까지 막힘
            try:
                r.close()                                          # 연결을 닫으면 서버도 그 턴을 취소
            except Exception:                                      # noqa: BLE001
                pass
        out["elapsed"] = round(time.monotonic() - t0, 3)
    if out["ended_by"] == "deadline":
        out["error"] = f"응답 대기 상한 {limit:g}초 초과"
        if cancel_on_deadline:
            out["cancel"] = cancel(tid, base)
    return out


def _shutdown(r):
    """다른 스레드가 읽고 있는 응답 소켓을 끊음. 서버가 이벤트를 계속 보내는 중이면 r.close() 는 읽는 스레드가 쥔
    버퍼 잠금 때문에 스트림이 끝날 때까지 기다리므로, 소켓을 먼저 shutdown 해서 읽기를 끝냄 (urllib3 2.3+ 는 shutdown())"""
    import socket
    raw = getattr(r, "raw", None)
    try:
        if hasattr(raw, "shutdown"):
            raw.shutdown()
            return
    except Exception:                                              # noqa: BLE001
        pass
    for get in (lambda: raw._connection.sock, lambda: raw._fp.fp.raw._sock):
        try:
            s = get()
            if s is not None:
                s.shutdown(socket.SHUT_RDWR)
                return
        except Exception:                                          # noqa: BLE001
            continue


def cancel(thread_id, base=None, timeout=5, retries=1):
    """대기 상한을 넘긴 턴을 서버에서도 멈춤(연결 종료로 이미 취소되지만 한 번 더). 실패하면 1초 뒤 한 번 더 — 결과는 로그에 남김"""
    import requests
    out = None
    for i in range(retries + 1):
        try:
            r = requests.post(f"{(base or BASE).rstrip('/')}/api/chat/cancel", json={"thread_id": thread_id}, timeout=timeout)
            out = r.status_code
            if r.status_code < 400:
                return out
        except Exception as e:                                      # noqa: BLE001
            out = f"{type(e).__name__}"
        if i < retries:
            time.sleep(1)
    return out


# ---------------------------------------------------------------- 이벤트 묶기 (공간오케 jsonapi.gather 와 같은 규칙)
def gather(events):
    """이벤트 흐름 → 한 덩어리. 호출은 시작 순서를 지키고, 끝난 호출에 원본 payload 가 붙음.

    {thread_id, status, answer, stop_reason, usage, finished(bool), asks, calls, actions, workflow,
     receipts, answer_check, errors}
      calls[]  = {call_id, callee, tool, args, status, reason_code, elapsed_ms, payload, error, replayed}
      actions  = 'actions' 이벤트(items) 를 받은 순서대로 — 없으면 각 호출 payload.actions 로 채움"""
    calls, order, asks, errors, answer = {}, [], [], [], []
    finished, workflow, receipts, answer_check = {}, {}, None, None
    action_events = []
    for name, data in events:
        if not isinstance(data, dict):
            continue
        call_id = str(data.get("call_id") or "")
        if name == "call.started" and call_id:
            if call_id not in calls:
                order.append(call_id)
            calls[call_id] = {"call_id": call_id, "callee": data.get("callee"), "tool": data.get("tool"),
                              "args": data.get("args") or {}, "status": "in_flight", "payload": None}
        elif name == "call.finished" and call_id:
            if call_id not in calls:
                order.append(call_id)
                calls[call_id] = {"call_id": call_id}
            calls[call_id].update({k: data.get(k) for k in
                                   ("callee", "tool", "status", "reason_code", "elapsed_ms", "payload", "error", "replayed")
                                   if k in data or k not in calls[call_id]})
            calls[call_id].setdefault("args", data.get("args") or {})
        elif name == "actions":
            for item in data.get("items") or []:
                if isinstance(item, dict):
                    action_events.append({**item, "_call_id": data.get("call_id")})
        elif name == "ask":
            asks.append(data)
        elif name == "answer.delta":
            answer.append(str(data.get("delta") or data.get("text") or ""))
        elif name == "workflow":
            workflow = data
        elif name == "receipts":
            receipts = data
        elif name == "answer_check":
            answer_check = data
        elif name == "turn.finished":
            finished = data
        elif name == "error":
            errors.append(data)
    rows = [calls[c] for c in order]
    actions = action_events
    if not actions:                                                # actions 이벤트가 없는 배포 → 호출별 payload.actions
        for row in rows:
            pay = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            for item in pay.get("actions") or []:
                if isinstance(item, dict) and item not in actions:
                    actions.append({**item, "_call_id": row.get("call_id")})
    return {
        "thread_id": finished.get("thread_id"),
        "status": finished.get("status"),
        "answer": finished.get("answer") or "".join(answer),
        "stop_reason": finished.get("stop_reason") or "",
        "usage": finished.get("usage") or {},
        "finished": bool(finished),
        "asks": asks,
        "calls": rows,
        "actions": actions,
        "workflow": workflow,
        "receipts": receipts,
        "answer_check": answer_check,
        "errors": errors,
    }


# ---------------------------------------------------------------- 사전 점검 · 버전
def _get(url, timeout=8, headers=None):
    import requests
    t0 = time.monotonic()
    try:
        r = requests.get(url, timeout=timeout, headers=headers or {})
        ms = round((time.monotonic() - t0) * 1000)
        body = None
        try:
            body = r.json()
        except ValueError:
            body = r.text[:200]
        return dict(ok=r.status_code == 200, status=r.status_code, ms=ms, body=body)
    except Exception as e:                                          # noqa: BLE001
        return dict(ok=False, status=None, ms=round((time.monotonic() - t0) * 1000), body=f"{type(e).__name__}: {str(e)[:200]}")


def _call_err(c):
    """호출 실패 메시지 한 줄 (payload.errors[].message 또는 error)"""
    pay = c.get("payload") if isinstance(c.get("payload"), dict) else {}
    e = pay.get("errors") or c.get("error") or ""
    if isinstance(e, list):
        e = "; ".join(str(x.get("message") or x) if isinstance(x, dict) else str(x) for x in e)
    return str(e)[:120]


def precheck(base=None, checks=None, probe_message=None, deadline=120, llm_key=None):
    """실행 전 점검 — [{항목, 주소, 결과(정상/실패), 응답(ms), 내용}].
    probe_message 를 주면 공간오케에 실제 명령 1건을 보내 V-World 주소 검색까지 확인(위치 확정 호출이 성공했는지).
    llm_key 를 주면(없으면 환경변수) LLM 서버가 띄운 모델이 시험 조건·공간오케 설정과 같은지 확인"""
    base = (base or BASE).rstrip("/")
    items = dict(checks or CHECKS)
    if base != BASE:                                               # 화면에서 공간오케 주소를 바꾼 경우
        items["공간오케 (/health)"] = base + "/health"
        items["공간오케 준비 상태 (/ready)"] = base + "/ready"
    if checks is None:
        items["콘솔 서비스 (에이전트·MCP 가동)"] = os.environ.get("AI6_CONSOLE_URL") or _host_url(base, 18800, "/api/stack")
        items["모니터 중계 (공간오케 → 에이전트)"] = os.environ.get("AI6_RELAY_URL") or _host_url(base, 17007, "/api/status")
    rows = []
    key = llm_key_env() if llm_key is None else llm_key
    diag = None
    for name, url in items.items():
        if name.startswith("LLM"):                                 # 키가 있어야 목록을 주는 vLLM — 키가 없으면 서버 응답만 확인
            ok, detail, _, r = llm_check(url, key, diag)
            rows.append({"항목": name, "주소": url, "결과": "정상" if ok else "실패", "응답(ms)": r["ms"], "내용": detail})
            continue
        r = _get(url)
        detail = ""
        if r["ok"] and isinstance(r["body"], dict):
            if name.startswith("공간오케 (/health"):
                detail = json.dumps(r["body"], ensure_ascii=False)[:120]
                r["ok"] = str(r["body"].get("status", "")).lower() in ("ok", "healthy", "up")
            elif "준비" in name:
                diag = r["body"].get("vllm_diag") or {}
                detail = f"ready={r['body'].get('ready')} · 모델={diag.get('model', '')}"
                r["ok"] = bool(r["body"].get("ready", True))
            elif name.startswith("콘솔"):
                svc = {str(x.get("name")): x for x in r["body"].get("services") or [] if isinstance(x, dict)}
                down = [n for n in REQUIRED_SERVICES if n in svc and not svc[n].get("up")]
                gone = [n for n in REQUIRED_SERVICES if n not in svc]
                r["ok"] = not down and not gone
                detail = (f"필수 {len(REQUIRED_SERVICES)}개 가동" if r["ok"] else
                          "; ".join(x for x in (f"내려감 {', '.join(down)}" if down else "", f"목록에 없음 {', '.join(gone)}" if gone else "") if x))
            elif name.startswith("모니터 중계"):
                b = r["body"]
                relay = b.get("relay") if isinstance(b.get("relay"), dict) else {}
                bad = [a for a in RELAY_AGENTS if a in relay and (relay[a] or {}).get("status") not in (200, None)]
                core_ok = (b.get("core") or {}).get("alive") is not False
                console_ok = (b.get("console") or {}).get("ok") is not False
                r["ok"] = core_ok and console_ok and not bad
                detail = (f"코어 {'정상' if core_ok else '응답 없음'} · 콘솔 {'정상' if console_ok else '연결 안 됨'}"
                          + (f" · 중계 실패 {', '.join(bad)}" if bad else f" · 중계 {sum(1 for a in RELAY_AGENTS if a in relay)}개 정상"))
            elif name.startswith("공간분석 API"):                # 동결 버전 확인용 — OpenAPI 문서의 info.version (시스템 조회값)
                info = r["body"].get("info") if isinstance(r["body"].get("info"), dict) else {}
                ver = str(info.get("version") or "").strip()
                detail = f"{info.get('title') or 'GIS API'} 버전 {ver or '미제공'}"
                if ver and GIS_API_VERSION and ver.lstrip("vV") != GIS_API_VERSION.lstrip("vV"):   # 규격: 시험 기간 중 v0.1 고정
                    r["ok"] = False
                    detail += f" — 시험 조건({GIS_API_VERSION})과 다름"
        elif not r["ok"]:
            detail = str(r["body"])[:200]
        rows.append({"항목": name, "주소": url, "결과": "정상" if r["ok"] else "실패", "응답(ms)": r["ms"], "내용": detail})
    if probe_message:
        res = stream_chat(probe_message, base=base, deadline=deadline)
        turn = gather(res["events"])
        loc = [c for c in turn["calls"] if c.get("callee") == "map_navigation_b"]
        bad = [c for c in loc if str(c.get("status") or "").lower() != "ok"]
        ok = res["ended_by"] == "turn.finished" and bool(loc) and not bad   # 검색만 되고 필지 선택(도형 조회)이 실패해도 실패 — 2026-11-12 KGEOP 시간 초과
        why = "; ".join(f"{c.get('tool') or c.get('callee')} {c.get('status')}: {_call_err(c)}" for c in bad)
        rows.append({"항목": "주소 검색 1건 (V-World 경유)", "주소": res["url"], "결과": "정상" if ok else "실패",
                     "응답(ms)": round((res["elapsed"] or 0) * 1000),
                     "내용": (res["error"] or f"위치 확정 호출 {len(loc)}건 · 상태 {turn['status']}" + (f" — 실패 {why}" if why else ""))[:200]})
    return rows


def _pre_text(pre, prefix):
    return next((str(r.get("내용") or "") for r in pre or [] if str(r.get("항목") or "").startswith(prefix)), "")


def version_rows(pre=None, ready=None, llm=None):
    """규격 시험방법 (2) '동결 버전 확인' 표 — [{항목, 시험 조건, 확인값, 확인 방법, 결과}]. 네트워크를 쓰지 않음:
    사전 점검 행(pre) · 실행 기록의 /ready 조회값(ready) · LLM 서버 확인값(llm) 으로 만듦.
    결과 = 일치 / 불일치 / 확인 못 함 / 선언값(시스템이 알려 주지 않아 의뢰기업 선언값으로 기록)"""
    ready, llm = ready or {}, llm or {}
    want = dict(SPEC_VERSIONS)
    rows = []

    def add(name, got, how, ok):
        rows.append({"항목": name, "시험 조건": want[name], "확인값": got or "-", "확인 방법": how,
                     "결과": "확인 못 함" if ok is None else ("일치" if ok is True else "불일치" if ok is False else ok)})
    served = [m for m in llm.get("모델") or [] if m]
    llm_row = next((r for r in pre or [] if str(r.get("항목") or "").startswith("LLM")), None)
    llm_bad = bool(llm_row) and llm_row.get("결과") != "정상"   # 키 오류·모델 없음 등 — 모델 목록 없이 '일치'로 보이지 않게
    m = re.search(r"모델=([^\s·]+)", _pre_text(pre, "공간오케 준비"))
    conf = ready.get("LLM 모델") or llm.get("공간오케 설정 모델") or (m.group(1) if m else "")
    if served:
        add("AI 모델", ", ".join(map(str, served)), "LLM 서버 /v1/models (LLM 키로 확인)",
            _model_name(LLM_MODEL) in {_model_name(x) for x in served})
    elif llm_bad:
        add("AI 모델", conf, "LLM 점검 실패 — 사전 점검 표의 LLM 행 참고", None)
    else:
        add("AI 모델", conf, "공간오케 /ready 설정 모델 (LLM 키가 없으면 서버의 모델 목록은 확인 못 함)",
            (_model_name(conf) == _model_name(LLM_MODEL)) if conf else None)
    v = llm.get("vLLM") if llm.get("vLLM") not in (None, "", "미확인") else None
    if not v:
        m = re.search(r"vLLM (\d+(?:\.\d+)+)", _pre_text(pre, "LLM"))
        v = m.group(1) if m else None
    add("추론 서버 (vLLM)", v, "LLM 서버 /version", (v == want["추론 서버 (vLLM)"]) if v else None)
    for name in ("지도제어·공간분석 Sub 오케스트레이터", "지도제어·공간분석 에이전트"):
        add(name, "시스템 미제공", "버전을 알려 주는 API 없음 — 의뢰기업 선언값으로 기록 (연결 에이전트 목록은 위 표)", "선언값")
    m = re.search(r"버전 (\S+)", _pre_text(pre, "공간분석 API"))
    g = m.group(1) if m and m.group(1) != "미제공" else None
    add("공간분석 API", g, "GIS API /v3/api-docs info.version",
        (g.lstrip("vV") == want["공간분석 API"].lstrip("vV")) if g else None)
    add("지도 데이터 기준일", "시스템 미제공", "기준일을 알려 주는 API 없음 — 의뢰기업 선언값으로 기록", "선언값")
    add("AI 엔진 결정성 옵션", "시스템 미제공",
        "temperature 를 알려 주는 API 없음(공간오케 /ready·응답에 없음) — 의뢰기업 선언값으로 기록", "선언값")
    return rows


def llm_info(base=None, llm_key=None, url=None):
    """실행 기록(meta)용 — LLM 서버가 띄운 모델(키가 있을 때)과 공간오케 /ready 의 설정 모델. 키는 기록하지 않음"""
    diag = {}
    r = _get((base or BASE).rstrip("/") + "/ready")
    if r["ok"] and isinstance(r["body"], dict):
        diag = r["body"].get("vllm_diag") or {}
    ok, detail, info, _ = llm_check(url or CHECKS["LLM (GPT-OSS 120B)"], llm_key_env() if llm_key is None else llm_key, diag)
    return {**info, "정상": ok, "내용": detail, "공간오케 설정 모델": diag.get("model"), "공간오케 LLM 주소": diag.get("base")}


def ready_info(base=None):
    """공간오케 /ready 에서 읽을 수 있는 실행 환경(LLM 주소·모델명·연결된 에이전트) — 버전 표에 '조회값'으로 씀"""
    r = _get((base or BASE).rstrip("/") + "/ready")
    if not (r["ok"] and isinstance(r["body"], dict)):
        return {}
    b = r["body"]
    diag = b.get("vllm_diag") or {}
    callees = [c.get("name") for c in b.get("callees") or [] if isinstance(c, dict)]
    return {"LLM 주소": diag.get("base"), "LLM 모델": diag.get("model"), "연결 에이전트": ", ".join(map(str, callees))}
