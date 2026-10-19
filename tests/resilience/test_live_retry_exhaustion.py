"""
Targeted live test for Infinite Retry End Condition (Item 2).
Uses TRANSIENT_RETRY_TIMEOUT_SECONDS=15 to deterministically trigger
timeout exhaustion within a ~40s backend outage window.
"""

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


def run_cmd(cmd, timeout=90):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def get_pod_name(app_label):
    out, _, _ = run_cmd(["kubectl", "get", "pods", "-l", f"app={app_label}", "-o", "jsonpath={.items[0].metadata.name}"])
    return out.strip()


def get_status(analysis_id):
    req = urllib.request.Request(f"{BACKEND_URL}/api/analyses/{analysis_id}/status")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def upload_file(csv_bytes, filename="live_retry.csv"):
    boundary = "----FormBoundaryLiveRetryExhaust"
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


def poll_status(analysis_id, target_statuses, timeout=60):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            st = get_status(analysis_id)
            s = st.get("status")
            if s != last:
                last = s
                print(f"  [Status Poll] Job {analysis_id[:8]} -> {s}")
            if s in target_statuses:
                return st
        except Exception:
            pass
        time.sleep(1)
    return {"status": last, "errorMessage": f"Timed out polling after {timeout}s"}


def main():
    print("=" * 70)
    print("LIVE TEST: INFINITE RETRY TIMEOUT & PARTITION UNBLOCKING")
    print("=" * 70)

    # 1. Scale backend to 1 and wait Ready
    print("\n[Step 1] Ensuring backend is running...")
    run_cmd(["kubectl", "scale", "deployment", "backend", "--replicas=1"])
    run_cmd(["kubectl", "wait", "--for=condition=Ready", "pod", "-l", "app=backend", "--timeout=60s"])

    # 2. Configure TRANSIENT_RETRY_TIMEOUT_SECONDS=15 on ml-service
    print("\n[Step 2] Configuring TRANSIENT_RETRY_TIMEOUT_SECONDS=15 on ml-service...")
    run_cmd(["kubectl", "set", "env", "deployment/ml-service", "TRANSIENT_RETRY_TIMEOUT_SECONDS=15"])
    run_cmd(["kubectl", "rollout", "status", "deployment/ml-service", "--timeout=60s"])
    run_cmd(["kubectl", "wait", "--for=condition=Ready", "pod", "-l", "app=ml-service", "--timeout=60s"])
    ml_pod = get_pod_name("ml-service")
    print(f"ml-service ready with 15s retry window on pod {ml_pod}.")

    # 3. Scale ml-service to 0
    print("\n[Step 3] Scaling ml-service to 0 so Job 1 is queued in Kafka...")
    run_cmd(["kubectl", "scale", "deployment", "ml-service", "--replicas=0"])
    run_cmd(["kubectl", "wait", "--for=delete", "pod", "-l", "app=ml-service", "--timeout=30s"])

    with open(SAMPLE_CSV, "rb") as f:
        csv_bytes = f.read()

    # 4. Upload Job 1
    print("\n[Step 4] Uploading Job 1 (sitting queued in Kafka as PENDING)...")
    status, resp1 = upload_file(csv_bytes, "job1_exhaust_test.csv")
    job1_id = resp1["analysisId"]
    st1_init = get_status(job1_id)
    print(f"Job 1 uploaded: analysisId={job1_id}, initial status={st1_init.get('status')}")

    # 5. Scale backend to 0
    print("\n[Step 5] Scaling backend to 0 (outage starts)...")
    run_cmd(["kubectl", "scale", "deployment", "backend", "--replicas=0"])
    run_cmd(["kubectl", "wait", "--for=delete", "pod", "-l", "app=backend", "--timeout=30s"])
    print("Backend is 0 replicas (DOWN).")

    # 6. Scale ml-service to 1
    print("\n[Step 6] Scaling ml-service to 1 (ML starts consuming Job 1, backend is DOWN)...")
    t_ml_start = time.time()
    run_cmd(["kubectl", "scale", "deployment", "ml-service", "--replicas=1"])
    run_cmd(["kubectl", "wait", "--for=condition=Ready", "pod", "-l", "app=ml-service", "--timeout=60s"])
    active_ml_pod = get_pod_name("ml-service")
    print(f"ML service active on pod {active_ml_pod}.")

    # 7. Wait 45s (> 15s timeout) for retry exhaustion
    print("\n[Step 7] Waiting 45s for retry exhaustion (limit 15s)...")
    for _ in range(9):
        time.sleep(5)
        print(f"  Outage ongoing: {round(time.time() - t_ml_start, 1)}s elapsed since ML start...")

    # Print logs from active ml-service pod
    print(f"\n[Step 7b] Checking logs from {active_ml_pod}:")
    out, _, _ = run_cmd(["kubectl", "logs", active_ml_pod, "--tail=50"])
    found_exhaustion = False
    for line in out.splitlines():
        if any(k in line for k in ("Transient", "exhausted", "Committed offset", "FAILED")):
            print(f"    {line}")
            if "exhausted" in line:
                found_exhaustion = True

    print(f"Retry exhaustion logged: {found_exhaustion}")

    # 8. Restore backend to 1 replica
    print("\n[Step 8] Restoring backend to 1 replica...")
    run_cmd(["kubectl", "scale", "deployment", "backend", "--replicas=1"])
    run_cmd(["kubectl", "wait", "--for=condition=Ready", "pod", "-l", "app=backend", "--timeout=90s"])
    print("Backend restored and Ready.")
    time.sleep(4)

    # 9. Verify Job 1 status
    print("\n[Step 9] Checking Job 1 final status...")
    st1 = poll_status(job1_id, ["FAILED", "COMPLETED"], timeout=30)
    print(f"Job 1 Result: Status={st1.get('status')}")
    print(f"Job 1 Error: {st1.get('errorMessage')}")

    # 10. Upload Job 2 to prove the partition is NOT blocked
    print("\n[Step 10] Uploading Job 2 on same partition to prove queue is unblocked...")
    status, resp2 = upload_file(csv_bytes, "job2_unblocked_test.csv")
    job2_id = resp2["analysisId"]
    print(f"Job 2 uploaded: analysisId={job2_id}, HTTP {status}")

    st2 = poll_status(job2_id, ["COMPLETED", "FAILED"], timeout=60)
    print(f"Job 2 Result: Status={st2.get('status')}")
    if st2.get("errorMessage"):
        print(f"Job 2 Error: {st2.get('errorMessage')}")

    # 11. Restore TRANSIENT_RETRY_TIMEOUT_SECONDS to default 600
    print("\n[Step 11] Restoring TRANSIENT_RETRY_TIMEOUT_SECONDS=600 on ml-service...")
    run_cmd(["kubectl", "set", "env", "deployment/ml-service", "TRANSIENT_RETRY_TIMEOUT_SECONDS=600"])
    run_cmd(["kubectl", "rollout", "status", "deployment/ml-service", "--timeout=60s"])
    print("ml-service restored to 600s default window.")

    # Verification summary
    print("\n" + "=" * 70)
    print("LIVE RETRY TEST VERIFICATION SUMMARY:")
    print("=" * 70)
    print(f"Job 1 (exhausted):  Status={st1.get('status')} | Error={st1.get('errorMessage')}")
    print(f"Job 2 (subsequent): Status={st2.get('status')}")

    assert st1.get("status") == "FAILED", f"Expected Job 1 to be FAILED, got {st1.get('status')}"
    assert "transient retries exhausted" in str(st1.get("errorMessage")), "Expected retry exhaustion in error message"
    assert st2.get("status") == "COMPLETED", f"Expected Job 2 to be COMPLETED, got {st2.get('status')}"
    print("\n[VERIFICATION PASSED] Job 1 marked FAILED after transient retries exhausted; Partition unblocked; Job 2 COMPLETED successfully!")


if __name__ == "__main__":
    main()
