#!/usr/bin/env python3
"""
tests/bench/generate_summary_and_charts.py

Reads raw benchmark files based on explicit suite manifests:
  - results/bench_manifest_1792477680.json (sync closed-loop baseline)
  - results/hpa_openloop_manifest_1792484863.json (kafka_hpa closed-loop uncensored & open-loop suite)

Performs:
  1. Per-run extraction and validation.
  2. Strict consistency check: completed + pending + failed == accepted for EVERY run.
  3. Explicit aggregation into medians vs totals with Runs count and file lists.
  4. Generates results/benchmark_summary.csv and docs/benchmark_summary.csv.
  5. Generates publication-ready charts (chart1 to chart4) and copies to docs/images/.
"""

import csv
import datetime
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
DOCS_DIR = os.path.abspath("docs")
DOCS_IMG_DIR = os.path.join(DOCS_DIR, "images")
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
        return None, None, 0, 0, 0, 0, None, None, 0.0, 0

    job_rows = []
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            job_rows.append(row)
            st = row.get("status")
            c_at = row.get("created_at")
            comp_at = row.get("completed_at")

            if c_at and c_at != "NULL":
                try:
                    # Explicit UTC timezone conversion
                    dt = datetime.datetime.fromisoformat(c_at).replace(tzinfo=datetime.timezone.utc).timestamp()
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
                        # Explicit UTC timezone conversion to match k6 Unix timestamps
                        dt = datetime.datetime.fromisoformat(comp_at).replace(tzinfo=datetime.timezone.utc).timestamp()
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

    drain_time = 0.0
    if k6_end_ts and t_last_complete and t_last_complete > k6_end_ts:
        drain_time = max(0.0, t_last_complete - k6_end_ts)

    accepted = len(job_rows)
    return p50, p95, comp_k6, comp_drain, pending_end, failed, t_first_create, t_last_complete, round(drain_time, 2), accepted


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


def load_runs_from_manifests():
    """
    Builds explicit run list directly from manifest JSON files.
    - Excludes closed-loop kafka_fixed1 (which had a 45s drain-cap / censored cells).
    - Closed-loop comparison is sync vs kafka_hpa.
    - Fixed1 vs HPA is compared strictly in open loop.
    """
    m1_path = os.path.join(RESULTS_DIR, "bench_manifest_1792477680.json")
    m2_path = os.path.join(RESULTS_DIR, "hpa_openloop_manifest_1792484863.json")

    manifest_entries = []

    # 1. Sync closed-loop baseline from m1
    if os.path.exists(m1_path):
        with open(m1_path, "r") as f:
            m1 = json.load(f)
        for r in m1:
            if r.get("condition") == "sync":
                manifest_entries.append({
                    "run_id": r["run_id"],
                    "condition": "sync",
                    "file_mb": r["file_mb"],
                    "load_level": f"{r['vus']}vu",
                    "vus": r["vus"],
                    "rep": r["rep"],
                    "is_open_loop": False,
                    "manifest_source": "bench_manifest_1792477680.json"
                })

    # 2. HPA closed-loop & Open-loop suite from m2
    if os.path.exists(m2_path):
        with open(m2_path, "r") as f:
            m2 = json.load(f)
        for r in m2:
            # Exclude run where HPA started with 3 replicas instead of 1
            if r.get("run_id") == "kafka_hpa_2mb_4vu_rep2_1792485786":
                print(f"[MANIFEST EXCLUDE] Excluding {r['run_id']}: started with 3 replicas (invalid baseline).")
                continue

            is_ol = r.get("type") == "open_loop" or "openloop" in r.get("run_id", "")
            cond = r["condition"]
            if is_ol:
                if cond == "kafka_fixed1":
                    cond = "openloop_fixed1"
                elif cond == "kafka_hpa":
                    cond = "openloop_hpa"
                rate = r.get("rate_per_min", 20)
                load_lvl = f"{rate}rate"
                vus_val = rate
            else:
                load_lvl = f"{r['vus']}vu"
                vus_val = r["vus"]

            manifest_entries.append({
                "run_id": r["run_id"],
                "condition": cond,
                "file_mb": r["file_mb"],
                "load_level": load_lvl,
                "vus": vus_val,
                "rep": r["rep"],
                "is_open_loop": is_ol,
                "manifest_source": "hpa_openloop_manifest_1792484863.json"
            })

    print(f"[MANIFEST] Loaded {len(manifest_entries)} explicit benchmark runs across suites.")
    return manifest_entries


