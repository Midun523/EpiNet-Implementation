"""
Timeline test for ML pod recovery with single replica guarantee.
Guarantees 0 HPA, 1 ML replica, 1 backend replica before each trial.
Logs all events using a single clock (UTC ISO-8601).
"""

import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

sys.stdout.reconfigure(line_buffering=True)

BACKEND_URL = "http://localhost:30081"
SAMPLE_CSV = os.path.abspath("sample-data/sample_pharmacogenomics.csv")


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def fmt_ts(dt):
    if dt is None:
        return "N/A"
    return dt.isoformat()


def run_cmd(cmd, timeout=30):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


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


def upload_file(csv_bytes, filename="recovery_trial.csv"):
    boundary = "----FormBoundaryPodResilience"
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


def get_status(analysis_id):
    req = urllib.request.Request(f"{BACKEND_URL}/api/analyses/{analysis_id}/status")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def get_ml_pods():
    out, _, rc = run_cmd(["kubectl", "get", "pods", "-l", "app=ml-service", "-o", "json"])
    if rc != 0 or not out:
        return []
    data = json.loads(out)
    return data.get("items", [])


def ensure_single_replica_and_print_state(trial_idx):
    print(f"\n=======================================================")
    print(f"PRE-TRIAL {trial_idx} REPLICA AUDIT & CLUSTER STATE")
    print(f"=======================================================")
    # 1. Print HPA
    stdout, stderr, _ = run_cmd(["kubectl", "get", "hpa"])
    print("[COMMAND] kubectl get hpa:")
    print(stdout if stdout else "No HPA found.")
    if "ml-service-hpa" in stdout:
        print("WARNING: HPA found! Deleting ml-service-hpa...")
        run_cmd(["kubectl", "delete", "hpa", "ml-service-hpa"])

    # 2. Print pods
    stdout, _, _ = run_cmd(["kubectl", "get", "pods"])
    print("\n[COMMAND] kubectl get pods:")
    print(stdout)

    # 3. Ensure exact replica counts
    run_cmd(["kubectl", "scale", "deployment", "ml-service", "--replicas=1"])
    run_cmd(["kubectl", "scale", "deployment", "backend", "--replicas=1"])
    run_cmd(["kubectl", "wait", "--for=condition=Ready", "pod", "-l", "app=backend", "--timeout=60s"])
    run_cmd(["kubectl", "wait", "--for=condition=Ready", "pod", "-l", "app=ml-service", "--timeout=60s"])

    # Double check ml pods count
    pods = get_ml_pods()
    running_pods = [p for p in pods if p.get("status", {}).get("phase") in ("Running", "Pending")]
    print(f"\n[AUDIT] Active ml-service pods count: {len(running_pods)}")
    for p in running_pods:
        print(f"  - {p['metadata']['name']} (Phase: {p['status'].get('phase')})")
    assert len(running_pods) == 1, f"Expected exactly 1 ML pod, found {len(running_pods)}"


