#!/usr/bin/env python3
"""
Load test for pool_server: simulates N concurrent GPU trainers.

Usage:
    python tests/load_test.py --url https://pool.example.com --token <TOKEN> --machines 500 --duration 60

Each virtual trainer:
  1. GET /get_number?count=<batch>
  2. Sleeps (simulating work)
  3. POST /mark_done with leases
  4. Repeats

At the end, prints:
  - Total requests, success rate, p50/p95/p99 latency
  - Rejected leases, errors
  - Whether any X was double-assigned (via local tracking)
"""
import argparse
import asyncio
import random
import time

import httpx


async def trainer_loop(
    client: httpx.AsyncClient,
    machine_id: str,
    token: str,
    batch_size: int,
    work_time: float,
    results: dict,
    stop_event: asyncio.Event,
):
    headers = {
        "Authorization": token,
        "X-Machine-Id": machine_id,
        "X-Hostname": f"load-{machine_id[:8]}",
        "X-GPU-Name": "Virtual GPU",
        "X-GPU-Count": "1",
    }

    while not stop_event.is_set():
        try:
            t0 = time.monotonic()
            resp = await client.get(
                f"/get_number?count={batch_size}",
                headers=headers,
                timeout=15.0,
            )
            lat = (time.monotonic() - t0) * 1000
            results["get_latencies"].append(lat)

            if resp.status_code != 200:
                results["errors"] += 1
                await asyncio.sleep(1)
                continue

            data = resp.json()
            if data["command"] != "work" or not data["numbers"]:
                results["waits"] += 1
                await asyncio.sleep(0.5)
                continue

            results["get_ok"] += 1
            nums = data["numbers"]
            leases = data["leases"]

            for n in nums:
                if n in results["seen_x"]:
                    results["duplicates"] += 1
                results["seen_x"].add(n)

            await asyncio.sleep(work_time + random.uniform(0, work_time * 0.3))

            t0 = time.monotonic()
            done_resp = await client.post(
                "/mark_done",
                headers=headers,
                json={"nums": nums, "leases": leases},
                timeout=15.0,
            )
            lat = (time.monotonic() - t0) * 1000
            results["done_latencies"].append(lat)

            if done_resp.status_code == 200:
                d = done_resp.json()
                results["done_ok"] += 1
                results["acked"] += d.get("count", 0)
                results["rejected"] += d.get("rejected", 0)
            else:
                results["errors"] += 1

        except Exception as e:
            results["errors"] += 1
            results["exceptions"].append(str(e)[:100])
            await asyncio.sleep(1)


async def main():
    parser = argparse.ArgumentParser(description="Pool server load test")
    parser.add_argument("--url", required=True, help="Pool server base URL")
    parser.add_argument("--token", required=True, help="TRAINER_AUTH_TOKEN")
    parser.add_argument("--machines", type=int, default=100, help="Number of virtual trainers")
    parser.add_argument("--duration", type=int, default=30, help="Test duration in seconds")
    parser.add_argument("--batch", type=int, default=10, help="Batch size per get_number")
    parser.add_argument("--work-time", type=float, default=0.5, help="Simulated work time per batch (seconds)")
    args = parser.parse_args()

    results = {
        "get_ok": 0,
        "done_ok": 0,
        "acked": 0,
        "rejected": 0,
        "errors": 0,
        "waits": 0,
        "duplicates": 0,
        "get_latencies": [],
        "done_latencies": [],
        "seen_x": set(),
        "exceptions": [],
    }

    stop = asyncio.Event()

    print(f"Starting load test: {args.machines} trainers, {args.duration}s, batch={args.batch}")
    print(f"Target: {args.url}")
    print()

    async with httpx.AsyncClient(base_url=args.url) as client:
        tasks = []
        for i in range(args.machines):
            mid = f"load-test-{i:05d}"
            tasks.append(
                asyncio.create_task(
                    trainer_loop(client, mid, args.token, args.batch, args.work_time, results, stop)
                )
            )

        await asyncio.sleep(args.duration)
        stop.set()

        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    total_requests = results["get_ok"] + results["done_ok"] + results["errors"] + results["waits"]

    print("=" * 60)
    print("LOAD TEST RESULTS")
    print("=" * 60)
    print(f"Duration:        {args.duration}s")
    print(f"Machines:        {args.machines}")
    print(f"Total requests:  {total_requests}")
    print(f"get_number OK:   {results['get_ok']}")
    print(f"mark_done OK:    {results['done_ok']}")
    print(f"Acked total:     {results['acked']}")
    print(f"Rejected leases: {results['rejected']}")
    print(f"Waits (empty):   {results['waits']}")
    print(f"Errors:          {results['errors']}")
    print(f"Duplicate X:     {results['duplicates']}")
    print(f"Unique X seen:   {len(results['seen_x'])}")
    print()

    if results["get_latencies"]:
        lats = sorted(results["get_latencies"])
        print(f"get_number latency (ms):")
        print(f"  p50={lats[len(lats)//2]:.1f}  p95={lats[int(len(lats)*0.95)]:.1f}  p99={lats[int(len(lats)*0.99)]:.1f}  max={lats[-1]:.1f}")

    if results["done_latencies"]:
        lats = sorted(results["done_latencies"])
        print(f"mark_done latency (ms):")
        print(f"  p50={lats[len(lats)//2]:.1f}  p95={lats[int(len(lats)*0.95)]:.1f}  p99={lats[int(len(lats)*0.99)]:.1f}  max={lats[-1]:.1f}")

    if results["exceptions"]:
        unique_exc = list(set(results["exceptions"]))[:5]
        print(f"\nSample exceptions ({len(results['exceptions'])} total):")
        for e in unique_exc:
            print(f"  {e}")

    if results["duplicates"] > 0:
        print(f"\n*** WARNING: {results['duplicates']} DUPLICATE X VALUES DETECTED ***")
    else:
        print(f"\nNo duplicate X detected — lease model holds.")


if __name__ == "__main__":
    asyncio.run(main())
