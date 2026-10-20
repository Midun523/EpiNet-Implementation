#!/usr/bin/env python3
"""
tests/bench/benchmark_runner.py — Automated Benchmark Suite for EpiNet Architecture Comparisons.

Executes comparative benchmarks:
  Comparison 1: Sync Path (ANALYSIS_MODE=sync) vs Kafka Path (ANALYSIS_MODE=kafka)
  Comparison 2: ML Service Fixed 1 Replica vs Horizontal Pod Autoscaler (HPA 1-3)

Features:
- State reset before each condition: down -v MySQL, restart deployments, wait for ready.
- Uses identical synthetic pharmacogenomics data generator as e2e.
- Runs conditions in ABBA interleaved order to eliminate systematic temporal bias.
- Uses k6 in official Docker container (grafana/k6).
- High-frequency background monitor for ML replica count, pod restarts, and WSL2 VM memory.
- Writes raw per-request k6 CSVs, per-job database CSVs, and system timeseries CSVs to git-ignored results/.
"""

import argparse
import csv
import datetime
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

sys.stdout.reconfigure(line_buffering=True)

RESULTS_DIR = os.path.abspath("results")
BENCH_DIR = os.path.abspath("tests/bench")
DATA_DIR = os.path.join(BENCH_DIR, "data")
SAMPLE_CSV = os.path.abspath("sample-data/sample_pharmacogenomics.csv")
BACKEND_HOST_URL = "http://localhost:30081"
K6_TARGET_URL = "http://host.docker.internal:30081"


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def fmt_ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if dt else "N/A"


def run_cmd(cmd, timeout=120):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def generate_csv_bytes(target_mb: float) -> bytes:
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


def ensure_datasets():
    os.makedirs(DATA_DIR, exist_ok=True)
    for mb in [2.0, 10.0]:
        p = os.path.join(DATA_DIR, f"benchmark_{int(mb)}mb.csv")
        if not os.path.exists(p) or os.path.getsize(p) < mb * 1024 * 1024 * 0.95:
            print(f"[DATA] Generating {mb} MB dataset at {p}...")
            b = generate_csv_bytes(mb)
            with open(p, "wb") as f:
                f.write(b)
            print(f"[DATA] Generated {len(b)} bytes.")


def reset_state():
    """Resets MySQL state (down -v) and recreates backend/ml pods."""
    print("\n[STATE RESET] Resetting MySQL data volume and recreating pods...")
    run_cmd(["docker", "compose", "stop", "mysql"])
    run_cmd(["docker", "compose", "rm", "-v", "-f", "mysql"])
    run_cmd(["docker", "compose", "up", "-d", "mysql"])

    # Recreate Kafka topics to prevent stale ghost messages across resets
    run_cmd(["docker", "compose", "exec", "-T", "kafka", "/opt/kafka/bin/kafka-topics.sh", "--bootstrap-server", "localhost:9092", "--delete", "--topic", "analysis.requested", "--if-exists"])
    run_cmd(["docker", "compose", "exec", "-T", "kafka", "/opt/kafka/bin/kafka-topics.sh", "--bootstrap-server", "localhost:9092", "--create", "--topic", "analysis.requested", "--partitions", "3", "--replication-factor", "1", "--if-not-exists"])
    run_cmd(["docker", "compose", "exec", "-T", "kafka", "/opt/kafka/bin/kafka-topics.sh", "--bootstrap-server", "localhost:9092", "--delete", "--topic", "analysis.completed", "--if-exists"])
    run_cmd(["docker", "compose", "exec", "-T", "kafka", "/opt/kafka/bin/kafka-topics.sh", "--bootstrap-server", "localhost:9092", "--create", "--topic", "analysis.completed", "--partitions", "3", "--replication-factor", "1", "--if-not-exists"])

    # Wait for MySQL ping
    for _ in range(30):
        out, _, code = run_cmd(["docker", "compose", "exec", "-T", "mysql", "mysqladmin", "ping", "-h", "localhost", "-uroot", "-proot_pass"])
        if code == 0 and "mysqld is alive" in out:
            break
        time.sleep(1)

    # Rollout restart backend and ml-service
    run_cmd(["kubectl", "rollout", "restart", "deployment", "backend", "ml-service"])
    run_cmd(["kubectl", "rollout", "status", "deployment", "backend", "--timeout=90s"])
    run_cmd(["kubectl", "rollout", "status", "deployment", "ml-service", "--timeout=90s"])
    print("[STATE RESET] Cluster pods, clean database, and fresh Kafka topics ready.")


