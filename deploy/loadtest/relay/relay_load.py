"""SSE 릴레이 부하 측정 — LLM 없이 릴레이만 잰다.

- 가짜 생산자: job 마다 delta 를 일정 간격으로 XADD(+PUBLISH)하고 마지막에 done.
  rag-service 와 같은 키·이벤트 모양을 쓴다. data 에 순번(seq)과 발행 시각(ts, ns)을 넣는다.
- 부하 클라이언트: resume 엔드포인트로 job 마다 SSE 연결을 하나씩 연다.
  수신 시각 - 발행 시각 = 릴레이 지연. 받은 seq 로 유실·중복을 검사한다.

생산자와 클라이언트는 같은 컨테이너에서 돌아 시계가 같다. 클라이언트는 여러 프로세스로 나눈다
(파이썬 한 프로세스가 수천 개 스트림을 파싱하면 클라이언트 쪽 지연이 섞인다).

역할(role):
  normal    끝까지 받는다
  drop      delta 를 cut 개 받은 뒤 끊고 다시 붙지 않는다 (L2)
  slow      청크마다 일부러 늦게 읽는다 (L3)
  reconnect delta 를 cut 개 받은 뒤 끊고 Last-Event-ID 로 다시 붙는다 (L4)

--retry-like-frontend: 연결 실패·비정상 종료 시 프론트(resumeUntilDone)처럼 1·2·4·8·16초 백오프로
Last-Event-ID 를 붙여 다시 붙는다. 서버 재기동(L6) 측정용.
"""
import argparse
import asyncio
import json
import multiprocessing as mp
import os
import random
import time

import httpx
import redis.asyncio as aioredis


def stream_key(job: str) -> str:
    return f"chat:answer:stream:{job}"


def channel_key(job: str) -> str:
    return f"chat:answer:live:{job}"


# ---------------------------------------------------------------- 생산자

async def _produce(jobs: list[str], deltas: int, interval: float, start_at: float, groups: int) -> None:
    r = aioredis.Redis(host=os.environ["REDIS_HOST"], password=os.environ["REDIS_PASSWORD"])
    await asyncio.sleep(max(0.0, start_at - time.time()))

    async def run_group(g: int, gjobs: list[str]) -> None:
        # 그룹마다 시작을 어긋나게 해 모든 job 이 같은 순간에 몰리지 않게 한다
        await asyncio.sleep(interval * g / groups)
        for i in range(deltas + 1):
            t0 = time.monotonic()
            pipe = r.pipeline(transaction=False)
            for job in gjobs:
                if i < deltas:
                    data = {"text": "x", "seq": i, "ts": time.time_ns()}
                    pipe.xadd(stream_key(job), {"event": "delta", "data": json.dumps(data)})
                else:
                    data = {"messageId": 0, "answerStatus": "OK", "modelName": "loadtest", "latencyMs": 0,
                            "suggestions": [], "resumeAt": None, "ts": time.time_ns()}
                    pipe.xadd(stream_key(job), {"event": "done", "data": json.dumps(data)})
                    pipe.expire(stream_key(job), 300)
                # POLL 모드에서는 구독자가 없어 무해하다. 두 모드를 같은 생산자 코드로 잰다
                pipe.publish(channel_key(job), "1")
            await pipe.execute()
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - t0)))

    buckets = [jobs[g::groups] for g in range(groups)]
    await asyncio.gather(*(run_group(g, b) for g, b in enumerate(buckets) if b))
    await r.aclose()


def producer_main(jobs, deltas, interval, start_at, groups):
    asyncio.run(_produce(jobs, deltas, interval, start_at, groups))


# ---------------------------------------------------------------- 클라이언트

