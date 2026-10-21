#!/usr/bin/env python3
"""
tests/bench/run_hpa_and_openloop.py

Automated runner for:
1. HPA Independence Re-run (18 cells):
   - Resets ml-service deployment to 1 replica before EVERY cell.
   - Recreates/resets HPA and waits for readyReplicas == 1.
   - Logs replicas at start and end.
   - Drains until queue empty (cap 600s).
   - Records requests accepted (202), 503s, completed k6, completed drain, pending end, failed, drain time.

2. Open-Loop Suite (12 cells):
   - Tests fixed arrival rates (20 jobs/min and 60 jobs/min) for 2 MB dataset.
   - Compares fixed1 vs HPA across 3 repetitions.
   - Uses k6 constant-arrival-rate executor.
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

sys.stdout.reconfigure(line_buffering=True)

RESULTS_DIR = os.path.abspath("results")
BENCH_DIR = os.path.abspath("tests/bench")
DATA_DIR = os.path.join(BENCH_DIR, "data")
K6_TARGET_URL = "http://host.docker.internal:30081"


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def run_cmd(cmd, timeout=120):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def truncate_db():
    run_cmd(["docker", "compose", "exec", "-T", "mysql", "mysql", "-uroot", "-proot_pass", "-e", "TRUNCATE TABLE epinet.analysis_run;"])


def reset_hpa_to_single_replica(max_retries=5):
    """Ensures HPA is recreated and ml-service has exactly 1 ready replica, retrying and aborting if not held."""
    for attempt in range(1, max_retries + 1):
        print(f"  [HPA RESET] Resetting ml-service to 1 replica (attempt {attempt}/{max_retries})...")
        run_cmd(["kubectl", "delete", "hpa", "ml-service-hpa", "--ignore-not-found=true"])
        run_cmd(["kubectl", "scale", "deployment", "ml-service", "--replicas=1"])
        run_cmd(["kubectl", "rollout", "status", "deployment", "ml-service", "--timeout=60s"])
        run_cmd(["kubectl", "apply", "-f", "k8s/hpa.yaml"])
        
        settled = False
        for _ in range(30):
            reps = get_ml_replicas()
            if reps == 1:
                settled = True
                break
            time.sleep(1)
        
        if settled:
            time.sleep(3)
            if get_ml_replicas() == 1:
                print("  [HPA RESET] Replicas successfully verified and settled at 1.")
                return 1
            
        print(f"  [HPA RESET WARNING] Attempt {attempt} failed to settle at 1 replica (current: {get_ml_replicas()}). Retrying...")
        time.sleep(2)
        
    rep_final = get_ml_replicas()
    raise RuntimeError(f"HPA reset failed to achieve 1 replica after {max_retries} attempts. Current replicas: {rep_final}. Aborting cell.")


def get_ml_replicas():
    out, _, _ = run_cmd(["kubectl", "get", "deployment", "ml-service", "-o", "jsonpath={.status.readyReplicas}"])
    try:
        return int(out.strip()) if out.strip() else 0
    except ValueError:
        return 0


def get_kafka_consumer_lag():
    out, err, code = run_cmd([
        "docker", "exec", "epinet-kafka",
        "/opt/kafka/bin/kafka-consumer-groups.sh",
        "--bootstrap-server", "localhost:9092",
        "--describe", "--group", "epinet-ml-workers"
    ])
    if code != 0 or not out:
        return "N/A (docker offline)"
    total_lag = 0
    found = False
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[0] == "epinet-ml-workers":
            try:
                total_lag += int(parts[5])
                found = True
            except (ValueError, IndexError):
                pass
    return total_lag if found else 0


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
            ml_reps = get_ml_replicas()

            pods_out, _, _ = run_cmd(["kubectl", "get", "pods", "-l", "app=ml-service", "-o", "jsonpath={range .items[*]}{.metadata.name},{.status.containerStatuses[0].restartCount},{.status.containerStatuses[0].lastState.terminated.reason}{'\\n'}{end}"])
            restarts_sum = 0
            oom_count = 0
            for line in pods_out.splitlines():
                parts = line.strip().split(",")
                if len(parts) >= 2 and parts[1].isdigit():
                    restarts_sum += int(parts[1])
                if len(parts) >= 3 and "OOM" in parts[2]:
                    oom_count += 1

            wsl_mem, _, _ = run_cmd(["wsl", "free", "-m"])
            free_mb = 0
            avail_mb = 0
            swap_used_mb = 0
            for line in wsl_mem.splitlines():
                if line.startswith("Mem:"):
                    cols = line.split()
                    if len(cols) >= 7:
                        free_mb = int(cols[3])
                        avail_mb = int(cols[6])
                elif line.startswith("Swap:"):
                    cols = line.split()
                    if len(cols) >= 3:
                        swap_used_mb = int(cols[2])

            self.records.append({
                "timestamp_utc": t_str,
                "ml_ready_replicas": ml_reps,
                "ml_restarts": restarts_sum,
                "ml_oom_killed": oom_count,
                "vm_free_mb": free_mb,
                "vm_avail_mb": avail_mb,
                "swap_used_mb": swap_used_mb,
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


def run_k6_closed_loop(csv_file_name: str, vus: int, duration_sec: int, run_id: str):
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
    t0 = time.time()
    out, err, code = run_cmd(cmd, timeout=duration_sec + 180)
    dur = time.time() - t0
    return k6_raw_out, dur


def run_k6_open_loop(rate: int, duration_sec: int, run_id: str):
    k6_raw_out = os.path.join(RESULTS_DIR, f"{run_id}_k6_raw.csv")
    container_csv_path = "/work/tests/bench/data/benchmark_2mb.csv"
    container_out_path = f"/work/results/{run_id}_k6_raw.csv"

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{os.getcwd()}:/work",
        "-w", "/work",
        "-e", f"CSV_PATH={container_csv_path}",
        "-e", f"TARGET_URL={K6_TARGET_URL}",
        "-e", f"RATE={rate}",
        "-e", f"DURATION={duration_sec}s",
        "grafana/k6", "run",
        "--out", f"csv={container_out_path}",
        "/work/tests/bench/open_loop_bench.js"
    ]
    t0 = time.time()
    out, err, code = run_cmd(cmd, timeout=duration_sec + 180)
    dur = time.time() - t0
    return k6_raw_out, dur


def wait_for_queue_drain(timeout_sec: int = 600):
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        out, _, _ = run_cmd(["docker", "compose", "exec", "-T", "mysql", "mysql", "-uroot", "-proot_pass", "-e", "SELECT COUNT(*) FROM epinet.analysis_run WHERE status IN ('PENDING', 'PROCESSING', 'UPLOADED');"])
        lines = out.splitlines()
        if len(lines) > 1 and lines[1].strip() == "0":
            return round(time.time() - t0, 2), True
        time.sleep(1)
    return round(time.time() - t0, 2), False


def extract_database_jobs(out_path: str):
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
    return rows


def parse_k6_stats(k6_path: str):
    req_202 = 0
    req_503 = 0
    if not os.path.exists(k6_path):
        return 0, 0
    with open(k6_path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if not row or len(row) < 14:
                continue
            if row[0] == "http_reqs":
                status = row[13]
                if status == "202":
                    req_202 += 1
                elif status == "503":
                    req_503 += 1
    return req_202, req_503


def parse_job_stats(job_rows, k6_end_dt):
    comp_k6 = 0
    comp_drain = 0
    pending_end = 0
    failed = 0
    
    for r in job_rows:
        st = r.get("status")
        comp_at_str = r.get("completed_at")
        if st == "COMPLETED" and comp_at_str and comp_at_str != "NULL":
            try:
                # Handle MySQL datetime format: YYYY-MM-DD HH:MM:SS.ffffff
                comp_dt = datetime.datetime.fromisoformat(comp_at_str).replace(tzinfo=datetime.timezone.utc)
                if comp_dt <= k6_end_dt:
                    comp_k6 += 1
                else:
                    comp_drain += 1
            except Exception:
                comp_drain += 1
        elif st in ("PENDING", "PROCESSING", "UPLOADED"):
            pending_end += 1
        elif st == "FAILED":
            failed += 1
            
    return comp_k6, comp_drain, pending_end, failed


def run_hpa_suite(duration=30):
    load_levels = [
        (2, 1),
        (2, 2),
        (2, 4),
        (2, 8),
        (10, 1),
        (10, 2),
    ]
    reps = 3
    total_runs = len(load_levels) * reps
    expected_duration = total_runs * (duration + 15 + 10)  # run + drain + reset
    
    print("\n" + "=" * 80)
    print("HPA INDEPENDENT RE-RUN SUITE")
    print(f"  Load levels: {load_levels}")
    print(f"  Repetitions: {reps}")
    print(f"  Total cells: {total_runs}")
    print(f"  Expected duration: ~{expected_duration}s (~{expected_duration / 60.0:.1f} minutes)")
    print("=" * 80)

    t_suite_start = time.time()
    results = []

    for rep in range(1, reps + 1):
        for mb, vus in load_levels:
            t_cell_start = time.time()
            ts = int(time.time())
            run_id = f"kafka_hpa_{mb}mb_{vus}vu_rep{rep}_{ts}"
            print(f"\n--- [HPA CELL START] {run_id} | File={mb}MB | VUs={vus} | Rep={rep} ---")
            
            # Step 1: Ensure HPA reset to 1 replica
            reset_hpa_to_single_replica()
            rep_start = get_ml_replicas()
            if rep_start != 1:
                raise RuntimeError(f"HPA cell start assertion failed: replicas={rep_start} != 1. Aborting run.")
            print(f"  -> Replicas at cell start verified: {rep_start}")

            # Step 2: Clean database
            truncate_db()

            # Step 3: Start monitor
            monitor = SystemMonitor(run_id=run_id)
            monitor.start()

            k6_raw = None
            try:
                # Step 4: Run k6 load
                csv_file = f"benchmark_{mb}mb.csv"
                k6_raw, k6_dur = run_k6_closed_loop(csv_file, vus, duration, run_id)
                k6_end_dt = now_utc()
                print(f"  -> k6 run completed in {k6_dur:.2f}s")

                # Step 5: Drain queue
                drain_time, drained_ok = wait_for_queue_drain(timeout_sec=600)
                print(f"  -> Queue drained: {drained_ok} in {drain_time}s")
            finally:
                monitor.stop()
                sys_out = os.path.join(RESULTS_DIR, f"{run_id}_system.csv")
                monitor.save(sys_out)

            rep_end = get_ml_replicas()
            print(f"  -> Replicas at cell end: {rep_end}")

            # Step 6: Extract jobs
            jobs_out = os.path.join(RESULTS_DIR, f"{run_id}_jobs.csv")
            job_rows = extract_database_jobs(jobs_out)

            # Step 7: Parse stats
            req_202, req_503 = parse_k6_stats(k6_raw) if k6_raw else (0, 0)
            comp_k6, comp_drain, pending_end, failed = parse_job_stats(job_rows, k6_end_dt)
            cell_wall_time = round(time.time() - t_cell_start, 2)

            res = {
                "run_id": run_id,
                "condition": "kafka_hpa",
                "file_mb": mb,
                "vus": vus,
                "rep": rep,
                "replicas_start": rep_start,
                "replicas_end": rep_end,
                "requests_202": req_202,
                "http_503": req_503,
                "jobs_completed_k6": comp_k6,
                "jobs_completed_drain": comp_drain,
                "jobs_pending_end": pending_end,
                "jobs_failed": failed,
                "drain_time_sec": drain_time,
                "wall_time_sec": cell_wall_time,
            }
            results.append(res)
            print(f"[HPA CELL COMPLETE] Wall={cell_wall_time}s | Replicas {rep_start}->{rep_end} | 202s={req_202} | CompK6={comp_k6} | CompDrain={comp_drain} | Pending={pending_end} | Drain={drain_time}s")

    real_suite_dur = round(time.time() - t_suite_start, 2)
    print("\n" + "=" * 80)
    print(f"[HPA SUITE FINISHED] Real Total Duration: {real_suite_dur}s ({real_suite_dur / 60.0:.2f} min)")
    print("=" * 80)
    return results


def run_openloop_suite(duration=30):
    rates = [20, 60]  # arrivals per minute
    conditions = ["kafka_fixed1", "kafka_hpa"]
    reps = 3
    total_runs = len(rates) * len(conditions) * reps
    expected_duration = total_runs * (duration + 15 + 10)

    print("\n" + "=" * 80)
    print("OPEN-LOOP BENCHMARK SUITE (Fixed Arrival Rate)")
    print(f"  Payload: 2 MB")
    print(f"  Arrival Rates: {rates} jobs/min")
    print(f"  Conditions: {conditions}")
    print(f"  Repetitions: {reps}")
    print(f"  Total cells: {total_runs}")
    print(f"  Expected duration: ~{expected_duration}s (~{expected_duration / 60.0:.1f} minutes)")
    print("=" * 80)

    t_suite_start = time.time()
    results = []

    for rep in range(1, reps + 1):
        for cond in conditions:
            # Explicit state reset between conditions
            print(f"\n[CONDITION SETUP] Resetting state for condition: {cond}")
            truncate_db()
            if cond == "kafka_fixed1":
                run_cmd(["kubectl", "set", "env", "deployment/backend", "ANALYSIS_MODE=kafka"])
                run_cmd(["kubectl", "delete", "hpa", "ml-service-hpa", "--ignore-not-found=true"])
                run_cmd(["kubectl", "scale", "deployment", "ml-service", "--replicas=1"])
                run_cmd(["kubectl", "rollout", "status", "deployment", "ml-service", "--timeout=60s"])
            else:
                run_cmd(["kubectl", "set", "env", "deployment/backend", "ANALYSIS_MODE=kafka"])
                reset_hpa_to_single_replica()

            for rate in rates:
                t_cell_start = time.time()
                ts = int(time.time())
                run_id = f"openloop_{cond}_2mb_{rate}rate_rep{rep}_{ts}"
                print(f"\n--- [OPEN-LOOP CELL START] {run_id} | Cond={cond} | Rate={rate}/min | Rep={rep} ---")

                if cond == "kafka_hpa":
                    reset_hpa_to_single_replica()

                rep_start = get_ml_replicas()
                lag_start = get_kafka_consumer_lag()
                print(f"  -> Replicas at cell start: {rep_start} | Kafka Consumer Lag: {lag_start}")

                truncate_db()
                monitor = SystemMonitor(run_id=run_id)
                monitor.start()

                k6_raw = None
                try:
                    k6_raw, k6_dur = run_k6_open_loop(rate, duration, run_id)
                    k6_end_dt = now_utc()
                    print(f"  -> k6 open-loop run finished in {k6_dur:.2f}s")
                    drain_time, drained_ok = wait_for_queue_drain(timeout_sec=600)
                    print(f"  -> Queue drained: {drained_ok} in {drain_time}s")
                finally:
                    monitor.stop()
                    sys_out = os.path.join(RESULTS_DIR, f"{run_id}_system.csv")
                    monitor.save(sys_out)

                rep_end = get_ml_replicas()
                print(f"  -> Replicas at cell end: {rep_end}")

                jobs_out = os.path.join(RESULTS_DIR, f"{run_id}_jobs.csv")
                job_rows = extract_database_jobs(jobs_out)

                req_202, req_503 = parse_k6_stats(k6_raw) if k6_raw else (0, 0)
                comp_k6, comp_drain, pending_end, failed = parse_job_stats(job_rows, k6_end_dt)
                cell_wall_time = round(time.time() - t_cell_start, 2)

                res = {
                    "run_id": run_id,
                    "condition": cond,
                    "type": "open_loop",
                    "rate_per_min": rate,
                    "file_mb": 2,
                    "rep": rep,
                    "replicas_start": rep_start,
                    "replicas_end": rep_end,
                    "requests_202": req_202,
                    "http_503": req_503,
                    "jobs_completed_k6": comp_k6,
                    "jobs_completed_drain": comp_drain,
                    "jobs_pending_end": pending_end,
                    "jobs_failed": failed,
                    "drain_time_sec": drain_time,
                    "wall_time_sec": cell_wall_time,
                }
                results.append(res)
                print(f"[OPEN-LOOP CELL COMPLETE] Wall={cell_wall_time}s | Replicas {rep_start}->{rep_end} | 202s={req_202} | CompK6={comp_k6} | CompDrain={comp_drain} | Pending={pending_end} | Drain={drain_time}s")

    real_suite_dur = round(time.time() - t_suite_start, 2)
    print("\n" + "=" * 80)
    print(f"[OPEN-LOOP SUITE FINISHED] Real Total Duration: {real_suite_dur}s ({real_suite_dur / 60.0:.2f} min)")
    print("=" * 80)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["hpa_only", "openloop_only", "all"], default="all")
    parser.add_argument("--duration", type=int, default=30)
    args = parser.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    
    all_manifest = []
    manifest_file = os.path.join(RESULTS_DIR, f"hpa_openloop_manifest_{int(time.time())}.json")

    if args.mode in ("hpa_only", "all"):
        hpa_res = run_hpa_suite(duration=args.duration)
        all_manifest.extend(hpa_res)

    if args.mode in ("openloop_only", "all"):
        ol_res = run_openloop_suite(duration=args.duration)
        all_manifest.extend(ol_res)

    with open(manifest_file, "w") as f:
        json.dump(all_manifest, f, indent=2)
    print(f"\n[DONE] Manifest written -> {manifest_file}")


if __name__ == "__main__":
    main()
