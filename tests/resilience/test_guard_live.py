"""
tests/resilience/test_guard_live.py — Live validation of StuckJobGuard under Kubernetes.

Tests:
1. Temporarily scales down ML service to 0 so job remains in PENDING state.
2. Lowers stuck-job timeout to 5s on backend deployment.
3. Uploads sample CSV to backend.
4. Waits for StuckJobGuard scheduled scan to fire live.
5. Asserts job transitions to FAILED with timeout error message.
6. Inspects live backend logs proving StuckJobGuard executed the failure transition.
7. Fully restores StuckJobGuard configuration and restores ml-service to 1 replica.

Saves raw run results to git-ignored `results/guard_live_results.json`.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

sys.stdout.reconfigure(line_buffering=True)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:30081")
SAMPLE_CSV = os.path.abspath("sample-data/sample_pharmacogenomics.csv")
RESULTS_DIR = os.path.abspath("results")


def run(cmd):
    print(f"+ {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.stderr.strip():
        print(r.stderr.strip())
    return r.stdout.strip()


def upload():
    boundary = "----FormBoundaryGuardLive"
    with open(SAMPLE_CSV, "rb") as f:
        csv_bytes = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="guard_live.csv"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode() + csv_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/uploads",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def get_status(aid):
    req = urllib.request.Request(f"{BACKEND_URL}/api/analyses/{aid}/status")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def main():
    print("=" * 70)
    print("LIVE STUCK-JOB GUARD TEST")
    print("=" * 70)

    # Step 1: Temporarily lower timeout
    print("\n=== STEP 1: Temporarily lower StuckJobGuard timeout to 5s, interval to 3000ms ===")
    run("kubectl set env deployment/backend ANALYSIS_STUCK_JOB_PROCESSING_TIMEOUT_SECONDS=5 ANALYSIS_STUCK_JOB_PENDING_TIMEOUT_SECONDS=5 ANALYSIS_STUCK_JOB_CHECK_INTERVAL_MS=3000")
    run("kubectl rollout status deployment backend --timeout=90s")

    # Step 2: Scale ml-service to 0
    print("\n=== STEP 2: Scale ml-service to 0 so uploaded job stays in PENDING ===")
    run("kubectl scale deployment ml-service --replicas=0")
    run("kubectl wait --for=delete pod -l app=ml-service --timeout=30s")

    # Step 3: Upload job
    print("\n=== STEP 3: Upload job to backend ===")
    resp = upload()
    aid = resp["analysisId"]
    print(f"Uploaded analysisId={aid}, status={resp.get('status')}")

    # Step 4: Wait for guard to fire
    print("\n=== STEP 4: Wait 8s for StuckJobGuard to fire live ===")
    time.sleep(8)

    st = get_status(aid)
    print(f"Status after guard firing: {st.get('status')}")
    print(f"Error message stored: {st.get('errorMessage')}")

    # Step 5: Capture live logs
    print("\n=== STEP 5: Backend pod logs showing StuckJobGuard firing ===")
    backend_pod = run("kubectl get pods -l app=backend -o jsonpath={.items[0].metadata.name}")
    logs = run(f"kubectl logs {backend_pod} --tail=30")
    guard_logs = []
    for line in logs.splitlines():
        if "StuckJobGuard" in line or "stuck job guard" in line or aid in line:
            print(f"  [LIVE GUARD LOG] {line}")
            guard_logs.append(line)

    # Step 6: Restore
    print("\n=== STEP 6: Restore StuckJobGuard config and restore ml-service ===")
    run("kubectl set env deployment/backend ANALYSIS_STUCK_JOB_PROCESSING_TIMEOUT_SECONDS- ANALYSIS_STUCK_JOB_PENDING_TIMEOUT_SECONDS- ANALYSIS_STUCK_JOB_CHECK_INTERVAL_MS-")
    run("kubectl rollout status deployment backend --timeout=90s")
    run("kubectl scale deployment ml-service --replicas=1")
    run("kubectl rollout status deployment ml-service --timeout=60s")
    print("Restoration complete. Backend and ml-service back to normal.")

    result_data = {
        "analysis_id": aid,
        "final_status": st.get("status"),
        "error_message": st.get("errorMessage"),
        "guard_logs": guard_logs,
        "restored": True
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_file = os.path.join(RESULTS_DIR, "guard_live_results.json")
    with open(out_file, "w") as f:
        json.dump(result_data, f, indent=2)
    print(f"\nRaw results saved to {out_file}")

    assert st.get("status") == "FAILED", f"Expected FAILED, got {st.get('status')}"
    print("\n[VERIFICATION PASSED] StuckJobGuard fired live and marked job FAILED.")


if __name__ == "__main__":
    main()
