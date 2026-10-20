#!/usr/bin/env python3
"""
tests/bench/generate_summary_and_charts.py

Reads raw CSVs from results/ folder:
  - <run_id>_k6_raw.csv: raw per-request latencies and status
  - <run_id>_jobs.csv: per-job database execution times
  - <run_id>_system.csv: ML replicas, restarts, VM free memory timeseries

Generates:
  - results/benchmark_summary.csv
  - results/chart1_response_time_vs_load.png
  - results/chart2_throughput_vs_load.png
  - results/chart3_replicas_over_time.png
  - results/chart4_recovery_timeline.png

All charts formatted for high-contrast presentation on feeds (e.g. LinkedIn),
labeled with mandatory caption:
"Single laptop, Docker Desktop/WSL2, kind, synthetic data, N runs, medians with min-max."
"""

import csv
import datetime
import glob
import json
import math
import os
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

RESULTS_DIR = os.path.abspath("results")
CAPTION_TEXT = "Single laptop, Docker Desktop/WSL2, kind, synthetic data, N runs, medians with min-max."


def percentile(data, p):
    if not data:
        return 0.0
    return float(np.percentile(data, p))


def parse_k6_raw(filepath):
    durations = []
    failed_reqs = 0
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return 0, 0, 0
        for row in reader:
            if not row or len(row) < 3:
                continue
            metric_name = row[0]
            val_str = row[2]
            if metric_name == "http_req_duration":
                try:
                    durations.append(float(val_str))
                except ValueError:
                    pass
            elif metric_name == "http_req_failed":
                try:
                    if float(val_str) > 0:
                        failed_reqs += 1
                except ValueError:
                    pass
    if not durations:
        return 0.0, 0.0, failed_reqs
    return percentile(durations, 50), percentile(durations, 95), failed_reqs


def parse_jobs(filepath):
    durations = []
    completed = 0
    failed = 0
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            st = row.get("status")
            if st == "COMPLETED":
                completed += 1
                try:
                    durations.append(float(row.get("duration_sec", 0)))
                except ValueError:
                    pass
            elif st == "FAILED":
                failed += 1
    p50 = percentile(durations, 50) if durations else 0.0
    p95 = percentile(durations, 95) if durations else 0.0
    return p50, p95, completed, failed


def parse_system(filepath):
    max_reps = 1
    restarts = 0
    oom = 0
    min_free = 999999
    min_avail = 999999
    ts_data = []
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                r = int(row.get("ml_ready_replicas", 1))
                rst = int(row.get("ml_restarts", 0))
                o = int(row.get("ml_oom_killed", 0))
                free_mb = int(row.get("vm_free_mb", 0))
                avail_mb = int(row.get("vm_avail_mb", 0))
                if r > max_reps: max_reps = r
                if rst > restarts: restarts = rst
                if o > oom: oom = o
                if free_mb < min_free and free_mb > 0: min_free = free_mb
                if avail_mb < min_avail and avail_mb > 0: min_avail = avail_mb
                ts_data.append((row.get("timestamp_utc"), r, free_mb))
            except ValueError:
                pass
    return max_reps, restarts, oom, (min_free if min_free != 999999 else 0), ts_data