async def _consume(client: httpx.AsyncClient, args, job: str, role: str) -> dict:
    url = f"{args.base}/api/conversations/{args.conv}/jobs/{job}/stream"
    res = {"job": job, "role": role, "status": None, "error": None, "done": False,
           "seqs": [], "lat": [], "attempts": 0, "connect_ms": None}
    last_id = ""
    cut = random.randint(args.deltas // 5, args.deltas // 2)
    cut_done = False
    backoff = [1, 2, 4, 8, 16] if args.retry_like_frontend else []
    res["retries"] = 0

    async def retry() -> bool:
        if res["retries"] >= len(backoff):
            return False
        await asyncio.sleep(backoff[res["retries"]])
        res["retries"] += 1
        res["error"] = None
        return True

    while True:
        res["attempts"] += 1
        headers = {"Authorization": f"Bearer {args.token}", "Accept": "text/event-stream"}
        if last_id:
            headers["Last-Event-ID"] = last_id
        cut_now = False
        t0 = time.monotonic()
        try:
            async with client.stream("GET", url, headers=headers) as resp:
                res["status"] = resp.status_code
                if res["connect_ms"] is None:
                    res["connect_ms"] = (time.monotonic() - t0) * 1000
                if resp.status_code != 200:
                    body = (await resp.aread())[:200].decode("utf-8", "replace")
                    res["error"] = f"http {resp.status_code} {body}"
                    if await retry():
                        continue
                    return res
                buf = ""
                async for chunk in resp.aiter_text():
                    if role == "slow":
                        await asyncio.sleep(args.slow_delay)
                    buf += chunk.replace("\r\n", "\n")
                    *frames, buf = buf.split("\n\n")
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
                        if not data:
                            continue
                        if eid:
                            last_id = eid
                        payload = json.loads("\n".join(data))
                        if name == "delta":
                            res["seqs"].append(payload["seq"])
                            res["lat"].append((time.time_ns() - payload["ts"]) / 1e6)
                        elif name == "done":
                            res["done"] = True
                        elif name == "error":
                            res["error"] = f"sse error {payload}"
                    if res["done"] or res["error"]:
                        return res
                    if role in ("drop", "reconnect") and not cut_done and len(res["seqs"]) >= cut:
                        cut_done = cut_now = True
                        res["cut_at"] = time.time()
                        break
        except (httpx.HTTPError, OSError) as e:
            res["error"] = f"{type(e).__name__}: {e}"[:200]
            if await retry():
                continue
            if not (role == "reconnect" and res["attempts"] < 4):
                return res
            await asyncio.sleep(1)
            res["error"] = None
            continue
        if cut_now and role == "reconnect":
            await asyncio.sleep(args.reconnect_delay)
            continue
        if not res["done"] and not res["error"] and not cut_now:
            res["error"] = "stream ended without done"
            if await retry():
                continue
        return res


async def _client(args, assignments: list[tuple[str, str]]) -> list[dict]:
    await asyncio.sleep(args.connect_delay)
    limits = httpx.Limits(max_connections=None, max_keepalive_connections=0)
    timeout = httpx.Timeout(connect=15.0, read=args.read_timeout, write=15.0, pool=None)
    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        return await asyncio.gather(*(_consume(client, args, j, r) for j, r in assignments))


def client_main(args, assignments):
    return asyncio.run(_client(args, assignments))


# ---------------------------------------------------------------- 집계

def pct(xs: list[float], p: float):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 1)


