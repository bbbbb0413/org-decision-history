"""실제 LLM 으로 답변 SSE 계약을 확인한다 (릴레이 변경 시 회귀 확인용).

1. 질문 → conversation · sources · delta… · done 순서
2. delta 몇 개를 받은 뒤 끊고 Last-Event-ID 로 이어받기 → 받은 글자를 이어 붙인 것이 저장된 답변과 같다
3. 끝난 job 을 다시 요청 → done 한 번

질문 끝에 시각을 붙여 답변 캐시를 피한다 — 캐시가 맞으면 delta 가 1개라 이어받기를 시험하지 못한다.

    docker run --rm --network org-decision-history_backend -v "$PWD/deploy/loadtest/relay:/lt:ro" \
        relay-loadtest:1 python /lt/sse_e2e.py
"""
import asyncio
import json
import sys
import time

import httpx

BASE = "http://gateway:8080"
QUESTION = "사내 데이터를 외부로 반출하려면 어떤 승인 절차를 거쳐야 하나요?"


def parse(buf: str):
    *frames, rest = buf.replace("\r\n", "\n").split("\n\n")
    events = []
    for frame in frames:
        name, eid, data = "message", None, []
        for line in frame.split("\n"):
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("id:"):
                eid = line[3:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            events.append((name, eid, json.loads("\n".join(data))))
    return events, rest


async def read_events(resp, stop_after_deltas=None):
    buf, out, deltas = "", [], 0
    async for chunk in resp.aiter_text():
        events, buf = parse(buf + chunk)
        for e in events:
            out.append(e)
            if e[0] == "delta":
                deltas += 1
            if e[0] in ("done", "error"):
                return out, True
            if stop_after_deltas and deltas >= stop_after_deltas:
                return out, False
    return out, False


async def main() -> int:
    failures = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as c:
        token = (await c.post(f"{BASE}/api/auth/login",
                              json={"email": "member@company.com", "password": "password123"})).json()["accessToken"]
        h = {"Authorization": f"Bearer {token}"}
        conv = (await c.post(f"{BASE}/api/conversations", headers=h)).json()["id"]
        try:
            # 1·2: 질문을 보내고 delta 3개 뒤 끊는다
            async with c.stream("POST", f"{BASE}/api/conversations/{conv}/messages",
                                headers={**h, "Accept": "text/event-stream"}, json={"content": f"{QUESTION} (확인 {int(time.time())})"}) as r:
                first, finished = await read_events(r, stop_after_deltas=3)
            names = [e[0] for e in first]
            job_key = first[0][2]["jobKey"] if names and names[0] == "conversation" else None
            print("1차 이벤트:", names)
            if names[:1] != ["conversation"] or "sources" not in names:
                failures.append(f"순서가 다르다: {names}")
            if not first[0][1] is None:
                failures.append("conversation 이벤트에 id 가 있다(Stream 레코드가 아니어야 한다)")

            text = "".join(e[2]["text"] for e in first if e[0] == "delta")
            last_id = next((e[1] for e in reversed(first) if e[1]), "")
            all_events = list(first)
            if not finished:
                async with c.stream("GET", f"{BASE}/api/conversations/{conv}/jobs/{job_key}/stream",
                                    headers={**h, "Accept": "text/event-stream", "Last-Event-ID": last_id}) as r:
                    second, finished = await read_events(r)
                print("이어받기 이벤트 수:", len(second), "마지막:", second[-1][0] if second else None)
                all_events += second
                text += "".join(e[2]["text"] for e in second if e[0] == "delta")
                ids = [e[1] for e in all_events if e[1]]
                if len(ids) != len(set(ids)):
                    failures.append("이어받기에서 같은 레코드를 두 번 받았다")
            if not finished or all_events[-1][0] != "done":
                failures.append(f"done 으로 끝나지 않았다: {all_events[-1][0] if all_events else None}")
            else:
                print("done:", all_events[-1][2].get("answerStatus"), "delta 수:",
                      sum(1 for e in all_events if e[0] == "delta"))

            messages = (await c.get(f"{BASE}/api/conversations/{conv}/messages", headers=h)).json()
            stored = next((m["content"] for m in reversed(messages) if m.get("role") == "ASSISTANT"), None)
            if stored is None:
                failures.append(f"저장된 답변이 없다: {[m.get('role') for m in messages]}")
            elif stored.strip() != text.strip():
                failures.append(f"이어 붙인 글자가 저장본과 다르다 (받음 {len(text)}자, 저장 {len(stored)}자)")
            else:
                print("저장본과 일치:", len(stored), "자")

            # 3: 끝난 job 재요청
            async with c.stream("GET", f"{BASE}/api/conversations/{conv}/jobs/{job_key}/stream",
                                headers={**h, "Accept": "text/event-stream"}) as r:
                again, _ = await read_events(r)
            print("끝난 job 재요청:", [e[0] for e in again])
            if [e[0] for e in again] != ["done"]:
                failures.append(f"끝난 job 재요청이 done 한 번이 아니다: {[e[0] for e in again]}")
        finally:
            await c.delete(f"{BASE}/api/conversations/{conv}", headers=h)

    print("결과:", "PASS" if not failures else "FAIL")
    for f in failures:
        print(" -", f)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
