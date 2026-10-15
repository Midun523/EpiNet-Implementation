"""
40 MB Benchmark with Per-Stage Timing (Phase 2 Review).

WHY: Quantifies exact breakdown of latency across upload, queue wait,
worker CSV fetch, matrix computation, and MySQL result write-back for 40 MB runs.

Stages measured per job:
1. upload_to_published: Time for backend to receive HTTP upload, flush PENDING row to DB, and publish Kafka event.
2. queue_wait: Time the message waited in Kafka before the ML worker dequeued it.
3. csv_fetch: Time for ML service to GET /api/internal/analyses/{id}/data and receive 40 MB payload.
4. compute: Time for ML service to validate, convert DataFrame, and run interaction regression.
5. result_write_back: Time for ML worker to publish completed event and backend consumer to update status in MySQL.
6. total_e2e: End-to-end elapsed time from client upload start to COMPLETED status poll.
"""

import os
import sys
import time
import json
import statistics
import urllib.request
import re

SAMPLE_CSV_PATH = os.path.abspath("sample-data/sample_pharmacogenomics.csv")
BACKEND_URL = "http://localhost:8080"

def generate_csv_bytes(target_mb=40.0):
    with open(SAMPLE_CSV_PATH, "rb") as f:
        lines = f.readlines()
    header = lines[0]
    data_lines = lines[1:]
    block = b"".join(data_lines)
    target_bytes = int(target_mb * 1024 * 1024)
    needed = target_bytes - len(header)
    num_blocks = needed // len(block)
    rem = needed % len(block)
    chunks = [header]
    chunks.extend([block] * num_blocks)
    if rem > 0:
        part = block[:rem]
        last_nl = part.rfind(b"\n")
        if last_nl != -1:
            chunks.append(part[:last_nl + 1])
    return b"".join(chunks)

def upload_file(csv_bytes, filename="bench_40mb.csv"):
    boundary = "----WebKitFormBoundaryEpiNetStageBench"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode("utf-8") + csv_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    t_start = time.time()
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/uploads",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        t_upload = time.time() - t_start
        data = json.loads(resp.read().decode("utf-8"))
        return data["analysisId"], t_upload

def poll_for_completion(analysis_id, timeout=120):
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(f"{BACKEND_URL}/api/analyses/{analysis_id}/status")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                st = data.get("status")
                if st == "COMPLETED":
                    return True, time.time() - start
                if st == "FAILED":
                    return False, time.time() - start
        except Exception:
            pass
        time.sleep(0.5)
    return False, time.time() - start

def extract_stage_timings(backend_log_path, ml_log_path, analysis_id):
    stages = {
        "upload_to_published": None,
        "multipart_recv": None,
        "csv_validate": None,
        "entity_build": None,
        "db_insert": None,
        "kafka_publish": None,
        "queue_wait": None,
        "csv_fetch": None,
        "compute": None,
        "result_write_back": None,
    }

    def read_lines(path):
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path, "rb") as f:
                raw = f.read()
            if raw.startswith(b"\xff\xfe") or b"\x00" in raw[:200]:
                text = raw.decode("utf-16le", errors="ignore")
            else:
                text = raw.decode("utf-8", errors="ignore")
            return text.splitlines()
        except Exception:
            return []

    for line in read_lines(backend_log_path):
        if analysis_id in line:
            m = re.search(r"upload_received -> event_published total=(\d+)\s*ms", line)
            if m:
                stages["upload_to_published"] = round(int(m.group(1)) / 1000.0, 3)
            for sub in ["multipart_recv", "csv_validate", "entity_build", "db_insert", "kafka_publish"]:
                m_sub = re.search(sub + r"=(\d+)\s*ms", line)
                if m_sub:
                    stages[sub] = round(int(m_sub.group(1)) / 1000.0, 3)
            m2 = re.search(r"marked COMPLETED in MySQL \(db_update took (\d+)\s*ms\)", line)
            if m2:
                stages["result_write_back"] = round(int(m2.group(1)) / 1000.0, 3)

    for line in read_lines(ml_log_path):
        if analysis_id in line:
            m_qw = re.search(r"queue_wait=([\d\.]+)s", line)
            if m_qw:
                stages["queue_wait"] = round(float(m_qw.group(1)), 3)
            m_cf = re.search(r"csv_fetch=([\d\.]+)s", line)
            if m_cf:
                stages["csv_fetch"] = round(float(m_cf.group(1)), 3)
            m_comp = re.search(r"compute=([\d\.]+)s", line)
            if m_comp:
                stages["compute"] = round(float(m_comp.group(1)), 3)
        elif stages["compute"] is None and "STAGE TIMING: compute=" in line:
            m_comp = re.search(r"compute=([\d\.]+)s", line)
            if m_comp:
                stages["compute"] = round(float(m_comp.group(1)), 3)

    return stages

def run_benchmark(num_runs=5, backend_log=None, ml_log=None):
    print(f"\n=======================================================")
    print(f"=== Starting 40 MB Benchmark ({num_runs} runs) ===")
    print(f"=======================================================")
    t_gen = time.time()
    csv_bytes = generate_csv_bytes(40.0)
    print(f"Generated {len(csv_bytes)/(1024*1024):.2f} MB payload in {time.time()-t_gen:.2f}s.\n")

    results = []

    for i in range(1, num_runs + 1):
        print(f"--- Run {i}/{num_runs} ---")
        analysis_id, client_upload_time = upload_file(csv_bytes)
        print(f"  Job ID: {analysis_id}")
        print(f"  Client Upload Time: {client_upload_time:.3f}s")

        completed, poll_duration = poll_for_completion(analysis_id)
        total_e2e = client_upload_time + poll_duration
        print(f"  Status Poll Duration: {poll_duration:.3f}s | Completed: {completed} | Total E2E: {total_e2e:.3f}s")

        # Sleep briefly to allow logs to flush
        time.sleep(1.0)
        stages = extract_stage_timings(backend_log, ml_log, analysis_id) if backend_log and ml_log else {}
        stages["total_e2e"] = round(total_e2e, 3)
        stages["client_upload"] = round(client_upload_time, 3)
        stages["poll_duration"] = round(poll_duration, 3)

        print("  Stage Breakdown:")
        for k, v in stages.items():
            print(f"    {k:22}: {v}s" if v is not None else f"    {k:22}: N/A")
        print()
        results.append(stages)

    print("\n=======================================================")
    print(f"=== 40 MB Benchmark Summary (Min / Median / Max across {num_runs} runs) ===")
    print("=======================================================")
    keys = ["client_upload", "upload_to_published",
            "multipart_recv", "csv_validate", "entity_build", "db_insert", "kafka_publish",
            "queue_wait", "csv_fetch", "compute", "result_write_back", "poll_duration", "total_e2e"]
    for k in keys:
        vals = [r[k] for r in results if r.get(k) is not None]
        if vals:
            min_v = min(vals)
            med_v = statistics.median(vals)
            max_v = max(vals)
            print(f"  {k:22}: Min={min_v:7.3f}s | Median={med_v:7.3f}s | Max={max_v:7.3f}s")
        else:
            print(f"  {k:22}: N/A")
    print("=======================================================\n")

if __name__ == "__main__":
    b_log = sys.argv[1] if len(sys.argv) > 1 else None
    m_log = sys.argv[2] if len(sys.argv) > 2 else None
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    run_benchmark(num_runs=n, backend_log=b_log, ml_log=m_log)
