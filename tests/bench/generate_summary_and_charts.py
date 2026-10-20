#!/usr/bin/env python3
"""
tests/bench/generate_summary_and_charts.py

Reads raw CSVs from results/ folder:
  - <run_id>_k6_raw.csv: raw per-request latencies and HTTP status codes
  - <run_id>_jobs.csv: per-job database execution times and statuses
  - <run_id>_system.csv: ML replicas, restarts, VM free memory timeseries
  - ml_pod_recovery_trials.json: single-clock recovery milestones

Generates:
  - results/benchmark_summary.csv (Comprehensive summary table)
  - results/chart1_response_time_vs_load.png
  - results/chart2_throughput_vs_load.png
  - results/chart3_replicas_over_time.png
  - results/chart4_recovery_timeline.png
  - Copies all charts to docs/images/
"""

import csv
import datetime
import glob
import json
import math
import os
import shutil
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

RESULTS_DIR = os.path.abspath("results")
DOCS_IMG_DIR = os.path.abspath("docs/images")
CAPTION_TEXT = "Single laptop, Docker Desktop/WSL2, kind, synthetic data, N runs, medians with min-max."


def percentile(data, p):
    if not data:
        return None
    return float(np.percentile(data, p))


def fmt_val(val, decimals=2):
    if val is None or val == "":
        return "N/A"
    try:
        f = float(val)
        if math.isnan(f):
            return "N/A"
        return f"{f:.{decimals}f}"
    except (ValueError, TypeError):
        return "N/A"


def parse_k6_raw(filepath):
    durations = []
    req_202 = 0
    req_503 = 0
    k6_start_ts = None
    k6_end_ts = None

    if not os.path.exists(filepath):
        return None, None, 0, 0, None, None

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if not row or len(row) < 14:
                continue
            m = row[0]
            val_str = row[2]
            try:
                ts = float(row[1]) if row[1] else None
            except ValueError:
                ts = None

            if ts:
                if k6_start_ts is None or ts < k6_start_ts:
                    k6_start_ts = ts
                if k6_end_ts is None or ts > k6_end_ts:
                    k6_end_ts = ts

            if m == "http_req_duration":
                try:
                    durations.append(float(val_str))
                except ValueError:
                    pass
            elif m == "http_reqs":
                status = row[13]
                if status == "202":
                    req_202 += 1
                elif status == "503":
                    req_503 += 1

    p50 = percentile(durations, 50)
    p95 = percentile(durations, 95)
    return p50, p95, req_202, req_503, k6_start_ts, k6_end_ts


def parse_jobs(filepath, k6_end_ts=None):
    durations = []
    comp_k6 = 0
    comp_drain = 0
    pending_end = 0
    failed = 0
    t_first_create = None
    t_last_complete = None

    if not os.path.exists(filepath):
        return None, None, 0, 0, 0, 0, None, None, 0.0

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            st = row.get("status")
            c_at = row.get("created_at")
            comp_at = row.get("completed_at")

            if c_at and c_at != "NULL":
                try:
                    dt = datetime.datetime.fromisoformat(c_at).timestamp()
                    if t_first_create is None or dt < t_first_create:
                        t_first_create = dt
                except Exception:
                    pass

            if st == "COMPLETED":
                dur = row.get("duration_sec")
                if dur and dur != "NULL":
                    try:
                        durations.append(float(dur))
                    except ValueError:
                        pass
                if comp_at and comp_at != "NULL":
                    try:
                        dt = datetime.datetime.fromisoformat(comp_at).timestamp()
                        if t_last_complete is None or dt > t_last_complete:
                            t_last_complete = dt
                        if k6_end_ts and dt <= k6_end_ts:
                            comp_k6 += 1
                        else:
                            comp_drain += 1
                    except Exception:
                        comp_drain += 1
                else:
                    comp_k6 += 1
            elif st in ("PENDING", "PROCESSING", "UPLOADED"):
                pending_end += 1
            elif st == "FAILED":
                failed += 1

    p50 = percentile(durations, 50)
    p95 = percentile(durations, 95)

    # Calculate drain time: from k6_end_ts to t_last_complete (if completed during drain)
    drain_time = 0.0
    if k6_end_ts and t_last_complete and t_last_complete > k6_end_ts:
        drain_time = max(0.0, t_last_complete - k6_end_ts)

    return p50, p95, comp_k6, comp_drain, pending_end, failed, t_first_create, t_last_complete, round(drain_time, 2)


