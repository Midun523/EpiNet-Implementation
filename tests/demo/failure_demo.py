#!/usr/bin/env python3
"""
tests/demo/failure_demo.py — Live Mid-Job Pod Failure Resilience Demo.

WHY: Demonstrates Kubernetes self-healing and Kafka message durability during
an active computation:
1. Verifies ml-service is ready with DEMO_COMPUTE_DELAY_SECONDS=10.
2. Uploads 10 MB pharmacogenomics CSV.
3. Detects exact timestamp when job transitions to PROCESSING.
4. Asserts job is NOT terminal before kill; aborts with 'kill too late' if already terminal.
5. Kills active ml-service pod mid-computation via `kubectl delete pod --now`.
6. Tracks single-clock absolute timestamps for:
   - Upload accepted
   - PROCESSING seen
   - Pod kill dispatched
   - Replacement pod created
   - Replacement pod Ready
   - Job COMPLETED
7. Waits 5 seconds after COMPLETED and prompts operator to stop recording (Win+Alt+R).
"""

import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request

# WHY: NodePort 30081 routes directly to Spring Boot backend inside Kind cluster.
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:30081")
BENCH_10MB_CSV = os.path.abspath("tests/bench/data/benchmark_10mb.csv")
SAMPLE_CSV = os.path.abspath("sample-data/sample_pharmacogenomics.csv")


def now_local():
    # WHY: Local timezone matches the Windows taskbar clock seen in screen recordings.
    return datetime.datetime.now()


def fmt_ts(dt):
    # WHY: Millisecond precision allows exact timeline correlation with k8s watch pane.
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if dt else "N/A"


def run_cmd(cmd, timeout=30):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def get_pod_details(app_label):
    # WHY: Query name, phase, and Ready condition directly from k8s jsonpath.
    out, _, _ = run_cmd([
        "kubectl", "get", "pods", "-l", f"app={app_label}",
        "-o", "jsonpath={range .items[*]}{.metadata.name},{.status.phase},{.status.conditions[?(@.type=='Ready')].status}{'\\n'}{end}"
    ])
    pods = []
    for line in out.splitlines():
        if line.strip():
            parts = line.strip().split(",")
            name = parts[0]
            phase = parts[1] if len(parts) > 1 else "Unknown"
            ready = parts[2] if len(parts) > 2 else "False"
            pods.append({"name": name, "phase": phase, "ready": (ready == "True")})
    return pods


def generate_csv_bytes(target_mb=10.0):
    # WHY: Fallback generator if pre-baked benchmark_10mb.csv is missing.
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