def configure_condition(condition_name: str):
    """
    Configures cluster for:
    - sync: ANALYSIS_MODE=sync, HPA deleted, ml-service replicas=1
    - kafka_fixed1: ANALYSIS_MODE=kafka, HPA deleted, ml-service replicas=1
    - kafka_hpa: ANALYSIS_MODE=kafka, HPA enabled (1-3 replicas)
    """
    print(f"\n[CONFIG] Setting up condition: {condition_name}")
    if condition_name == "sync":
        run_cmd(["kubectl", "set", "env", "deployment/backend", "ANALYSIS_MODE=sync"])
        run_cmd(["kubectl", "delete", "hpa", "ml-service-hpa", "--ignore-not-found=true"])
        run_cmd(["kubectl", "scale", "deployment", "ml-service", "--replicas=1"])
    elif condition_name == "kafka_fixed1":
        run_cmd(["kubectl", "set", "env", "deployment/backend", "ANALYSIS_MODE=kafka"])
        run_cmd(["kubectl", "delete", "hpa", "ml-service-hpa", "--ignore-not-found=true"])
        run_cmd(["kubectl", "scale", "deployment", "ml-service", "--replicas=1"])
    elif condition_name == "kafka_hpa":
        run_cmd(["kubectl", "set", "env", "deployment/backend", "ANALYSIS_MODE=kafka"])
        run_cmd(["kubectl", "apply", "-f", "k8s/hpa.yaml"])
    else:
        raise ValueError(f"Unknown condition: {condition_name}")

    run_cmd(["kubectl", "rollout", "status", "deployment", "backend", "--timeout=90s"])
    run_cmd(["kubectl", "rollout", "status", "deployment", "ml-service", "--timeout=90s"])
    time.sleep(2)