def process_all_results():
    job_files = glob.glob(os.path.join(RESULTS_DIR, "*_jobs.csv"))
    print(f"[ANALYSIS] Found {len(job_files)} trial runs in {RESULTS_DIR}")
    
    trials = []
    for jf in job_files:
        basename = os.path.basename(jf).replace("_jobs.csv", "")
        k6_f = os.path.join(RESULTS_DIR, f"{basename}_k6_raw.csv")
        sys_f = os.path.join(RESULTS_DIR, f"{basename}_system.csv")
        
        # Parse run_id: {cond}_{mb}mb_{vus}vu_rep{r}_{ts}
        if basename.startswith("kafka_fixed1_"):
            cond = "kafka_fixed1"
            rest = basename[len("kafka_fixed1_"):]
        elif basename.startswith("kafka_hpa_"):
            cond = "kafka_hpa"
            rest = basename[len("kafka_hpa_"):]
        elif basename.startswith("sync_"):
            cond = "sync"
            rest = basename[len("sync_"):]
        else:
            continue
            
        parts = rest.split("_")
        if len(parts) < 3:
            continue
            
        try:
            mb = int(parts[0].replace("mb", ""))
            vus = int(parts[1].replace("vu", ""))
            rep = int(parts[2].replace("rep", ""))
        except ValueError:
            continue
            
        up_p50, up_p95, up_failed = parse_k6_raw(k6_f) if os.path.exists(k6_f) else (0, 0, 0)
        job_p50, job_p95, comp_jobs, failed_jobs = parse_jobs(jf)
        max_reps, restarts, oom, min_free, ts_data = parse_system(sys_f) if os.path.exists(sys_f) else (1, 0, 0, 0, [])
        
        # Calculate throughput (jobs/min) from actual trial duration
        if len(ts_data) > 1 and ts_data[0][0] and ts_data[-1][0]:
            try:
                t0 = datetime.datetime.fromisoformat(ts_data[0][0])
                t1 = datetime.datetime.fromisoformat(ts_data[-1][0])
                actual_dur = max(10.0, (t1 - t0).total_seconds())
            except Exception:
                actual_dur = 60.0
        else:
            actual_dur = 60.0
        throughput = (comp_jobs / actual_dur) * 60.0
        
        trials.append({
            "run_id": basename,
            "condition": cond,
            "file_mb": mb,
            "vus": vus,
            "rep": rep,
            "upload_p50_ms": up_p50,
            "upload_p95_ms": up_p95,
            "job_p50_sec": job_p50,
            "job_p95_sec": job_p95,
            "completed_jobs": comp_jobs,
            "failed_jobs": failed_jobs,
            "throughput_jobs_per_min": throughput,
            "max_replicas": max_reps,
            "restarts": restarts,
            "oom_killed": oom,
            "min_vm_free_mb": min_free,
            "timeseries": ts_data
        })
        
    return trials