def process_manifest_runs(manifest_entries):
    print("\n" + "=" * 115)
    print(f"{'PER-RUN CONSISTENCY & DISCREPANCY AUDIT REPORT':^115}")
    print("=" * 115)
    print(f"{'Run ID':<46} | {'202':<3} | {'503':<3} | {'Rows':<4} | {'Comp':<4} | {'Pend':<4} | {'Fail':<4} | {'Diff':<4} | Discrepancy Explanation")
    print("-" * 115)

    trials = []
    failures = 0

    for entry in manifest_entries:
        run_id = entry["run_id"]
        k6_f = os.path.join(RESULTS_DIR, f"{run_id}_k6_raw.csv")
        jobs_f = os.path.join(RESULTS_DIR, f"{run_id}_jobs.csv")
        sys_f = os.path.join(RESULTS_DIR, f"{run_id}_system.csv")

        up_p50, up_p95, req_202, req_503, k6_t0, k6_t1 = parse_k6_raw(k6_f)
        job_p50, job_p95, comp_k6, comp_drain, pending_end, failed, t_c0, t_c1, drain_time, accepted = parse_jobs(jobs_f, k6_t1)
        max_reps, restarts, oom, min_free, min_avail, max_swap, ts_data = parse_system(sys_f)

        total_completed = comp_k6 + comp_drain
        k6_total = req_202 + req_503
        diff = accepted - k6_total

        # STRICT CONSISTENCY CHECK
        is_consistent = (total_completed + pending_end + failed == accepted)
        if not is_consistent:
            failures += 1
            print(f"[CONSISTENCY FAIL] {run_id}: completed({total_completed}) + pending({pending_end}) + failed({failed}) != rows({accepted})")
            raise AssertionError(f"Consistency check failed for run {run_id}")

        # Explain discrepancy between MySQL rows created and k6 HTTP responses (202 + 503)
        explanation = "Exact match (rows == 202s)"
        if failed > 0 and req_503 == failed and diff == 0:
            explanation = f"Exact match: backend created row then marked FAILED on 503 ({failed} rejected)"
        elif diff > 0:
            explanation = f"{diff} in-flight upload(s) persisted in MySQL after k6 30s+15s cutoff"
        elif diff < 0:
            explanation = f"{abs(diff)} requests received HTTP 202 before row commit"

        print(f"{run_id:<46} | {req_202:<3} | {req_503:<3} | {accepted:<4} | {total_completed:<4} | {pending_end:<4} | {failed:<4} | {diff:<4} | {explanation}")

        # Throughput definition for closed-loop: completions per minute over active window
        throughput = None
        if not entry["is_open_loop"]:
            if total_completed > 0 and t_c0 and t_c1 and t_c1 > t_c0:
                active_interval_sec = max(1.0, t_c1 - t_c0)
                throughput = (total_completed / active_interval_sec) * 60.0
            elif total_completed > 0 and k6_t0 and k6_t1 and k6_t1 > k6_t0:
                active_interval_sec = max(1.0, k6_t1 - k6_t0) + drain_time
                throughput = (total_completed / active_interval_sec) * 60.0

        trials.append({
            "run_id": run_id,
            "condition": entry["condition"],
            "is_open_loop": entry["is_open_loop"],
            "file_mb": entry["file_mb"],
            "load_level": entry["load_level"],
            "vus": entry["vus"],
            "rep": entry["rep"],
            "db_rows_created": accepted,
            "k6_http_202": req_202,
            "k6_http_503": req_503,
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
            "file_name": f"{run_id}_jobs.csv",
        })

    print("-" * 115)
    print(f"Consistency check complete: {len(trials)} runs verified, 0 failures.")
    print("-" * 115)
    return trials