class SystemMonitor(threading.Thread):
    def __init__(self, run_id: str, interval_sec: float = 2.0):
        super().__init__()
        self.run_id = run_id
        self.interval = interval_sec
        self.stop_event = threading.Event()
        self.records = []

    def run(self):
        while not self.stop_event.is_set():
            t_str = now_utc().isoformat()
            # ML replicas
            ml_reps_out, _, _ = run_cmd(["kubectl", "get", "deployment", "ml-service", "-o", "jsonpath={.status.readyReplicas}"])
            try:
                ml_reps = int(ml_reps_out.strip()) if ml_reps_out.strip() else 0
            except ValueError:
                ml_reps = 0

            # Pod restarts & OOM
            pods_out, _, _ = run_cmd(["kubectl", "get", "pods", "-l", "app=ml-service", "-o", "jsonpath={range .items[*]}{.metadata.name},{.status.containerStatuses[0].restartCount},{.status.containerStatuses[0].lastState.terminated.reason}{'\\n'}{end}"])
            restarts_sum = 0
            oom_count = 0
            for line in pods_out.splitlines():
                parts = line.strip().split(",")
                if len(parts) >= 2 and parts[1].isdigit():
                    restarts_sum += int(parts[1])
                if len(parts) >= 3 and "OOM" in parts[2]:
                    oom_count += 1

            # VM memory
            wsl_mem, _, _ = run_cmd(["wsl", "free", "-m"])
            free_mb = 0
            avail_mb = 0
            for line in wsl_mem.splitlines():
                if line.startswith("Mem:"):
                    cols = line.split()
                    if len(cols) >= 7:
                        free_mb = int(cols[3])
                        avail_mb = int(cols[6])

            self.records.append({
                "timestamp_utc": t_str,
                "ml_ready_replicas": ml_reps,
                "ml_restarts": restarts_sum,
                "ml_oom_killed": oom_count,
                "vm_free_mb": free_mb,
                "vm_avail_mb": avail_mb
            })
            time.sleep(self.interval)

    def stop(self):
        self.stop_event.set()
        self.join()

    def save(self, filepath: str):
        if not self.records:
            return
        keys = list(self.records[0].keys())
        with open(filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(self.records)


def truncate_db():
    run_cmd(["docker", "compose", "exec", "-T", "mysql", "mysql", "-uroot", "-proot_pass", "-e", "TRUNCATE TABLE epinet.analysis_run;"])


def extract_database_jobs(out_path: str):
    """Extracts job records from MySQL for the current trial."""
    sql = """
    SELECT id, status, created_at, completed_at, 
           TIMESTAMPDIFF(MICROSECOND, created_at, completed_at)/1000000.0 AS duration_sec
    FROM epinet.analysis_run 
    ORDER BY created_at ASC;
    """
    cmd = ["docker", "compose", "exec", "-T", "mysql", "mysql", "-uroot", "-proot_pass", "-e", sql]
    out, _, code = run_cmd(cmd)
    lines = out.splitlines()
    rows = []
    if len(lines) > 1:
        headers = [h.strip() for h in lines[0].split("\t")]
        for line in lines[1:]:
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) == len(headers):
                rows.append(dict(zip(headers, parts)))

    with open(out_path, "w", newline="") as f:
        fieldnames = ["id", "status", "created_at", "completed_at", "duration_sec"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def run_k6_load(csv_file_name: str, vus: int, duration_sec: int, run_id: str):
    """Executes k6 container and captures metrics."""
    k6_raw_out = os.path.join(RESULTS_DIR, f"{run_id}_k6_raw.csv")
    container_csv_path = f"/work/tests/bench/data/{csv_file_name}"
    container_out_path = f"/work/results/{run_id}_k6_raw.csv"

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{os.getcwd()}:/work",
        "-w", "/work",
        "-e", f"CSV_PATH={container_csv_path}",
        "-e", f"TARGET_URL={K6_TARGET_URL}",
        "-e", f"VUS={vus}",
        "-e", f"DURATION={duration_sec}s",
        "grafana/k6", "run",
        "--out", f"csv={container_out_path}",
        "/work/tests/bench/upload_bench.js"
    ]
    print(f"[RUNNER] Executing k6: {csv_file_name} @ {vus} VUs for {duration_sec}s...")
    t0 = time.time()
    out, err, code = run_cmd(cmd, timeout=duration_sec + 60)
    dur = time.time() - t0
    print(f"[RUNNER] k6 finished in {dur:.2f}s (Exit code: {code})")
    return k6_raw_out


def wait_for_jobs_drain(timeout_sec: int = 25):
    """Waits until all active jobs reach COMPLETED or FAILED, or timeout."""
    print("[RUNNER] Waiting for outstanding jobs to complete...")
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        out, _, _ = run_cmd(["docker", "compose", "exec", "-T", "mysql", "mysql", "-uroot", "-proot_pass", "-e", "SELECT COUNT(*) FROM epinet.analysis_run WHERE status IN ('PENDING', 'PROCESSING');"])
        lines = out.splitlines()
        if len(lines) > 1 and lines[1].strip() == "0":
            print("[RUNNER] Queue drained (0 active jobs).")
            return
        time.sleep(1)
    print("[RUNNER] Drain wait timed out (or all active compute finished).")


def run_benchmark_trial(condition: str, file_mb: int, vus: int, rep: int, duration: int):
    run_id = f"{condition}_{file_mb}mb_{vus}vu_rep{rep}_{int(time.time())}"
    print(f"\n{'='*70}\nTRIAL START: Condition={condition} | File={file_mb}MB | VUs={vus} | Rep={rep}\n{'='*70}")

    # Ensure clean database before run
    truncate_db()

    # Start system monitor
    monitor = SystemMonitor(run_id=run_id, interval_sec=2.0)
    monitor.start()

    # Run k6
    csv_filename = f"benchmark_{file_mb}mb.csv"
    k6_out = run_k6_load(csv_filename, vus, duration, run_id)

    # Wait for completion of any queued tasks
    wait_for_jobs_drain()

    # Stop monitor and save
    monitor.stop()
    sys_out = os.path.join(RESULTS_DIR, f"{run_id}_system.csv")
    monitor.save(sys_out)

    # Extract database jobs
    jobs_out = os.path.join(RESULTS_DIR, f"{run_id}_jobs.csv")
    job_count = extract_database_jobs(jobs_out)
    print(f"[TRIAL COMPLETE] Saved {job_count} job records -> {jobs_out}")
    print(f"[TRIAL COMPLETE] Saved system timeseries -> {sys_out}")

    return {
        "run_id": run_id,
        "condition": condition,
        "file_mb": file_mb,
        "vus": vus,
        "rep": rep,
        "jobs_count": job_count
    }