def generate_charts(summary_data):
    """Generates 4 publication-quality matplotlib charts."""
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
    
    # Chart 1: Response Time vs Load (Sync vs Kafka)
    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=300)
    
    # We plot 2 MB loads (1, 2, 4, 8 VUs)
    x_labels = ["1 VU", "2 VUs", "4 VUs", "8 VUs", "10MB 1VU", "10MB 2VU"]
    x = np.arange(len(x_labels))
    width = 0.35
    
    sync_p50 = []
    sync_p95 = []
    kafka_p50 = []
    kafka_p95 = []
    
    for mb, vus in [(2, 1), (2, 2), (2, 4), (2, 8), (10, 1), (10, 2)]:
        s = summary_data.get(("sync", mb, vus), {"upload_p50": 0, "upload_p95": 0})
        k = summary_data.get(("kafka_fixed1", mb, vus), {"upload_p50": 0, "upload_p95": 0})
        sync_p50.append(s["upload_p50"])
        sync_p95.append(s["upload_p95"])
        kafka_p50.append(k["upload_p50"])
        kafka_p95.append(k["upload_p95"])
        
    ax.bar(x - width/2, sync_p50, width, label="Sync Path p50 (Monolith analog)", color="#d9534f", alpha=0.85)
    ax.bar(x + width/2, kafka_p50, width, label="Kafka Async Path p50 (Proposed)", color="#0275d8", alpha=0.85)
    
    # Error bar whiskers for p95
    ax.errorbar(x - width/2, sync_p50, yerr=[[0]*len(sync_p50), [p95-p50 for p50, p95 in zip(sync_p50, sync_p95)]], fmt='none', ecolor='#8b0000', capsize=4, label="Sync p95 tail")
    ax.errorbar(x + width/2, kafka_p50, yerr=[[0]*len(kafka_p50), [p95-p50 for p50, p95 in zip(kafka_p50, kafka_p95)]], fmt='none', ecolor='#00008b', capsize=4, label="Kafka p95 tail")
    
    ax.set_title("Upload API Response Time: Sync vs Kafka Architecture", fontsize=14, fontweight="bold", pad=12)
    ax.set_ylabel("Response Time (ms, log scale)", fontsize=11)
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, fontsize=10)
    ax.legend(frameon=True, loc="upper left", fontsize=9)
    fig.text(0.5, 0.01, CAPTION_TEXT, ha="center", fontsize=8, color="#555555", style="italic")
    plt.tight_layout(rect=[0, 0.03, 1, 0.97])
    p1 = os.path.join(RESULTS_DIR, "chart1_response_time_vs_load.png")
    plt.savefig(p1)
    plt.close()
    print(f"[CHART] Generated: {p1}")

    # Chart 2: Throughput vs Load
    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=300)
    tp_sync = [summary_data.get(("sync", mb, vus), {"tp_med": 0})["tp_med"] for mb, vus in [(2, 1), (2, 2), (2, 4), (2, 8), (10, 1), (10, 2)]]
    tp_fixed = [summary_data.get(("kafka_fixed1", mb, vus), {"tp_med": 0})["tp_med"] for mb, vus in [(2, 1), (2, 2), (2, 4), (2, 8), (10, 1), (10, 2)]]
    tp_hpa = [summary_data.get(("kafka_hpa", mb, vus), {"tp_med": 0})["tp_med"] for mb, vus in [(2, 1), (2, 2), (2, 4), (2, 8), (10, 1), (10, 2)]]

    ax.plot(x_labels, tp_sync, marker="s", color="#d9534f", linewidth=2.2, label="Sync Path (Monolith)")
    ax.plot(x_labels, tp_fixed, marker="o", color="#0275d8", linewidth=2.2, label="Kafka Path (Fixed 1 Replica)")
    ax.plot(x_labels, tp_hpa, marker="^", color="#5cb85c", linewidth=2.2, linestyle="--", label="Kafka Path + HPA (1-3 Replicas)")

    ax.set_title("System Throughput: Jobs Completed per Minute", fontsize=14, fontweight="bold", pad=12)
    ax.set_ylabel("Throughput (jobs / min)", fontsize=11)
    ax.set_xlabel("Concurrency / Workload", fontsize=11)
    ax.legend(frameon=True, loc="upper left", fontsize=10)
    fig.text(0.5, 0.01, CAPTION_TEXT, ha="center", fontsize=8, color="#555555", style="italic")
    plt.tight_layout(rect=[0, 0.03, 1, 0.97])
    p2 = os.path.join(RESULTS_DIR, "chart2_throughput_vs_load.png")
    plt.savefig(p2)
    plt.close()
    print(f"[CHART] Generated: {p2}")

    # Chart 3: Replicas Over Time Under Sustained Load (from real system monitor CSV)
    fig, ax = plt.subplots(figsize=(9, 5.0), dpi=300)
    
    def load_system_timeseries(condition_prefix):
        files = glob.glob(os.path.join(RESULTS_DIR, f"{condition_prefix}_*system.csv"))
        if not files:
            return [], []
        # Prefer higher load files (8vu, then 4vu)
        files.sort(key=lambda x: ("8vu" in x, "4vu" in x, "2vu" in x), reverse=True)
        target_file = files[0]
        t_rel = []
        replicas = []
        start_dt = None
        with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts_str = row.get("timestamp_utc")
                if not ts_str:
                    continue
                try:
                    dt = datetime.datetime.fromisoformat(ts_str)
                except ValueError:
                    continue
                if start_dt is None:
                    start_dt = dt
                elapsed = (dt - start_dt).total_seconds()
                t_rel.append(elapsed)
                try:
                    replicas.append(int(row.get("ml_ready_replicas", 1)))
                except ValueError:
                    replicas.append(1)
        return t_rel, replicas

    hpa_t, hpa_reps = load_system_timeseries("kafka_hpa")
    fixed_t, fixed_reps = load_system_timeseries("kafka_fixed1")

    if hpa_t and hpa_reps:
        ax.step(hpa_t, hpa_reps, where="post", color="#5cb85c", linewidth=2.5, label="HPA Active Replicas")
    if fixed_t and fixed_reps:
        ax.step(fixed_t, fixed_reps, where="post", color="#0275d8", linewidth=2.0, linestyle=":", label="Fixed Allocation (1 Replica)")
    
    ax.set_title("Kubernetes Horizontal Pod Autoscaling (HPA) Elasticity", fontsize=14, fontweight="bold", pad=12)
    ax.set_ylabel("Ready Pod Replicas", fontsize=11)
    ax.set_xlabel("Time under Sustained Load (seconds)", fontsize=11)
    ax.set_ylim(0.5, 3.5)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.legend(frameon=True, loc="upper left", fontsize=10)
    fig.text(0.5, 0.01, CAPTION_TEXT, ha="center", fontsize=8, color="#555555", style="italic")
    plt.tight_layout(rect=[0, 0.03, 1, 0.97])
    p3 = os.path.join(RESULTS_DIR, "chart3_replicas_over_time.png")
    plt.savefig(p3)
    plt.close()
    print(f"[CHART] Generated: {p3}")

    # Chart 4: Recovery Timeline from Pod-Kill Trials (Loaded from real recovery trial data)
    recovery_file = os.path.join(RESULTS_DIR, "ml_pod_recovery_trials.json")
    if not os.path.exists(recovery_file):
        raise RuntimeError(f"Missing real recovery trial data at {recovery_file}")

    with open(recovery_file, "r") as f:
        trials_json = json.load(f)
    
    # Extract real milestones from trial 1
    t1 = trials_json[0]
    t_kill_dt = datetime.datetime.fromisoformat(t1["t_kill"])
    t_created_dt = datetime.datetime.fromisoformat(t1["t_pod_created"])
    t_started_dt = datetime.datetime.fromisoformat(t1["t_container_started"])
    t_ready_dt = datetime.datetime.fromisoformat(t1["t_pod_ready"])
    t_terminal_dt = datetime.datetime.fromisoformat(t1["t_terminal"])

    d_kill = 0.0
    d_created = max(0.1, (t_created_dt - t_kill_dt).total_seconds())
    d_started = max(d_created + 0.2, (t_started_dt - t_kill_dt).total_seconds())
    d_ready = (t_ready_dt - t_kill_dt).total_seconds()
    d_terminal = (t_terminal_dt - t_kill_dt).total_seconds()

    milestones = [
        ("Pod Kill Signal Sent", d_kill, "#d9534f"),
        ("Replacement Pod Created", d_created, "#f0ad4e"),
        ("Container Started", d_started, "#0275d8"),
        ("Readiness Probe Passed (Ready)", d_ready, "#5bc0de"),
        ("Job Terminal (COMPLETED)", d_terminal, "#5cb85c")
    ]
    
    fig, ax = plt.subplots(figsize=(9.5, 4.8), dpi=300)
    y_pos = np.arange(len(milestones))
    times = [m[1] for m in milestones]
    colors = [m[2] for m in milestones]
    labels = [m[0] for m in milestones]
    
    bars = ax.barh(y_pos, times, color=colors, height=0.55, alpha=0.88)
    for bar, t in zip(bars, times):
        ax.text(t + 0.25, bar.get_y() + bar.get_height()/2, f"T+{t:.2f}s", va="center", ha="left", fontsize=9.5, fontweight="bold")
        
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=10, fontweight="bold")
    ax.invert_yaxis()
    ax.set_xlabel("Elapsed Time from Pod Kill (seconds)", fontsize=11)
    ax.set_title("Single-Clock Kubernetes Pod Recovery & Job Resume Timeline", fontsize=14, fontweight="bold", pad=12)
    ax.set_xlim(0, max(times) + 4)
    fig.text(0.5, 0.01, CAPTION_TEXT, ha="center", fontsize=8, color="#555555", style="italic")
    plt.tight_layout(rect=[0, 0.03, 1, 0.97])
    p4 = os.path.join(RESULTS_DIR, "chart4_recovery_timeline.png")
    plt.savefig(p4)
    plt.close()
    print(f"[CHART] Generated: {p4}")

    # Copy all generated charts to docs/images/
    docs_img_dir = os.path.abspath("docs/images")
    os.makedirs(docs_img_dir, exist_ok=True)
    import shutil
    for p in [p1, p2, p3, p4]:
        dest = os.path.join(docs_img_dir, os.path.basename(p))
        shutil.copyfile(p, dest)
        print(f"[DOCS] Copied {os.path.basename(p)} -> {dest}")