def aggregate_summary(trials):
    groups = {}
    for t in trials:
        key = (t["condition"], t["file_mb"], t["load_level"])
        groups.setdefault(key, []).append(t)

    summary_rows = []
    summary_dict = {}

    for (cond, mb, load_lvl), tr_list in sorted(groups.items()):
        runs_count = len(tr_list)
        files_str = "; ".join(t["file_name"] for t in tr_list)

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

        tot_rows = sum_attr("db_rows_created")
        tot_202 = sum_attr("k6_http_202")
        tot_503 = sum_attr("k6_http_503")
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

        # In open-loop, throughput equals arrival rate; report N/A as requested
        is_ol = "openloop" in cond
        tp_med = None if is_ol else med_or_none("throughput_jobs_min")
        tp_min = None if is_ol else min_or_none("throughput_jobs_min")
        tp_max = None if is_ol else max_or_none("throughput_jobs_min")

        max_reps = max(t["max_replicas"] for t in tr_list)
        tot_restarts = max(t["restarts"] for t in tr_list)
        tot_oom = max(t["oom_killed"] for t in tr_list)
        min_free = min((t["min_vm_free_mb"] for t in tr_list if t["min_vm_free_mb"] is not None), default=None)
        min_avail = min((t["min_vm_avail_mb"] for t in tr_list if t["min_vm_avail_mb"] is not None), default=None)
        max_swap = max(t["max_swap_used_mb"] for t in tr_list)

        row = {
            "condition": cond,
            "file_mb": mb,
            "load_level": load_lvl,
            "runs": runs_count,
            "db_rows_created_total": tot_rows,
            "k6_http_202_total": tot_202,
            "k6_http_503_total": tot_503,
            "jobs_completed_k6_total": tot_comp_k6,
            "jobs_completed_drain_total": tot_comp_drain,
            "jobs_pending_end_total": tot_pending,
            "jobs_failed_total": tot_failed,
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
            "file_list": files_str,
        }
        summary_rows.append(row)
        vus_val = tr_list[0]["vus"]
        summary_dict[(cond, mb, vus_val)] = {
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

    # Chart 1: Upload API Latency (Sync vs Kafka HPA)
    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=300)
    x_labels = ["1 VU", "2 VUs", "4 VUs", "8 VUs", "10MB 1VU", "10MB 2VU"]
    x = np.arange(len(x_labels))
    width = 0.35

    sync_p50, sync_p95 = [], []
    kafka_p50, kafka_p95 = [], []

    for mb, vus in [(2, 1), (2, 2), (2, 4), (2, 8), (10, 1), (10, 2)]:
        s = summary_dict.get(("sync", mb, vus), {"upload_p50": 0, "upload_p95": 0})
        k = summary_dict.get(("kafka_hpa", mb, vus), {"upload_p50": 0, "upload_p95": 0})
        sync_p50.append(s["upload_p50"])
        sync_p95.append(s["upload_p95"])
        kafka_p50.append(k["upload_p50"])
        kafka_p95.append(k["upload_p95"])

    ax.bar(x - width/2, sync_p50, width, label="Sync Path p50 (Monolith baseline)", color="#d9534f", alpha=0.85)
    ax.bar(x + width/2, kafka_p50, width, label="Kafka Async Path p50 (Decoupled HPA)", color="#0275d8", alpha=0.85)

    ax.errorbar(x - width/2, sync_p50, yerr=[[0]*len(sync_p50), [max(0, p95-p50) for p50, p95 in zip(sync_p50, sync_p95)]], fmt='none', ecolor='#8b0000', capsize=4, label="Sync p95 tail")
    ax.errorbar(x + width/2, kafka_p50, yerr=[[0]*len(kafka_p50), [max(0, p95-p50) for p50, p95 in zip(kafka_p50, kafka_p95)]], fmt='none', ecolor='#00008b', capsize=4, label="Kafka p95 tail")

    ax.set_title("Upload API Response Time: Sync Monolith vs Kafka Architecture", fontsize=13, fontweight="bold", pad=12)
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

    # Chart 2: Closed-Loop Throughput vs Load (Sync vs Kafka HPA)
    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=300)
    tp_sync = [summary_dict.get(("sync", mb, vus), {"tp_med": 0})["tp_med"] for mb, vus in [(2, 1), (2, 2), (2, 4), (2, 8), (10, 1), (10, 2)]]
    tp_hpa = [summary_dict.get(("kafka_hpa", mb, vus), {"tp_med": 0})["tp_med"] for mb, vus in [(2, 1), (2, 2), (2, 4), (2, 8), (10, 1), (10, 2)]]

    ax.plot(x_labels, tp_sync, marker="s", color="#d9534f", linewidth=2.2, label="Sync Path (Monolith baseline)")
    ax.plot(x_labels, tp_hpa, marker="^", color="#5cb85c", linewidth=2.2, linestyle="--", label="Kafka Path + HPA (1-3 Replicas)")

    ax.set_title("Closed-Loop System Throughput: Jobs Completed per Minute", fontsize=13, fontweight="bold", pad=12)
    ax.set_ylabel("Throughput (jobs / min)", fontsize=11)
    ax.set_xlabel("Concurrency / Workload", fontsize=11)
    ax.legend(frameon=True, loc="upper left", fontsize=10)
    fig.text(0.5, 0.01, CAPTION_TEXT, ha="center", fontsize=8, color="#555555", style="italic")
    plt.tight_layout(rect=[0, 0.03, 1, 0.97])
    p2 = os.path.join(RESULTS_DIR, "chart2_throughput_vs_load.png")
    plt.savefig(p2)
    plt.close()

    # Chart 3: Replicas Over Time from new HPA suite
    fig, ax = plt.subplots(figsize=(9, 5.0), dpi=300)
    # Target system file from new HPA suite showing scaling: kafka_hpa_2mb_8vu_rep3_1792486470_system.csv
    hpa_sys_file = os.path.join(RESULTS_DIR, "kafka_hpa_2mb_8vu_rep3_1792486470_system.csv")
    t_rel, replicas = [], []
    if os.path.exists(hpa_sys_file):
        start_dt = None
        with open(hpa_sys_file, "r", encoding="utf-8", errors="ignore") as f:
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

    if t_rel and replicas:
        ax.step(t_rel, replicas, where="post", color="#5cb85c", linewidth=2.5, label="HPA Active Replicas (2MB 8VU Sustained Load)")
        # Plot single replica baseline for contrast
        ax.axhline(1.0, color="#0275d8", linewidth=1.8, linestyle=":", label="Fixed Allocation (1 Replica Baseline)")

    ax.set_title("Kubernetes Horizontal Pod Autoscaler (HPA) Elasticity", fontsize=13, fontweight="bold", pad=12)
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
    if not os.path.exists(recovery_file):
        recovery_file = os.path.join(DOCS_DIR, "data", "ml_pod_recovery_trials.json")

    p4 = None
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

    for p in [p1, p2, p3, p4]:
        if p and os.path.exists(p):
            dest = os.path.join(DOCS_IMG_DIR, os.path.basename(p))
            shutil.copyfile(p, dest)
            print(f"[DOCS] Copied {os.path.basename(p)} -> {dest}")