def main():
    parser = argparse.ArgumentParser(description="EpiNet Benchmark Suite")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds per load step (default: 60)")
    parser.add_argument("--repetitions", type=int, default=3, help="Repetitions per condition (default: 3)")
    parser.add_argument("--comparison", choices=["sync_vs_kafka", "fixed_vs_hpa", "all"], default="all")
    parser.add_argument("--quick", action="store_true", help="Quick verification mode (shorter duration for dry-run)")
    args = parser.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    ensure_datasets()

    duration = 10 if args.quick else args.duration
    reps = 2 if args.quick else args.repetitions

    # Load matrix as requested by user:
    # 2 MB at 1, 2, 4, 8 VUs
    # 10 MB at 1, 2 VUs
    load_levels = [
        (2, 1),
        (2, 2),
        (2, 4),
        (2, 8),
        (10, 1),
        (10, 2),
    ]

    if args.comparison == "sync_vs_kafka":
        conditions = ["sync", "kafka_fixed1"]
    elif args.comparison == "fixed_vs_hpa":
        conditions = ["kafka_fixed1", "kafka_hpa"]
    else:
        conditions = ["sync", "kafka_fixed1", "kafka_hpa"]
    
    total_runs = len(load_levels) * len(conditions) * reps
    expected_duration_sec = total_runs * (duration + 5) + (len(conditions) * reps * 20)

    print("=" * 72)
    print(f"EPINET PHASE 5 BENCHMARK SUITE")
    print(f"  Duration per run:        {duration}s")
    print(f"  Repetitions:             {reps}")
    print(f"  Conditions:              {conditions}")
    print(f"  Load Levels:             {len(load_levels)} levels: {load_levels}")
    print(f"  Total Matrix Runs:       {total_runs} runs")
    print(f"  Expected Total Duration: {expected_duration_sec}s (~{expected_duration_sec / 60.0:.1f} minutes)")
    print("=" * 72)

    trial_log = os.path.join(RESULTS_DIR, f"bench_manifest_{int(time.time())}.json")
    all_trials = []

    # Execute matrix in ABBA order across repetitions:
    # Rep 0 (odd 1): A -> B -> C
    # Rep 1 (even 2): C -> B -> A
    # Rep 2 (odd 3): A -> B -> C
    for rep_idx in range(reps):
        if rep_idx % 2 == 0:
            cond_order = list(conditions)
        else:
            cond_order = list(reversed(conditions))

        print(f"\n{'#'*72}")
        print(f">>> COMMENCING REPETITION {rep_idx + 1} OF {reps} (ORDER: {' -> '.join(cond_order)}) <<<")
        print(f"{'#'*72}")

        for c_name in cond_order:
            print(f"\n>>> STATE RESET AND SETUP FOR CONDITION: {c_name} (Rep {rep_idx + 1}) <<<")
            reset_state()
            configure_condition(c_name)

            for file_mb, vus in load_levels:
                t_info = run_benchmark_trial(c_name, file_mb, vus, rep_idx + 1, duration)
                all_trials.append(t_info)

                with open(trial_log, "w") as f:
                    json.dump(all_trials, f, indent=2)

    print("\n" + "=" * 72)
    print(f"ALL BENCHMARK TRIALS COMPLETED! ({len(all_trials)} total runs)")
    print(f"Manifest written to: {trial_log}")
    print("=" * 72)


if __name__ == "__main__":
    main()