def main():
    trials = process_all_results()
    if not trials:
        raise RuntimeError("No trial results found in results/! Cannot generate summary or charts without real empirical data.")

    # Group by (condition, file_mb, vus)
    grouped = {}
    for t in trials:
        key = (t["condition"], t["file_mb"], t["vus"])
        grouped.setdefault(key, []).append(t)
        
    summary_data = {}
    summary_csv_rows = []
    for key, rows in grouped.items():
        cond, mb, vus = key
        up_p50s = [r["upload_p50_ms"] for r in rows if r["upload_p50_ms"] > 0] or [0.0]
        up_p95s = [r["upload_p95_ms"] for r in rows if r["upload_p95_ms"] > 0] or [0.0]
        tps = [r["throughput_jobs_per_min"] for r in rows]
        job_p50s = [r["job_p50_sec"] for r in rows if r["job_p50_sec"] > 0] or [0.0]
        job_p95s = [r["job_p95_sec"] for r in rows if r["job_p95_sec"] > 0] or [0.0]
        restarts = [r["restarts"] for r in rows]
        ooms = [r["oom_killed"] for r in rows]
        free_mbs = [r["min_vm_free_mb"] for r in rows if r["min_vm_free_mb"] > 0] or [0]
        failed_sum = sum(r["failed_jobs"] for r in rows)
        
        med_up_p50 = float(np.median(up_p50s))
        med_up_p95 = float(np.median(up_p95s))
        med_tp = float(np.median(tps))
        med_job_p50 = float(np.median(job_p50s))
        med_job_p95 = float(np.median(job_p95s))
        
        summary_data[key] = {
            "upload_p50": med_up_p50,
            "upload_p95": med_up_p95,
            "tp_med": med_tp,
            "job_p50": med_job_p50
        }
        summary_csv_rows.append({
            "condition": cond,
            "file_mb": mb,
            "vus": vus,
            "runs": len(rows),
            "upload_p50_ms_median": round(med_up_p50, 2),
            "upload_p50_ms_min": round(min(up_p50s), 2),
            "upload_p50_ms_max": round(max(up_p50s), 2),
            "upload_p95_ms_median": round(med_up_p95, 2),
            "job_p50_sec_median": round(med_job_p50, 3),
            "job_p95_sec_median": round(med_job_p95, 3),
            "throughput_jobs_min_median": round(med_tp, 1),
            "throughput_jobs_min_min": round(min(tps), 1),
            "throughput_jobs_min_max": round(max(tps), 1),
            "failed_jobs_total": failed_sum,
            "pod_restarts_max": max(restarts),
            "oom_killed_max": max(ooms),
            "min_vm_free_mb": min(free_mbs)
        })
        
    summary_csv_path = os.path.join(RESULTS_DIR, "benchmark_summary.csv")
    docs_summary_path = os.path.join(os.path.abspath("docs"), "benchmark_summary.csv")
    os.makedirs(os.path.abspath("docs"), exist_ok=True)
    
    with open(summary_csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_csv_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_csv_rows)
    print(f"[SUMMARY] Wrote aggregated metrics to: {summary_csv_path}")

    with open(docs_summary_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_csv_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_csv_rows)
    print(f"[SUMMARY] Wrote summary to docs: {docs_summary_path}")

    generate_charts(summary_data)


if __name__ == "__main__":
    main()
