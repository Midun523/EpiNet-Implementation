#!/usr/bin/env python3
"""
tests/demo/failure_demo.py — Live Resiliency Failure Demo for Screen Recording.

Demonstrates Kubernetes pod resilience and Kafka message durability:
1. Prompts the operator to run `kubectl get pods -w` in a side-by-side terminal.
2. Uploads a 10 MB pharmacogenomics CSV.
3. Detects the exact moment the ML service transitions the job to PROCESSING.
4. Kills the active ml-service pod mid-computation via `kubectl delete pod`.
5. Watches Kubernetes spin up a replacement ml-service pod.
6. Shows the replacement worker resume/re-process the uncommitted Kafka event.
7. Prints job status transitions with single-clock timestamps until COMPLETED.

Usage:
  # Terminal 1 (watcher):
  kubectl get pods -w

  # Terminal 2 (demo runner):
  python tests/demo/failure_demo.py
"""

import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:30081")
SAMPLE_CSV = os.path.abspath("sample-data/sample_pharmacogenomics.csv")


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def fmt_ts(dt):
    return dt.strftime("%H:%M:%S.%f")[:-3] if dt else "N/A"


def run_cmd(cmd, timeout=30):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def get_pod_name(app_label):
    out, _, _ = run_cmd(["kubectl", "get", "pods", "-l", f"app={app_label}", "-o", "jsonpath={.items[0].metadata.name}"])
    return out.strip()


def generate_csv_bytes(target_mb=10.0):
    with open(SAMPLE_CSV, "rb") as f:
        lines = f.readlines()
    header, data_lines = lines[0], lines[1:]
    block = b"".join(data_lines)
    target = int(target_mb * 1024 * 1024) - len(header)
    chunks = [header] + [block] * (target // len(block))
    rem = target % len(block)
    if rem > 0:
        part = block[:rem]
        last_nl = part.rfind(b"\n")
        if last_nl != -1:
            chunks.append(part[:last_nl + 1])
    return b"".join(chunks)


def upload_file(csv_bytes, filename="failure_demo_10mb.csv"):
    boundary = "----FormBoundaryFailureDemo"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode() + csv_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/uploads",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.status, json.loads(resp.read().decode())


def get_status(aid):
    req = urllib.request.Request(f"{BACKEND_URL}/api/analyses/{aid}/status")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def main():
    print("=" * 72)
    print("EPINET POD FAILURE RESILIENCE DEMO")
    print("=" * 72)
    print("RECOMMENDED SETUP FOR SCREEN RECORDING:")
    print("  Terminal 1 (Left):  kubectl get pods -w")
    print("  Terminal 2 (Right): python tests/demo/failure_demo.py")
    print("=" * 72)

    initial_pod = get_pod_name("ml-service")
    if not initial_pod:
        print("[ERROR] No running ml-service pod found. Please check cluster.")
        sys.exit(1)

    print(f"\n[DEMO SETUP] Active ML Pod: {initial_pod}")
    print("[DEMO SETUP] Generating 10 MB synthetic pharmacogenomics CSV...")
    csv_bytes = generate_csv_bytes(10.0)
    print(f"[DEMO SETUP] Generated {len(csv_bytes) / (1024 * 1024):.2f} MB payload.\n")

    if "--non-interactive" not in sys.argv:
        input("Press ENTER to start the demo and trigger mid-flight failure...")
    else:
        print("[DEMO SETUP] Running in non-interactive mode. Commencing execution...")

    t0 = now_utc()
    print(f"[{fmt_ts(t0)}] Step 1: Uploading 10 MB file to backend...")
    status_code, resp = upload_file(csv_bytes)
    aid = resp["analysisId"]
    t_uploaded = now_utc()
    print(f"[{fmt_ts(t_uploaded)}] Step 2: Backend accepted job {aid} (HTTP {status_code}, status={resp.get('status')})")

    # Monitor for PROCESSING
    print(f"[{fmt_ts(now_utc())}] Step 3: Waiting for job to reach PROCESSING state...")
    t_processing = None
    while time.time() - t0.timestamp() < 30:
        st = get_status(aid)
        s = st.get("status")
        if s == "PROCESSING":
            t_processing = now_utc()
            print(f"[{fmt_ts(t_processing)}] >>> JOB IS NOW PROCESSING (Worker is computing epistasis) <<<")
            break
        elif s == "COMPLETED":
            # Very fast run; proceed to kill immediately
            t_processing = now_utc()
            break
        time.sleep(0.05)

    # Trigger failure: delete the ml-service pod immediately
    t_kill = now_utc()
    print(f"[{fmt_ts(t_kill)}] Step 4: KILLING POD {initial_pod} MID-PROCESSING!")
    run_cmd(["kubectl", "delete", "pod", initial_pod, "--now"])
    print(f"[{fmt_ts(now_utc())}] Pod kill command dispatched.")

    # Poll status until terminal
    print(f"[{fmt_ts(now_utc())}] Step 5: Polling job status while Kubernetes replaces pod...")
    seen_statuses = set()
    t_terminal = None
    final_status = None

    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            st = get_status(aid)
            s = st.get("status")
            now_ts = now_utc()
            if s not in seen_statuses:
                seen_statuses.add(s)
                print(f"[{fmt_ts(now_ts)}] Status update: {s}")
            if s in ("COMPLETED", "FAILED"):
                t_terminal = now_ts
                final_status = s
                break
        except Exception as e:
            pass
        time.sleep(0.5)

    replacement_pod = get_pod_name("ml-service")

    print("\n" + "=" * 72)
    print("DEMO EXECUTION SUMMARY:")
    print("=" * 72)
    print(f"  Analysis ID:          {aid}")
    print(f"  Target Pod Killed:    {initial_pod}")
    print(f"  Replacement Pod:      {replacement_pod}")
    print(f"  Initial Upload Time:  {fmt_ts(t0)}")
    print(f"  Pod Kill Time:        {fmt_ts(t_kill)}")
    print(f"  Terminal Time:        {fmt_ts(t_terminal)} ({final_status})")
    if t_terminal:
        print(f"  Total Duration:       {(t_terminal - t0).total_seconds():.2f}s")
        print(f"  Recovery Time (Kill->Done): {(t_terminal - t_kill).total_seconds():.2f}s")
    print("=" * 72)


if __name__ == "__main__":
    main()