def run_demo_trial(trial_num=1, target_mb=10.0, delay_sec=10):
    print("\n" + "=" * 70, flush=True)
    print(f"EPINET POD RESILIENCE DEMO (10 MB Payload | {delay_sec}s Demo Delay)", flush=True)
    print("=" * 70, flush=True)

    # WHY: Check existing env to avoid unnecessary rollout that would disturb the watch pane.
    out, _, _ = run_cmd([
        "kubectl", "get", "deployment", "ml-service",
        "-o", "jsonpath={.spec.template.spec.containers[0].env[?(@.name=='DEMO_COMPUTE_DELAY_SECONDS')].value}"
    ])
    if out != str(delay_sec):
        print(f"Applying DEMO_COMPUTE_DELAY_SECONDS={delay_sec} on ml-service...", flush=True)
        run_cmd(["kubectl", "set", "env", "deployment/ml-service", f"DEMO_COMPUTE_DELAY_SECONDS={delay_sec}"])
        run_cmd(["kubectl", "rollout", "status", "deployment/ml-service", "--timeout=60s"])

    pods = get_pod_details("ml-service")
    ready_pods = [p for p in pods if p["ready"]]
    if not ready_pods:
        raise RuntimeError("Precondition failed: No ready ml-service pod found before demo start.")
    target_pod = ready_pods[0]["name"]
    print(f"Active ml-service pod target: {target_pod}", flush=True)

    # Load 10MB test dataset
    if os.path.exists(BENCH_10MB_CSV):
        print(f"Loading {BENCH_10MB_CSV}...", flush=True)
        with open(BENCH_10MB_CSV, "rb") as f:
            csv_bytes = f.read()
    else:
        print(f"Generating {target_mb} MB CSV dataset...", flush=True)
        csv_bytes = generate_csv_bytes(target_mb)

    # 1. Upload CSV
    t_upload_start = now_local()
    print(f"[{fmt_ts(t_upload_start)}] Uploading CSV ({len(csv_bytes)/(1024*1024):.1f} MB) to backend...", flush=True)
    status_code, resp = upload_file(csv_bytes, f"failure_demo_trial{trial_num}.csv")
    t_upload_accepted = now_local()
    aid = resp["analysisId"]
    print(f"[{fmt_ts(t_upload_accepted)}] Upload accepted: analysis ID {aid} (HTTP {status_code}, status={resp.get('status')})", flush=True)

    # 2. Poll rapidly for PROCESSING
    # WHY: 50ms polling interval ensures we catch the exact start of processing.
    t_processing_seen = None
    deadline = time.time() + 45
    while time.time() < deadline:
        st = get_status(aid)
        s = st.get("status")
        if s == "PROCESSING":
            t_processing_seen = now_local()
            print(f"[{fmt_ts(t_processing_seen)}] PROCESSING seen: ml-service began computing (10s delay window active)", flush=True)
            break
        elif s in ("COMPLETED", "FAILED"):
            # WHY: Requirement 3c: must fail if job already terminal before kill
            raise RuntimeError(f"kill too late: job is already terminal ({s}) before PROCESSING intercepted!")
        time.sleep(0.05)

    if not t_processing_seen:
        raise TimeoutError("Timed out waiting for job to enter PROCESSING state.")

    # 3. Assert job is NOT terminal immediately before dispatching kill
    # WHY: Requirement 3c: strictly verify kill is executed mid-computation.
    st_check = get_status(aid)
    if st_check.get("status") in ("COMPLETED", "FAILED"):
        raise RuntimeError(f"kill too late: job is already terminal ({st_check.get('status')}) at kill time!")

    # 4. Dispatch Pod Kill
    t_kill = now_local()
    print(f"[{fmt_ts(t_kill)}] Pod kill: kubectl delete pod {target_pod} --now", flush=True)
    run_cmd(["kubectl", "delete", "pod", target_pod, "--now"])
    # WHY: Explain pod watch status in the recording so viewer understands the 'Error' status is from SIGKILL/delete.
    print("[NOTE] Old pod killed by kubectl delete; status Error is expected in pod watch pane.", flush=True)

    # 5. Track replacement pod creation and Ready status
    t_pod_created = None
    t_pod_ready = None
    replacement_pod_name = None

    t0_track = time.time()
    while time.time() - t0_track < 60:
        current_pods = get_pod_details("ml-service")
        for p in current_pods:
            if p["name"] != target_pod:
                if not t_pod_created:
                    t_pod_created = now_local()
                    replacement_pod_name = p["name"]
                    print(f"[{fmt_ts(t_pod_created)}] Replacement pod created: {replacement_pod_name} (phase: {p['phase']})", flush=True)
                if p["ready"] and not t_pod_ready:
                    t_pod_ready = now_local()
                    print(f"[{fmt_ts(t_pod_ready)}] Replacement pod Ready: {replacement_pod_name}", flush=True)
                    break
        if t_pod_ready:
            break
        time.sleep(0.1)

    # 6. Poll until job completes
    # WHY: Kafka durability ensures the new pod consumes the uncommitted offset and finishes the job.
    t_completed = None
    final_status = None
    while time.time() - t0_track < 90:
        try:
            st = get_status(aid)
            s = st.get("status")
            if s in ("COMPLETED", "FAILED"):
                t_completed = now_local()
                final_status = s
                print(f"[{fmt_ts(t_completed)}] Job COMPLETED: analysis {aid} marked COMPLETED", flush=True)
                break
        except Exception:
            pass
        time.sleep(0.2)

    if final_status != "COMPLETED":
        raise RuntimeError(f"Demo failed: final status is {final_status}, expected COMPLETED.")

    dur_kill_to_created = (t_pod_created - t_kill).total_seconds() if t_pod_created else None
    dur_kill_to_ready = (t_pod_ready - t_kill).total_seconds() if t_pod_ready else None
    dur_kill_to_completed = (t_completed - t_kill).total_seconds() if t_completed else None
    dur_total = (t_completed - t_upload_start).total_seconds() if t_completed else None

    print("\n" + "=" * 65, flush=True)
    print("TIMESTAMPS SUMMARY (Real Local Time):", flush=True)
    print(f"  Upload accepted:         {fmt_ts(t_upload_accepted)}", flush=True)
    print(f"  PROCESSING seen:         {fmt_ts(t_processing_seen)}", flush=True)
    print(f"  Pod kill:                {fmt_ts(t_kill)}", flush=True)
    print(f"  Replacement pod created: {fmt_ts(t_pod_created)} (+{dur_kill_to_created:.2f}s from kill)", flush=True)
    print(f"  Replacement pod Ready:   {fmt_ts(t_pod_ready)} (+{dur_kill_to_ready:.2f}s from kill)", flush=True)
    print(f"  Job COMPLETED:           {fmt_ts(t_completed)} (+{dur_kill_to_completed:.2f}s from kill)", flush=True)
    print(f"  Total Duration:          {dur_total:.2f}s", flush=True)
    print("=" * 65, flush=True)

    # WHY: Requirement 3d: wait 5s after COMPLETED, then print stop prompt.
    print("\n[DEMO] Mid-job kill verified: Replacement pod resumed computation cleanly.", flush=True)
    print("[DEMO] Waiting 5 seconds before stop prompt...", flush=True)
    time.sleep(5)
    print("\n" + "*" * 54, flush=True)
    print(">>> Press Win+Alt+R to stop recording. <<<", flush=True)
    print("*" * 54 + "\n", flush=True)

    return {
        "aid": aid,
        "target_pod": target_pod,
        "replacement_pod": replacement_pod_name,
        "ts_upload_accepted": fmt_ts(t_upload_accepted),
        "ts_processing_seen": fmt_ts(t_processing_seen),
        "ts_kill": fmt_ts(t_kill),
        "ts_pod_created": fmt_ts(t_pod_created),
        "ts_pod_ready": fmt_ts(t_pod_ready),
        "ts_completed": fmt_ts(t_completed),
        "dur_kill_to_created": dur_kill_to_created,
        "dur_kill_to_ready": dur_kill_to_ready,
        "dur_kill_to_completed": dur_kill_to_completed,
        "dur_total": dur_total,
        "status": final_status
    }


def main():
    # WHY: Single clean trial for screen-recording session.
    run_demo_trial(trial_num=1, target_mb=10.0, delay_sec=10)


if __name__ == "__main__":
    main()