def summarize(results: list[dict], deltas: int) -> dict:
    by_role: dict[str, dict] = {}
    all_lat: list[float] = []
    errors: dict[str, int] = {}
    for r in results:
        s = by_role.setdefault(r["role"], {"n": 0, "done": 0, "complete": 0, "missing": 0, "dup": 0, "error": 0,
                                           "retried": 0})
        s["n"] += 1
        if r.get("retries"):
            s["retried"] += 1
        if r["error"]:
            s["error"] += 1
            key = r["error"][:80]
            errors[key] = errors.get(key, 0) + 1
        if r["done"]:
            s["done"] += 1
        seqs = r["seqs"]
        uniq = set(seqs)
        s["dup"] += len(seqs) - len(uniq)
        if r["done"]:
            s["missing"] += deltas - len(uniq)
            if len(uniq) == deltas and len(seqs) == deltas:
                s["complete"] += 1
        if r["role"] in ("normal", "slow"):
            all_lat.extend(r["lat"])
    normal_lat = [x for r in results if r["role"] == "normal" for x in r["lat"]]
    connect = [r["connect_ms"] for r in results if r["connect_ms"] is not None]
    return {
        "connections": len(results),
        "roles": by_role,
        "errors": dict(sorted(errors.items(), key=lambda kv: -kv[1])),
        "latency_ms_normal": {"p50": pct(normal_lat, .5), "p95": pct(normal_lat, .95),
                              "p99": pct(normal_lat, .99), "max": pct(normal_lat, 1.0), "samples": len(normal_lat)},
        "latency_ms_all": {"p50": pct(all_lat, .5), "p99": pct(all_lat, .99)},
        "connect_ms": {"p50": pct(connect, .5), "p99": pct(connect, .99)},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://gateway:8080")
    ap.add_argument("--token", required=True)
    ap.add_argument("--conv", type=int, required=True)
    ap.add_argument("--jobs-file", required=True)
    ap.add_argument("--deltas", type=int, default=100)
    ap.add_argument("--interval-ms", type=int, default=200)
    ap.add_argument("--drop-frac", type=float, default=0.0)
    ap.add_argument("--slow-frac", type=float, default=0.0)
    ap.add_argument("--slow-delay", type=float, default=0.2)
    ap.add_argument("--reconnect-frac", type=float, default=0.0)
    ap.add_argument("--reconnect-delay", type=float, default=1.0)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--warmup", type=float, default=None, help="연결을 연 뒤 생산 시작까지 초")
    ap.add_argument("--read-timeout", type=float, default=90.0)
    ap.add_argument("--groups", type=int, default=10)
    ap.add_argument("--retry-like-frontend", action="store_true")
    ap.add_argument("--connect-delay", type=float, default=0.0,
                    help="연결을 열기 전에 기다릴 초. 게이트웨이 키 캐시 만료 시각에 연결이 몰리게 맞출 때 쓴다")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.jobs_file) as f:
        jobs = [line.strip() for line in f if line.strip()]
    rnd = random.Random(42)
    roles = []
    for _ in jobs:
        x = rnd.random()
        if x < args.drop_frac:
            roles.append("drop")
        elif x < args.drop_frac + args.slow_frac:
            roles.append("slow")
        elif x < args.drop_frac + args.slow_frac + args.reconnect_frac:
            roles.append("reconnect")
        else:
            roles.append("normal")
    pairs = list(zip(jobs, roles))
    chunks = [pairs[i::args.procs] for i in range(args.procs)]

    warmup = args.warmup if args.warmup is not None else 5 + len(jobs) / 200
    start_at = time.time() + args.connect_delay + warmup
    t_start = time.time()
    prod = mp.Process(target=producer_main,
                      args=(jobs, args.deltas, args.interval_ms / 1000, start_at, args.groups))
    prod.start()
    with mp.Pool(args.procs) as pool:
        parts = pool.starmap(client_main, [(args, c) for c in chunks if c])
    prod.join()

    results = [r for part in parts for r in part]
    summary = summarize(results, args.deltas)
    summary["params"] = {k: v for k, v in vars(args).items() if k != "token"}
    summary["wall_s"] = round(time.time() - t_start, 1)
    summary["connect_start_epoch"] = t_start + args.connect_delay
    summary["produce_start_epoch"] = start_at
    summary["drop_cut_epochs"] = sorted(r["cut_at"] for r in results if r["role"] == "drop" and "cut_at" in r)
    with open(args.out, "w") as f:
        json.dump({"summary": summary}, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "drop_cut_epochs"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