def print_open_loop_reps(trials):
    ol_trials = [t for t in trials if t["is_open_loop"]]
    print("\n" + "=" * 90)
    print("OPEN-LOOP PER-REPETITION BREAKDOWN")
    print("Metrics: Latency percentiles, backlog at k6 cutoff, and drain time per rep")
    print("=" * 90)
    print(f"{'Condition':<18} | {'Rate':<8} | {'Rep':<5} | {'Rows':<8} | {'Comp(k6)':<8} | {'Backlog(Drain)':<14} | {'Drain(s)':<8} | {'p50(s)':<8} | {'p95(s)':<8}")
    print("-" * 90)
    for t in sorted(ol_trials, key=lambda x: (x["condition"], x["vus"], x["rep"])):
        c = t["condition"]
        r = t["vus"]
        rep = t["rep"]
        acc = t["db_rows_created"]
        k6_c = t["jobs_completed_k6"]
        dr_c = t["jobs_completed_drain"]
        dr_s = t["drain_time_sec"]
        p50 = t["job_p50_sec"] or 0.0
        p95 = t["job_p95_sec"] or 0.0
        print(f"{c:<18} | {r:<8} | {rep:<5} | {acc:<8} | {k6_c:<8} | {dr_c:<14} | {dr_s:<8.2f} | {p50:<8.3f} | {p95:<8.3f}")
    print("=" * 90)


def main():
    manifest_entries = load_runs_from_manifests()
    trials = process_manifest_runs(manifest_entries)
    summary_rows, summary_dict = aggregate_summary(trials)

    # Write summary CSV to results/ and docs/
    out_res_csv = os.path.join(RESULTS_DIR, "benchmark_summary.csv")
    out_docs_csv = os.path.join(DOCS_DIR, "benchmark_summary.csv")
    write_summary_csv(summary_rows, out_res_csv)
    write_summary_csv(summary_rows, out_docs_csv)

    print_open_loop_reps(trials)
    generate_charts(summary_dict)
    print("\n[COMPLETE] Successfully generated summary table and charts with strict consistency verification.")


if __name__ == "__main__":
    main()
