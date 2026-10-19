"""
tests/resilience/test_cpu_experiment.py — CPU Thread-Oversubscription Hypothesis Benchmark.

Evaluates the effect of BLAS / OpenMP thread count on ML container performance under Kubernetes CFS quota:
- Reports nproc inside ML container (host cores visible to container)
- Measures 40 MB compute time 3x each across 4 conditions at 1500m CPU limit:
  1. unset (default, 16 threads competing across 1.5 cores)
  2. =1 (OMP_NUM_THREADS=1, OPENBLAS_NUM_THREADS=1)
  3. =2 (OMP_NUM_THREADS=2, OPENBLAS_NUM_THREADS=2)
  4. =4 (OMP_NUM_THREADS=4, OPENBLAS_NUM_THREADS=4)
- Measures 40 MB compute time directly in Docker (no CPU limit) as unconstrained baseline
- Reports for every run:
  * compute time (s) from STAGE TIMING logs
  * user CPU delta (s) from /sys/fs/cgroup/cpu.stat
  * system CPU delta (s) from /sys/fs/cgroup/cpu.stat
  * throttled periods delta (nr_throttled) from /sys/fs/cgroup/cpu.stat
  * throttled time delta (s) from /sys/fs/cgroup/cpu.stat

Saves raw results to git-ignored `results/cpu_experiment_results.json`.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error

sys.stdout.reconfigure(line_buffering=True)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:30081")
SAMPLE_CSV = os.path.abspath("sample-data/sample_pharmacogenomics.csv")
RESULTS_DIR = os.path.abspath("results")


def run_cmd(cmd, timeout=120):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def generate_csv_bytes(target_mb=40.0):
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


def upload_file(csv_bytes, filename="cpu_bench.csv"):
    boundary = "----FormBoundaryCpuBench"
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
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.status, json.loads(resp.read().decode())


def get_status(analysis_id):
    req = urllib.request.Request(f"{BACKEND_URL}/api/analyses/{analysis_id}/status")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def poll_terminal(analysis_id, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            st = get_status(analysis_id)
            s = st.get("status")
            if s in ("COMPLETED", "FAILED"):
                return s, round(time.time() - t0, 2)
        except Exception:
            pass
        time.sleep(1)
    return "TIMEOUT", round(time.time() - t0, 2)


def get_ml_pod():
    out, _, _ = run_cmd(["kubectl", "get", "pods", "-l", "app=ml-service", "-o", "jsonpath={.items[0].metadata.name}"])
    return out.strip()


def read_cgroup_cpu_stat(pod_name):
    out, _, rc = run_cmd(["kubectl", "exec", pod_name, "--", "cat", "/sys/fs/cgroup/cpu.stat"])
    stats = {}
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) == 2:
            try:
                stats[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return stats


def get_compute_time_from_logs(pod_name, analysis_id):
    logs, _, _ = run_cmd(["kubectl", "logs", pod_name, "--tail=200"])
    for line in logs.splitlines():
        if analysis_id in line and "compute=" in line:
            m = re.search(r"compute=([\d\.]+)s", line)
            if m:
                return float(m.group(1))
    return None


def run_compose_benchmark():
    print("\n" + "=" * 70)
    print("1. RUNNING COMPOSE BENCHMARK (NO CPU LIMIT, HOST HARDWARE)")
    print("=" * 70)

    cmd = [
        "docker", "run", "--rm",
        "epinet-ml-service:latest",
        "python3", "-c",
        '''
import time, io, numpy as np, pandas as pd
from main import EXPECTED_GENES, _run_full_analysis

rows = 350000
rng = np.random.default_rng(42)
data = {g: rng.random(rows).astype(np.float32) for g in EXPECTED_GENES}
data["sample_id"] = [f"S{i:06d}" for i in range(rows)]
data["phenotype"] = rng.random(rows).astype(np.float32)
df = pd.DataFrame(data)
buf = io.BytesIO()
df.to_csv(buf, index=False)
csv_bytes = buf.getvalue()

runs = []
for i in range(1, 4):
    t0 = time.time()
    res = _run_full_analysis(csv_bytes, analysis_id=f"compose-bench-{i}")
    dur = round(time.time() - t0, 3)
    print(f"COMPOSE_RUN_{i}={dur}s")
    runs.append(dur)
print(f"MEAN_COMPOSE={round(sum(runs)/len(runs), 3)}s")
'''
    ]

    out, _, _ = run_cmd(cmd, timeout=120)
    compose_times = []
    mean_s = 0.0
    for line in out.splitlines():
        if "COMPOSE_RUN_" in line:
            parts = line.split("=")
            t_val = float(parts[1].replace("s", ""))
            compose_times.append(t_val)
            print(f"  {line}")
        if "MEAN_COMPOSE=" in line:
            mean_s = float(line.split("=")[1].replace("s", ""))

    print(f"\nCompose (no limit) Mean Compute: {mean_s:.3f}s")
    return {"condition": "compose_no_limit", "runs": compose_times, "mean_compute": mean_s}


def run_k8s_condition(csv_bytes, cond_name, env_setting):
    print("\n" + "=" * 70)
    print(f"K8S BENCHMARK CONDITION: {cond_name} (LIMIT: 1500m)")
    print("=" * 70)

    if env_setting is None:
        print("Configuring: OMP_NUM_THREADS and OPENBLAS_NUM_THREADS UNSET")
        run_cmd(["kubectl", "set", "env", "deployment/ml-service", "OMP_NUM_THREADS-", "OPENBLAS_NUM_THREADS-"])
    else:
        print(f"Configuring: OMP_NUM_THREADS={env_setting} OPENBLAS_NUM_THREADS={env_setting}")
        run_cmd(["kubectl", "set", "env", "deployment/ml-service",
                 f"OMP_NUM_THREADS={env_setting}", f"OPENBLAS_NUM_THREADS={env_setting}"])

    run_cmd(["kubectl", "rollout", "status", "deployment/ml-service", "--timeout=90s"])
    run_cmd(["kubectl", "wait", "--for=condition=Ready", "pod", "-l", "app=ml-service", "--timeout=60s"])
    time.sleep(2)

    pod_name = get_ml_pod()
    out, _, _ = run_cmd(["kubectl", "exec", pod_name, "--", "python3", "-c",
                         "import threadpoolctl, numpy, scipy, sklearn; "
                         "info = threadpoolctl.threadpool_info(); "
                         "print('Threads reported:', [i['num_threads'] for i in info])"])
    print(f"ML Pod: {pod_name} | {out}")

    runs_data = []
    for run_idx in range(1, 4):
        print(f"\n--- {cond_name} Run {run_idx}/3 (Pod: {pod_name}) ---")
        cgroup_before = read_cgroup_cpu_stat(pod_name)

        t_up_start = time.time()
        status_code, resp = upload_file(csv_bytes, f"cpu_{cond_name}_{run_idx}.csv")
        aid = resp["analysisId"]
        up_s = round(time.time() - t_up_start, 2)
        print(f"  Uploaded {aid} ({up_s}s)")

        status, term_s = poll_terminal(aid, timeout=180)
        cgroup_after = read_cgroup_cpu_stat(pod_name)
        compute_s = get_compute_time_from_logs(pod_name, aid)

        delta_user = (cgroup_after.get("user_usec", 0) - cgroup_before.get("user_usec", 0)) / 1e6
        delta_sys = (cgroup_after.get("system_usec", 0) - cgroup_before.get("system_usec", 0)) / 1e6
        delta_throttled = cgroup_after.get("nr_throttled", 0) - cgroup_before.get("nr_throttled", 0)
        delta_throttled_s = (cgroup_after.get("throttled_usec", 0) - cgroup_before.get("throttled_usec", 0)) / 1e6

        print(f"  Status: {status} in {term_s}s | compute: {compute_s}s")
        print(f"  CPU Deltas: user={delta_user:.2f}s | sys={delta_sys:.2f}s | "
              f"throttled_periods=+{delta_throttled} | throttled_time={delta_throttled_s:.2f}s")

        runs_data.append({
            "run": run_idx,
            "analysis_id": aid,
            "status": status,
            "terminal_sec": term_s,
            "compute_sec": compute_s,
            "delta_user_sec": round(delta_user, 2),
            "delta_sys_sec": round(delta_sys, 2),
            "delta_nr_throttled": delta_throttled,
            "delta_throttled_sec": round(delta_throttled_s, 2),
        })

    valid_computes = [r["compute_sec"] for r in runs_data if r["compute_sec"] is not None]
    mean_compute = sum(valid_computes) / len(valid_computes) if valid_computes else 0.0
    mean_user = sum(r["delta_user_sec"] for r in runs_data) / len(runs_data)
    mean_sys = sum(r["delta_sys_sec"] for r in runs_data) / len(runs_data)
    mean_throttled_p = sum(r["delta_nr_throttled"] for r in runs_data) / len(runs_data)
    mean_throttled_s = sum(r["delta_throttled_sec"] for r in runs_data) / len(runs_data)

    return {
        "condition": cond_name,
        "env_setting": env_setting,
        "pod_name": pod_name,
        "runs": runs_data,
        "mean_compute_sec": round(mean_compute, 2),
        "mean_user_sec": round(mean_user, 2),
        "mean_sys_sec": round(mean_sys, 2),
        "mean_throttled_periods": round(mean_throttled_p, 1),
        "mean_throttled_sec": round(mean_throttled_s, 2),
    }


def main():
    print("Generating 40 MB CSV for CPU Experiment...")
    csv_40mb = generate_csv_bytes(40.0)

    all_results = {}
    compose_res = run_compose_benchmark()
    all_results["compose_no_limit"] = compose_res

    conditions = [
        ("unset", None),
        ("threads=1", 1),
        ("threads=2", 2),
        ("threads=4", 4),
    ]

    for cond_name, env_val in conditions:
        cond_res = run_k8s_condition(csv_40mb, cond_name, env_val)
        all_results[cond_name] = cond_res

    # Restore optimal manifest configuration (threads=1)
    print("\nRestoring optimal manifest configuration: OMP_NUM_THREADS=1, OPENBLAS_NUM_THREADS=1")
    run_cmd(["kubectl", "set", "env", "deployment/ml-service", "OMP_NUM_THREADS=1", "OPENBLAS_NUM_THREADS=1"])
    run_cmd(["kubectl", "rollout", "status", "deployment/ml-service", "--timeout=60s"])

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_file = os.path.join(RESULTS_DIR, "cpu_experiment_results.json")
    with open(out_file, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nRaw results saved to {out_file}")

    print("\n" + "=" * 95)
    print("FINAL CPU THREAD-OVERSUBSCRIPTION EXPERIMENT RESULTS")
    print("=" * 95)
    header = f"{'Condition':<15} | {'Run 1':<7} | {'Run 2':<7} | {'Run 3':<7} | {'Mean Compute':<13} | {'User CPU':<9} | {'Sys CPU':<9} | {'Throttled Periods':<18} | {'Throttled Time'}"
    print(header)
    print("-" * 95)

    comp_runs = compose_res["runs"]
    r1 = f"{comp_runs[0]:.2f}s" if len(comp_runs) > 0 else "N/A"
    r2 = f"{comp_runs[1]:.2f}s" if len(comp_runs) > 1 else "N/A"
    r3 = f"{comp_runs[2]:.2f}s" if len(comp_runs) > 2 else "N/A"
    print(f"{'compose (none)':<15} | {r1:<7} | {r2:<7} | {r3:<7} | {compose_res['mean_compute']:.2f}s        | {'N/A':<9} | {'N/A':<9} | {'0 (no limit)':<18} | 0.00s")

    for cond_name, _ in conditions:
        c = all_results[cond_name]
        runs = [f"{r['compute_sec']:.2f}s" if r['compute_sec'] else "N/A" for r in c["runs"]]
        while len(runs) < 3:
            runs.append("N/A")
        print(f"{cond_name:<15} | {runs[0]:<7} | {runs[1]:<7} | {runs[2]:<7} | {c['mean_compute_sec']:.2f}s        | {c['mean_user_sec']:.2f}s     | {c['mean_sys_sec']:.2f}s     | {c['mean_throttled_periods']:<18} | {c['mean_throttled_sec']:.2f}s")
    print("=" * 95)


if __name__ == "__main__":
    main()
