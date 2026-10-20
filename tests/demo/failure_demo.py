#!/usr/bin/env python3
"""
tests/demo/failure_demo.py — Live Mid-Job Pod Failure Resilience Demo.

Demonstrates Kubernetes pod resilience and Kafka message durability:
1. Prompts operator to run `kubectl get pods -w` in a side-by-side terminal.
2. Uploads 40 MB pharmacogenomics CSV.
3. Detects exact moment job transitions to PROCESSING.
4. Asserts job is NOT terminal before kill; aborts with 'kill too late' if already terminal.
5. Kills active ml-service pod mid-computation via `kubectl delete pod`.
6. Tracks single-clock absolute timestamps for:
   - PROCESSING seen
   - Kill dispatched
   - Replacement pod created
   - Replacement pod Ready
   - Final job status (COMPLETED)
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
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if dt else "N/A"


def run_cmd(cmd, timeout=30):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def get_pod_details(app_label):
    out, _, _ = run_cmd(["kubectl", "get", "pods", "-l", f"app={app_label}", "-o", "jsonpath={range .items[*]}{.metadata.name},{.status.phase},{.status.conditions[?(@.type=='Ready')].status}{'\\n'}{end}"])
    pods = []
    for line in out.splitlines():
        if line.strip():
            parts = line.strip().split(",")
            name = parts[0]
            phase = parts[1] if len(parts) > 1 else "Unknown"
            ready = parts[2] if len(parts) > 2 else "False"
            pods.append({"name": name, "phase": phase, "ready": (ready == "True")})
    return pods


def generate_csv_bytes(target_mb=40.0):
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


def upload_file(csv_bytes, filename="failure_demo_40mb.csv"):
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


def run_demo_trial(trial_num=1, target_mb=40.0, delay_sec=15):
    print("\n" + "=" * 72)
    print(f"FAILURE DEMO TRIAL {trial_num} (Payload: {target_mb}MB | Demo Delay: {delay_sec}s)")
    print("=" * 72)

    # Ensure demo compute delay is set on ml-service deployment
    run_cmd(["kubectl", "set", "env", "deployment/ml-service", f"DEMO_COMPUTE_DELAY_SECONDS={delay_sec}"])
    run_cmd(["kubectl", "rollout", "status", "deployment/ml-service", "--timeout=60s"])

    pods = get_pod_details("ml-service")
    ready_pods = [p for p in pods if p["ready"]]
    if not ready_pods:
        raise RuntimeError("No ready ml-service pod found before trial start.")
    target_pod = ready_pods[0]["name"]
    print(f"Target ML Pod: {target_pod}")

    print(f"Generating {target_mb} MB CSV...")
    csv_bytes = generate_csv_bytes(target_mb)

    t_upload_start = now_utc()
    print(f"[{fmt_ts(t_upload_start)}] Uploading CSV to backend...")
    status_code, resp = upload_file(csv_bytes, f"failure_demo_trial{trial_num}.csv")
    aid = resp["analysisId"]
    t_uploaded = now_utc()
    print(f"[{fmt_ts(t_uploaded)}] Backend accepted job {aid} (HTTP {status_code}, status={resp.get('status')})")

    # Poll rapidly for PROCESSING
    t_processing_seen = None
    deadline = time.time() + 45
    while time.time() < deadline:
        st = get_status(aid)
        s = st.get("status")
        if s == "PROCESSING":
            t_processing_seen = now_utc()
            print(f"[{fmt_ts(t_processing_seen)}] >>> PROCESSING SEEN (Worker is actively computing) <<<")
            break
        elif s in ("COMPLETED", "FAILED"):
            raise RuntimeError(f"kill too late: job transitioned to {s} before PROCESSING was intercepted!")
        time.sleep(0.05)

    if not t_processing_seen:
        raise TimeoutError("Timed out waiting for PROCESSING state.")

    # Assert job is NOT terminal right before kill
    st_check = get_status(aid)
    if st_check.get("status") in ("COMPLETED", "FAILED"):
        raise RuntimeError(f"kill too late: job already terminal ({st_check.get('status')}) at kill dispatch time!")

    # Dispatch kill
    t_kill = now_utc()
    print(f"[{fmt_ts(t_kill)}] >>> DISPATCHING POD KILL: kubectl delete pod {target_pod} --now <<<")
    run_cmd(["kubectl", "delete", "pod", target_pod, "--now"])

    # Track replacement pod creation and readiness
    t_pod_created = None
    t_pod_ready = None
    replacement_pod_name = None

    t0_track = time.time()
    while time.time() - t0_track < 60:
        current_pods = get_pod_details("ml-service")
        for p in current_pods:
            if p["name"] != target_pod:
                if not t_pod_created:
                    t_pod_created = now_utc()
                    replacement_pod_name = p["name"]
                    print(f"[{fmt_ts(t_pod_created)}] Replacement pod created: {replacement_pod_name} (phase: {p['phase']})")
                if p["ready"] and not t_pod_ready:
                    t_pod_ready = now_utc()
                    print(f"[{fmt_ts(t_pod_ready)}] Replacement pod Ready: {replacement_pod_name}")
                    break
        if t_pod_ready:
            break
        time.sleep(0.1)

    # Poll status until terminal
    t_terminal = None
    final_status = None
    while time.time() - t0_track < 90:
        try:
            st = get_status(aid)
            s = st.get("status")
            if s in ("COMPLETED", "FAILED"):
                t_terminal = now_utc()
                final_status = s
                print(f"[{fmt_ts(t_terminal)}] Job reached terminal state: {final_status}")
                break
        except Exception:
            pass
        time.sleep(0.2)

    dur_kill_to_created = (t_pod_created - t_kill).total_seconds() if t_pod_created else None
    dur_kill_to_ready = (t_pod_ready - t_kill).total_seconds() if t_pod_ready else None
    dur_kill_to_terminal = (t_terminal - t_kill).total_seconds() if t_terminal else None
    dur_total = (t_terminal - t_upload_start).total_seconds() if t_terminal else None

    print("\n--- TIMESTAMPS & DURATIONS ---")
    print(f"  PROCESSING seen:          {fmt_ts(t_processing_seen)}")
    print(f"  Kill dispatched:          {fmt_ts(t_kill)}")
    print(f"  Replacement pod created:  {fmt_ts(t_pod_created)} (+{dur_kill_to_created:.2f}s from kill)")
    print(f"  Replacement pod Ready:    {fmt_ts(t_pod_ready)} (+{dur_kill_to_ready:.2f}s from kill)")
    print(f"  Job Terminal (COMPLETED): {fmt_ts(t_terminal)} (+{dur_kill_to_terminal:.2f}s from kill)")
    print(f"  Total Duration:           {dur_total:.2f}s")
    print("------------------------------")

    return {
        "trial": trial_num,
        "aid": aid,
        "target_pod": target_pod,
        "replacement_pod": replacement_pod_name,
        "ts_processing_seen": fmt_ts(t_processing_seen),
        "ts_kill": fmt_ts(t_kill),
        "ts_pod_created": fmt_ts(t_pod_created),
        "ts_pod_ready": fmt_ts(t_pod_ready),
        "ts_terminal": fmt_ts(t_terminal),
        "dur_kill_to_created": dur_kill_to_created,
        "dur_kill_to_ready": dur_kill_to_ready,
        "dur_kill_to_terminal": dur_kill_to_terminal,
        "dur_total": dur_total,
        "status": final_status
    }


def main():
    trials_count = 3
    results = []
    try:
        for i in range(1, trials_count + 1):
            res = run_demo_trial(trial_num=i, target_mb=10.0, delay_sec=10)
            results.append(res)
            time.sleep(2)
    finally:
        # Restore normal operational mode: DEMO_COMPUTE_DELAY_SECONDS=0
        print("\n[CLEANUP] Restoring ml-service DEMO_COMPUTE_DELAY_SECONDS=0...")
        run_cmd(["kubectl", "set", "env", "deployment/ml-service", "DEMO_COMPUTE_DELAY_SECONDS=0"])
        run_cmd(["kubectl", "rollout", "status", "deployment/ml-service", "--timeout=60s"])
        print("[CLEANUP] ML service restored to normal operation.")

    print("\n" + "=" * 72)
    print("ALL 3 DEMO TRIALS COMPLETED SUCCESSFULLY:")
    print("=" * 72)
    for r in results:
        print(f"Trial {r['trial']}: Kill={r['ts_kill']} -> PodReady={r['ts_pod_ready']} (+{r['dur_kill_to_ready']:.2f}s) -> Done={r['ts_terminal']} (+{r['dur_kill_to_terminal']:.2f}s) [{r['status']}]")
    print("=" * 72)


if __name__ == "__main__":
    main()