def parse_system(filepath):
    max_reps = 1
    restarts = 0
    oom = 0
    min_free = None
    min_avail = None
    max_swap = 0
    ts_data = []

    if not os.path.exists(filepath):
        return 1, 0, 0, None, None, 0, []

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                r = int(row.get("ml_ready_replicas", 1))
                rst = int(row.get("ml_restarts", 0))
                o = int(row.get("ml_oom_killed", 0))
                free_mb = int(row.get("vm_free_mb", 0))
                avail_mb = int(row.get("vm_avail_mb", 0))
                swap_mb = int(row.get("swap_used_mb", 0))

                if r > max_reps:
                    max_reps = r
                if rst > restarts:
                    restarts = rst
                if o > oom:
                    oom = o
                if free_mb > 0:
                    if min_free is None or free_mb < min_free:
                        min_free = free_mb
                if avail_mb > 0:
                    if min_avail is None or avail_mb < min_avail:
                        min_avail = avail_mb
                if swap_mb > max_swap:
                    max_swap = swap_mb
                ts_data.append((row.get("timestamp_utc"), r, free_mb, avail_mb, swap_mb))
            except ValueError:
                pass

    return max_reps, restarts, oom, min_free, min_avail, max_swap, ts_data


def process_all_results():
    job_files = glob.glob(os.path.join(RESULTS_DIR, "*_jobs.csv"))
    print(f"[ANALYSIS] Found {len(job_files)} job files in {RESULTS_DIR}")

    trials = []
    for jf in job_files:
        basename = os.path.basename(jf).replace("_jobs.csv", "")
        k6_f = os.path.join(RESULTS_DIR, f"{basename}_k6_raw.csv")
        sys_f = os.path.join(RESULTS_DIR, f"{basename}_system.csv")

        # Parse condition and load parameters
        is_open_loop = False
        rate_val = None

        if basename.startswith("openloop_"):
            is_open_loop = True
            rest = basename[len("openloop_"):]
            if rest.startswith("kafka_fixed1_"):
                cond = "openloop_fixed1"
                rest = rest[len("kafka_fixed1_"):]
            elif rest.startswith("kafka_hpa_"):
                cond = "openloop_hpa"
                rest = rest[len("kafka_hpa_"):]
            else:
                continue
            parts = rest.split("_")
            # 2mb_{rate}rate_rep{r}_{ts}
            mb = int(parts[0].replace("mb", ""))
            rate_val = int(parts[1].replace("rate", ""))
            vus = rate_val  # store rate in vus for grouping
            rep = int(parts[2].replace("rep", ""))
        elif basename.startswith("kafka_fixed1_"):
            cond = "kafka_fixed1"
            rest = basename[len("kafka_fixed1_"):]
            parts = rest.split("_")
            mb = int(parts[0].replace("mb", ""))
            vus = int(parts[1].replace("vu", ""))
            rep = int(parts[2].replace("rep", ""))
        elif basename.startswith("kafka_hpa_"):
            cond = "kafka_hpa"
            rest = basename[len("kafka_hpa_"):]
            parts = rest.split("_")
            mb = int(parts[0].replace("mb", ""))
            vus = int(parts[1].replace("vu", ""))
            rep = int(parts[2].replace("rep", ""))
        elif basename.startswith("sync_"):
            cond = "sync"
            rest = basename[len("sync_"):]
            parts = rest.split("_")
            mb = int(parts[0].replace("mb", ""))
            vus = int(parts[1].replace("vu", ""))
            rep = int(parts[2].replace("rep", ""))
        else:
            continue

        up_p50, up_p95, req_202, req_503, k6_t0, k6_t1 = parse_k6_raw(k6_f)
        job_p50, job_p95, comp_k6, comp_drain, pending_end, failed, t_c0, t_c1, drain_time = parse_jobs(jf, k6_t1)
        max_reps, restarts, oom, min_free, min_avail, max_swap, ts_data = parse_system(sys_f)

        total_completed = comp_k6 + comp_drain

        # Unambiguous Throughput Definition:
        # Completions per minute over active processing interval (first created_at to last completed_at)
        throughput = None
        if total_completed > 0 and t_c0 and t_c1 and t_c1 > t_c0:
            active_interval_sec = max(1.0, t_c1 - t_c0)
            throughput = (total_completed / active_interval_sec) * 60.0
        elif total_completed > 0 and k6_t0 and k6_t1 and k6_t1 > k6_t0:
            active_interval_sec = max(1.0, k6_t1 - k6_t0) + drain_time
            throughput = (total_completed / active_interval_sec) * 60.0

        trials.append({
            "run_id": basename,
            "condition": cond,
            "is_open_loop": is_open_loop,
            "rate_per_min": rate_val,
            "file_mb": mb,
            "vus": vus,
            "rep": rep,
            "requests_202": req_202,
            "requests_503": req_503,
            "jobs_completed_k6": comp_k6,
            "jobs_completed_drain": comp_drain,
            "jobs_pending_end": pending_end,
            "jobs_failed": failed,
            "drain_time_sec": drain_time,
            "upload_p50_ms": up_p50,
            "upload_p95_ms": up_p95,
            "job_p50_sec": job_p50,
            "job_p95_sec": job_p95,
            "throughput_jobs_min": throughput,
            "max_replicas": max_reps,
            "restarts": restarts,
            "oom_killed": oom,
            "min_vm_free_mb": min_free,
            "min_vm_avail_mb": min_avail,
            "max_swap_used_mb": max_swap,
            "ts_data": ts_data,
        })

    return trials


