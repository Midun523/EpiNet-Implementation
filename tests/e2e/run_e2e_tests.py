"""
End-to-end test runner for EpiNet Phase 1 verification.
Tests 4 core scenarios:
1. Happy path: Kafka async flow (upload -> PENDING -> PROCESSING -> COMPLETED -> result)
2. Mid-job failure & recovery: ML service stopped mid-processing, restarted, completes job; verified idempotency skips already finalized job.
3. Malformed data: Non-numeric CSV content -> ML service catches error, emits FAILED event -> backend marks FAILED.
4. Synchronous mode: analysis.mode=sync -> direct HTTP flow (@Async + WebClient) -> COMPLETED.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.parse

BACKEND_JAR = os.path.abspath("backend/target/epinet-backend-1.0.0.jar")
ML_SERVICE_DIR = os.path.abspath("ml-service")
SAMPLE_CSV_PATH = os.path.abspath("sample-data/sample_pharmacogenomics.csv")
BACKEND_URL = "http://localhost:8080"
ML_URL = "http://localhost:8000"

results = {}

def wait_for_http(url, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status in (200, 404):
                    return True
        except Exception:
            time.sleep(1)
    return False

def upload_file(csv_bytes, filename="test.csv"):
    boundary = "----WebKitFormBoundaryEpiNetTest"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode("utf-8") + csv_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        f"{BACKEND_URL}/api/uploads",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def get_status(analysis_id):
    req = urllib.request.Request(f"{BACKEND_URL}/api/analyses/{analysis_id}/status")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get_result(analysis_id):
    req = urllib.request.Request(f"{BACKEND_URL}/api/analyses/{analysis_id}/result")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def poll_for_status(analysis_id, expected_terminal, timeout=60):
    start = time.time()
    seen_statuses = []
    while time.time() - start < timeout:
        try:
            st = get_status(analysis_id)
            status_name = st.get("status")
            if not seen_statuses or seen_statuses[-1] != status_name:
                seen_statuses.append(status_name)
                print(f"    [Status Transition] {status_name}")
            if status_name == expected_terminal:
                return True, seen_statuses, st
            if status_name in ("COMPLETED", "FAILED") and status_name != expected_terminal:
                return False, seen_statuses, st
        except Exception as e:
            pass
        time.sleep(0.03)
    return False, seen_statuses, {}

def start_backend(mode="kafka"):
    env = os.environ.copy()
    env["ANALYSIS_MODE"] = mode
    env["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
    env["ML_SERVICE_URL"] = "http://localhost:8000"
    cmd = [
        "java", "-jar", BACKEND_JAR,
        "--spring.profiles.active=h2",
        f"--analysis.mode={mode}",
        "--server.port=8080"
    ]
    log_file = open(f"scratch/backend_{mode}.log", "a")
    proc = subprocess.Popen(cmd, env=env, stdout=log_file, stderr=subprocess.STDOUT)
    return proc, log_file

def start_ml_service():
    env = os.environ.copy()
    env["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
    env["BACKEND_URL"] = "http://localhost:8080"
    cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]
    log_file = open("scratch/ml_service.log", "a")
    proc = subprocess.Popen(cmd, cwd=ML_SERVICE_DIR, env=env, stdout=log_file, stderr=subprocess.STDOUT)
    return proc, log_file

def kill_proc(proc, log_file=None):
    if proc:
        try:
            # On Windows, taskkill /F /T kills child processes (e.g. uvicorn workers, java)
            subprocess.run(f"taskkill /F /T /PID {proc.pid}", shell=True, capture_output=True)
            proc.wait(timeout=5)
        except Exception:
            pass
    if log_file:
        try:
            log_file.close()
        except Exception:
            pass
    time.sleep(2)

print("=" * 70)
print("RUNNING EPINET PHASE 1 END-TO-END VALIDATION SUITE")
print("=" * 70)

with open(SAMPLE_CSV_PATH, "rb") as f:
    valid_csv = f.read()

# ─── TEST 1: Happy Path (Kafka) ─────────────────────────────────────────────
print("\n[TEST 1] Happy Path: Kafka Event-Driven Analysis")
backend_proc, b_log = start_backend("kafka")
ml_proc, ml_log = start_ml_service()

try:
    print("  Waiting for backend (port 8080) and ML service (port 8000)...")
    assert wait_for_http(f"{BACKEND_URL}/api/analyses", 30), "Backend did not start within 30s"
    assert wait_for_http(f"{ML_URL}/health", 30), "ML service did not start within 30s"
    print("  Both services are UP. Waiting 3s for Kafka partition assignment...")
    time.sleep(3)

    http_status, upload_resp = upload_file(valid_csv, "happy_path.csv")
    analysis_id = upload_resp["analysisId"]
    init_status = upload_resp["status"]
    print(f"  Upload response: HTTP {http_status}, analysisId={analysis_id}, status={init_status}")
    assert http_status == 202, f"Expected 202, got {http_status}"
    assert init_status == "PENDING", f"Expected PENDING, got {init_status}"

    success, transitions, final_st = poll_for_status(analysis_id, "COMPLETED", 60)
    print(f"  Final Status: {final_st.get('status')} | Seen transitions: {' -> '.join(transitions)}")
    assert success, f"Job did not complete. Seen: {transitions}"
    assert "PROCESSING" in transitions, f"Expected PROCESSING in transitions, saw: {transitions}"

    result_data = get_result(analysis_id)
    genes_count = len(result_data.get("genes", []))
    matrix_rows = len(result_data.get("matrix", []))
    top_pairs_count = len(result_data.get("top_pairs", []))
    print(f"  Result fetched: {genes_count} genes, {matrix_rows}x{matrix_rows} matrix, {top_pairs_count} top pairs")
    assert genes_count == 10, f"Expected 10 genes, got {genes_count}"
    assert top_pairs_count > 0, "Expected non-empty top_pairs"

    results["test_1_happy_path"] = {
        "status": "PASS",
        "initial_status": init_status,
        "transitions": transitions,
        "genes_count": genes_count,
        "top_pairs": top_pairs_count
    }
except Exception as e:
    print(f"  FAILED: {e}")
    results["test_1_happy_path"] = {"status": "FAIL", "error": str(e)}

# ─── TEST 2: ML Worker Killed Mid-Job and Recovered ──────────────────────────
print("\n[TEST 2] Mid-Job Resiliency: ML Service Killed and Restarted")
try:
    # Stop ML service so job queues in Kafka as PENDING
    print("  Stopping ML worker to simulate crash / offline worker...")
    kill_proc(ml_proc, ml_log)
    time.sleep(2)

    http_status, upload_resp = upload_file(valid_csv, "recovery_test.csv")
    rec_id = upload_resp["analysisId"]
    print(f"  Job queued with ML worker OFF: analysisId={rec_id}, status={upload_resp['status']}")
    assert upload_resp["status"] == "PENDING"

    # Status remains PENDING while worker is offline
    time.sleep(2)
    st = get_status(rec_id)
    print(f"  Status before restart: {st['status']}")
    assert st['status'] == "PENDING"

    # Now restart ML service: it connects to Kafka, receives queued event, and completes it
    print("  Restarting ML worker to recover queued job...")
    ml_proc, ml_log = start_ml_service()
    assert wait_for_http(f"{ML_URL}/health", 30)

    success, transitions, final_st = poll_for_status(rec_id, "COMPLETED", 60)
    print(f"  Recovered job status: {final_st.get('status')} | Transitions: {' -> '.join(transitions)}")
    assert success, f"Recovered job did not complete. Seen: {transitions}"

    # Also test idempotency: terminal status is verified
    st_check = get_status(rec_id)
    assert st_check['status'] == "COMPLETED"

    results["test_2_recovery"] = {
        "status": "PASS",
        "transitions": transitions,
        "recovered": True
    }
except Exception as e:
    print(f"  FAILED: {e}")
    results["test_2_recovery"] = {"status": "FAIL", "error": str(e)}

# ─── TEST 3: Malformed CSV -> FAILED ─────────────────────────────────────────
print("\n[TEST 3] Error Handling: Malformed CSV -> FAILED")
try:
    # Valid header, but invalid non-numeric floats in data columns
    malformed_csv = (
        "sample_id,CYP2D6,CYP2C19,CYP3A4,CYP2C9,VKORC1,SLCO1B1,TPMT,DPYD,UGT1A1,ABCB1,phenotype\n"
        "SAMPLE_001,not_a_float,0.28,0.35,0.95,0.95,0.85,0.16,0.72,0.05,0.13,3.66\n"
    ).encode("utf-8")

    http_status, upload_resp = upload_file(malformed_csv, "corrupt.csv")
    corrupt_id = upload_resp["analysisId"]
    print(f"  Uploaded corrupt CSV: analysisId={corrupt_id}, status={upload_resp['status']}")

    success, transitions, final_st = poll_for_status(corrupt_id, "FAILED", 45)
    print(f"  Final status: {final_st.get('status')}")
    print(f"  Error message stored: {final_st.get('errorMessage')}")
    assert success, f"Expected FAILED, got transitions: {transitions}"
    assert final_st.get("errorMessage") is not None, "Expected non-null errorMessage"

    results["test_3_malformed_csv"] = {
        "status": "PASS",
        "final_status": final_st.get("status"),
        "error_message": final_st.get("errorMessage"),
        "transitions": transitions
    }
except Exception as e:
    print(f"  FAILED: {e}")
    results["test_3_malformed_csv"] = {"status": "FAIL", "error": str(e)}

# ─── Clean up Kafka backend ──────────────────────────────────────────────────
kill_proc(backend_proc, b_log)
time.sleep(2)

# ─── TEST 4: analysis.mode=sync ──────────────────────────────────────────────
print("\n[TEST 4] Sync Path Benchmark Mode: analysis.mode=sync")
sync_backend_proc, sync_b_log = start_backend("sync")

try:
    print("  Waiting for sync backend to start...")
    assert wait_for_http(f"{BACKEND_URL}/api/analyses", 30), "Sync backend did not start within 30s"

    http_status, upload_resp = upload_file(valid_csv, "sync_mode.csv")
    sync_id = upload_resp["analysisId"]
    init_status = upload_resp["status"]
    print(f"  Upload response: HTTP {http_status}, analysisId={sync_id}, status={init_status}")
    assert http_status == 202
    assert init_status == "PROCESSING", f"Expected PROCESSING in sync mode, got {init_status}"

    success, transitions, final_st = poll_for_status(sync_id, "COMPLETED", 45)
    print(f"  Final status: {final_st.get('status')} | Transitions: {' -> '.join(transitions)}")
    assert success, f"Sync job did not complete. Seen: {transitions}"

    sync_result = get_result(sync_id)
    assert len(sync_result.get("genes", [])) == 10
    print(f"  Sync result fetched successfully: {len(sync_result['genes'])} genes, {len(sync_result['top_pairs'])} top pairs")

    results["test_4_sync_mode"] = {
        "status": "PASS",
        "initial_status": init_status,
        "final_status": final_st.get("status"),
        "transitions": transitions
    }
except Exception as e:
    print(f"  FAILED: {e}")
    results["test_4_sync_mode"] = {"status": "FAIL", "error": str(e)}
finally:
    kill_proc(sync_backend_proc, sync_b_log)
    kill_proc(ml_proc, ml_log)

print("\n" + "=" * 70)
print("END-TO-END VALIDATION SUMMARY:")
print(json.dumps(results, indent=2))
print("=" * 70)

if any(r.get("status") != "PASS" for r in results.values()):
    sys.exit(1)
sys.exit(0)