def run_one_recovery_trial(trial_idx, csv_10mb):
    ensure_single_replica_and_print_state(trial_idx)

    initial_pod = get_ml_pods()[0]["metadata"]["name"]
    timeline = []

    def record(event_name, dt, details=""):
        timeline.append({"event": event_name, "time": dt, "details": details})
        print(f"[{fmt_ts(dt)}] EVENT: {event_name:<25} | {details}")

    # 1. Upload
    t_upload_start = now_utc()
    record("upload_started", t_upload_start, f"File size {len(csv_10mb)/(1024*1024):.2f} MB")
    status_code, resp = upload_file(csv_10mb, f"recovery_trial_{trial_idx}.csv")
    t_upload_end = now_utc()
    analysis_id = resp["analysisId"]
    record("upload_finished", t_upload_end, f"analysisId={analysis_id}, HTTP {status_code}")

    # 2. Wait for PROCESSING
    last_status = None
    t_wait_start = time.time()
    while time.time() - t_wait_start < 30:
        try:
            st = get_status(analysis_id)
            current_st = st.get("status")
            if current_st != last_status:
                last_status = current_st
                record(f"status_{current_st.lower()}", now_utc(), f"Job status -> {current_st}")
            if current_st == "PROCESSING":
                break
        except Exception as e:
            pass
        time.sleep(0.05)

    if last_status != "PROCESSING":
        print(f"ERROR: Job did not reach PROCESSING state within 30s! Current: {last_status}")
        return None

    # 3. Kill the pod immediately
    t_kill = now_utc()
    record("kill_issued", t_kill, f"Deleting active ML pod {initial_pod}")
    run_cmd(["kubectl", "delete", "pod", initial_pod, "--now"])

    # 4. Monitor replacement pod & job terminal status concurrently
    replacement_pod_name = None
    t_pod_created = None
    t_container_started = None
    t_pod_ready = None
    t_terminal = None
    terminal_status = None
    error_msg = None

    t_loop_start = time.time()
    while time.time() - t_loop_start < 120:
        t_now = now_utc()
        # Check replacement pod
        if t_pod_ready is None:
            pods = get_ml_pods()
            for p in pods:
                pname = p["metadata"]["name"]
                if pname != initial_pod:
                    replacement_pod_name = pname
                    # creationTimestamp
                    if t_pod_created is None and "creationTimestamp" in p["metadata"]:
                        cts_str = p["metadata"]["creationTimestamp"].replace("Z", "+00:00")
                        t_pod_created = datetime.datetime.fromisoformat(cts_str)
                        record("new_pod_created", t_pod_created, f"Pod {pname} created")

                    # container startedAt
                    if t_container_started is None:
                        cstatuses = p.get("status", {}).get("containerStatuses", [])
                        if cstatuses:
                            running_state = cstatuses[0].get("state", {}).get("running", {})
                            if "startedAt" in running_state:
                                sat_str = running_state["startedAt"].replace("Z", "+00:00")
                                t_container_started = datetime.datetime.fromisoformat(sat_str)
                                record("container_started", t_container_started, f"Container started in {pname}")

                    # pod Ready condition
                    conditions = p.get("status", {}).get("conditions", [])
                    for c in conditions:
                        if c.get("type") == "Ready" and c.get("status") == "True":
                            ltt_str = c.get("lastTransitionTime").replace("Z", "+00:00")
                            t_pod_ready = datetime.datetime.fromisoformat(ltt_str)
                            record("pod_ready", t_pod_ready, f"Pod {pname} Ready=True condition met")

        # Check job status
        if t_terminal is None:
            try:
                st = get_status(analysis_id)
                current_st = st.get("status")
                if current_st != last_status:
                    last_status = current_st
                    record(f"status_{current_st.lower()}", t_now, f"Job status -> {current_st}")
                if current_st in ("COMPLETED", "FAILED"):
                    t_terminal = t_now
                    terminal_status = current_st
                    error_msg = st.get("errorMessage")
            except Exception:
                pass

        # Break only when BOTH terminal status and pod ready have been captured
        if t_terminal is not None and t_pod_ready is not None:
            break

        time.sleep(0.2)

    # Calculate metrics
    dur_terminal_from_upload = (t_terminal - t_upload_start).total_seconds() if t_terminal else None
    dur_terminal_from_kill = (t_terminal - t_kill).total_seconds() if t_terminal else None
    dur_ready_from_kill = (t_pod_ready - t_kill).total_seconds() if t_pod_ready else None
    dur_terminal_from_ready = (t_terminal - t_pod_ready).total_seconds() if (t_terminal and t_pod_ready) else None

    print("\n-------------------------------------------------------")
    print(f"TRIAL {trial_idx} SUMMARY TIMELINE & MEASUREMENTS:")
    print("-------------------------------------------------------")
    print(f"Initial Killed Pod:           {initial_pod}")
    print(f"Replacement Pod:              {replacement_pod_name}")
    print(f"Kill Time (UTC):              {fmt_ts(t_kill)}")
    print(f"New Pod Created (UTC):        {fmt_ts(t_pod_created)}")
    print(f"Container Started (UTC):      {fmt_ts(t_container_started)}")
    print(f"Pod Ready (UTC):              {fmt_ts(t_pod_ready)}")
    print(f"Job Terminal (UTC):           {fmt_ts(t_terminal)} -> {terminal_status}")
    print(f"")
    print(f"Measurement Explanations:")
    term_up_str = f"{dur_terminal_from_upload:.2f}s" if dur_terminal_from_upload is not None else "N/A"
    term_kill_str = f"{dur_terminal_from_kill:.2f}s" if dur_terminal_from_kill is not None else "N/A"
    ready_kill_str = f"{dur_ready_from_kill:.2f}s" if dur_ready_from_kill is not None else "N/A"
    term_ready_str = f"{dur_terminal_from_ready:.2f}s" if dur_terminal_from_ready is not None else "N/A"
    print(f"  - Terminal from Upload:     {term_up_str}  (from initial POST /api/uploads to COMPLETED)")
    print(f"  - Terminal from Kill:       {term_kill_str}  (from kubectl delete pod to COMPLETED)")
    print(f"  - Replacement Ready:        {ready_kill_str}  (from kubectl delete pod to Pod Ready condition)")
    print(f"  - Delta (Terminal - Ready): {term_ready_str}  (Terminal minus Ready time; negative means Kafka worker processed before probe flipped Ready)")
    print("-------------------------------------------------------\n")

    return {
        "trial": trial_idx,
        "initial_pod": initial_pod,
        "replacement_pod": replacement_pod_name,
        "t_kill": fmt_ts(t_kill),
        "t_pod_created": fmt_ts(t_pod_created),
        "t_container_started": fmt_ts(t_container_started),
        "t_pod_ready": fmt_ts(t_pod_ready),
        "t_terminal": fmt_ts(t_terminal),
        "terminal_status": terminal_status,
        "dur_terminal_from_upload": dur_terminal_from_upload,
        "dur_terminal_from_kill": dur_terminal_from_kill,
        "dur_ready_from_kill": dur_ready_from_kill,
        "dur_terminal_from_ready": dur_terminal_from_ready,
        "error_msg": error_msg,
        "timeline": [{"event": e["event"], "time": fmt_ts(e["time"]), "details": e["details"]} for e in timeline]
    }


def main():
    print("Generating 10 MB CSV for ML Pod Recovery Trials...")
    csv_10mb = generate_csv_bytes(10.0)
    print(f"Generated CSV bytes: {len(csv_10mb)} bytes")

    results = []
    for t in range(1, 4):
        res = run_one_recovery_trial(t, csv_10mb)
        if res:
            results.append(res)
        time.sleep(3)

    os.makedirs("results", exist_ok=True)
    with open("results/ml_pod_recovery_trials.json", "w") as f:
        json.dump(results, f, indent=2)

    print("\n=======================================================")
    print("ALL 3 ML POD RECOVERY TRIALS COMPLETED")
    print("=======================================================")
    for r in results:
        print(f"Trial {r['trial']}: Status={r['terminal_status']} | "
              f"Replacement Ready={r['dur_ready_from_kill']:.2f}s | "
              f"Terminal from Kill={r['dur_terminal_from_kill']:.2f}s | "
              f"Terminal from Upload={r['dur_terminal_from_upload']:.2f}s")


if __name__ == "__main__":
    main()