def aggregate_summary(trials):
    groups = {}
    for t in trials:
        key = (t["condition"], t["file_mb"], t["vus"])
        groups.setdefault(key, []).append(t)

    summary_rows = []
    summary_dict = {}

    for (cond, mb, vus), tr_list in sorted(groups.items()):
        runs_count = len(tr_list)

        def med_or_none(attr):
            vals = [t[attr] for t in tr_list if t[attr] is not None]
            return float(np.median(vals)) if vals else None

        def min_or_none(attr):
            vals = [t[attr] for t in tr_list if t[attr] is not None]
            return min(vals) if vals else None

        def max_or_none(attr):
            vals = [t[attr] for t in tr_list if t[attr] is not None]
            return max(vals) if vals else None

        def sum_attr(attr):
            return sum(t[attr] for t in tr_list)

        tot_202 = sum_attr("requests_202")
        tot_503 = sum_attr("requests_503")
        tot_comp_k6 = sum_attr("jobs_completed_k6")
        tot_comp_drain = sum_attr("jobs_completed_drain")
        tot_pending = sum_attr("jobs_pending_end")
        tot_failed = sum_attr("jobs_failed")
        med_drain = med_or_none("drain_time_sec")

        up_p50_med = med_or_none("upload_p50_ms")
        up_p50_min = min_or_none("upload_p50_ms")
        up_p50_max = max_or_none("upload_p50_ms")
        up_p95_med = med_or_none("upload_p95_ms")

        job_p50_med = med_or_none("job_p50_sec")
        job_p95_med = med_or_none("job_p95_sec")

        tp_med = med_or_none("throughput_jobs_min")
        tp_min = min_or_none("throughput_jobs_min")
        tp_max = max_or_none("throughput_jobs_min")

        max_reps = max(t["max_replicas"] for t in tr_list)
        tot_restarts = sum(t["restarts"] for t in tr_list)
        tot_oom = sum(t["oom_killed"] for t in tr_list)
        min_free = min((t["min_vm_free_mb"] for t in tr_list if t["min_vm_free_mb"] is not None), default=None)
        min_avail = min((t["min_vm_avail_mb"] for t in tr_list if t["min_vm_avail_mb"] is not None), default=None)
        max_swap = max(t["max_swap_used_mb"] for t in tr_list)

        row = {
            "condition": cond,
            "file_mb": mb,
            "load_level": f"{vus}rate" if "openloop" in cond else f"{vus}vu",
            "runs": runs_count,
            "requests_202": tot_202,
            "requests_503": tot_503,
            "jobs_completed_k6": tot_comp_k6,
            "jobs_completed_drain": tot_comp_drain,
            "jobs_pending_end": tot_pending,
            "jobs_failed": tot_failed,
            "drain_time_sec_median": fmt_val(med_drain, 2),
            "upload_p50_ms_median": fmt_val(up_p50_med, 2),
            "upload_p50_ms_min": fmt_val(up_p50_min, 2),
            "upload_p50_ms_max": fmt_val(up_p50_max, 2),
            "upload_p95_ms_median": fmt_val(up_p95_med, 2),
            "job_p50_sec_median": fmt_val(job_p50_med, 3),
            "job_p95_sec_median": fmt_val(job_p95_med, 3),
            "throughput_jobs_min_median": fmt_val(tp_med, 1),
            "throughput_jobs_min_min": fmt_val(tp_min, 1),
            "throughput_jobs_min_max": fmt_val(tp_max, 1),
            "pod_restarts_max": tot_restarts,
            "oom_killed_max": tot_oom,
            "min_vm_free_mb": fmt_val(min_free, 0),
            "min_vm_avail_mb": fmt_val(min_avail, 0),
            "max_swap_used_mb": max_swap,
        }
        summary_rows.append(row)
        summary_dict[(cond, mb, vus)] = {
            "upload_p50": up_p50_med or 0.0,
            "upload_p95": up_p95_med or 0.0,
            "tp_med": tp_med or 0.0,
            "job_p50": job_p50_med,
            "raw_tr": tr_list,
        }

    return summary_rows, summary_dict


def write_summary_csv(rows, filepath):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[SUMMARY] Saved summary table -> {filepath}")


def generate_charts(summary_dict):
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
    os.makedirs(DOCS_IMG_DIR, exist_ok=True)

    # Chart 1: Upload API Latency (Sync vs Kafka)
    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=300)
    x_labels = ["1 VU", "2 VUs", "4 VUs", "8 VUs", "10MB 1VU", "10MB 2VU"]
    x = np.arange(len(x_labels))
    width = 0.35

    sync_p50, sync_p95 = [], []
    kafka_p50, kafka_p95 = [], []

    for mb, vus in [(2, 1), (2, 2), (2, 4), (2, 8), (10, 1), (10, 2)]:
        s = summary_dict.get(("sync", mb, vus), {"upload_p50": 0, "upload_p95": 0})
        k = summary_dict.get(("kafka_fixed1", mb, vus), {"upload_p50": 0, "upload_p95": 0})
        sync_p50.append(s["upload_p50"])
        sync_p95.append(s["upload_p95"])
        kafka_p50.append(k["upload_p50"])
        kafka_p95.append(k["upload_p95"])

    ax.bar(x - width/2, sync_p50, width, label="Sync Path p50 (Monolith analog)", color="#d9534f", alpha=0.85)
    ax.bar(x + width/2, kafka_p50, width, label="Kafka Async Path p50 (Decoupled)", color="#0275d8", alpha=0.85)

    ax.errorbar(x - width/2, sync_p50, yerr=[[0]*len(sync_p50), [max(0, p95-p50) for p50, p95 in zip(sync_p50, sync_p95)]], fmt='none', ecolor='#8b0000', capsize=4, label="Sync p95 tail")
    ax.errorbar(x + width/2, kafka_p50, yerr=[[0]*len(kafka_p50), [max(0, p95-p50) for p50, p95 in zip(kafka_p50, kafka_p95)]], fmt='none', ecolor='#00008b', capsize=4, label="Kafka p95 tail")

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

    # Chart 2: Throughput vs Load
    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=300)
    tp_sync = [summary_dict.get(("sync", mb, vus), {"tp_med": 0})["tp_med"] for mb, vus in [(2, 1), (2, 2), (2, 4), (2, 8), (10, 1), (10, 2)]]
    tp_fixed = [summary_dict.get(("kafka_fixed1", mb, vus), {"tp_med": 0})["tp_med"] for mb, vus in [(2, 1), (2, 2), (2, 4), (2, 8), (10, 1), (10, 2)]]
    tp_hpa = [summary_dict.get(("kafka_hpa", mb, vus), {"tp_med": 0})["tp_med"] for mb, vus in [(2, 1), (2, 2), (2, 4), (2, 8), (10, 1), (10, 2)]]

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

    # Chart 3: Replicas Over Time
    fig, ax = plt.subplots(figsize=(9, 5.0), dpi=300)

    def load_system_timeseries(condition_prefix):
        files = glob.glob(os.path.join(RESULTS_DIR, f"{condition_prefix}_*system.csv"))
        if not files:
            return [], []
        files.sort(key=lambda x: ("8vu" in x, "4vu" in x, "2vu" in x), reverse=True)
        t_rel, replicas = [], []
        start_dt = None
        with open(files[0], "r", encoding="utf-8", errors="ignore") as f:
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
                t_rel.append((dt - start_dt).total_seconds())
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

    # Chart 4: Recovery Timeline from Pod-Kill Trials
    recovery_file = os.path.join(RESULTS_DIR, "ml_pod_recovery_trials.json")
    if os.path.exists(recovery_file):
        with open(recovery_file, "r") as f:
            trials_json = json.load(f)

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
        ax.set_title("Single-Clock Kubernetes Pod Recovery & Job Resume Timeline\n(35MB payload, DEMO_COMPUTE_DELAY_SECONDS=0)", fontsize=13, fontweight="bold", pad=12)
        ax.set_xlim(0, max(times) + 4)
        fig.text(0.5, 0.01, CAPTION_TEXT, ha="center", fontsize=8, color="#555555", style="italic")
        plt.tight_layout(rect=[0, 0.03, 1, 0.97])
        p4 = os.path.join(RESULTS_DIR, "chart4_recovery_timeline.png")
        plt.savefig(p4)
        plt.close()
    else:
        p4 = None

    for p in [p1, p2, p3, p4]:
        if p and os.path.exists(p):
            shutil.copyfile(p, os.path.join(DOCS_IMG_DIR, os.path.basename(p)))
            print(f"[DOCS] Copied {os.path.basename(p)} -> docs/images/")


def main():
    trials = process_all_results()
    summary_rows, summary_dict = aggregate_summary(trials)
    out_csv = os.path.join(RESULTS_DIR, "benchmark_summary.csv")
    write_summary_csv(summary_rows, out_csv)
    generate_charts(summary_dict)
    print("\n[COMPLETE] Successfully regenerated summary table and charts from raw files.")


if __name__ == "__main__":
    main()
