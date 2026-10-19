# EpiNet Resilience & Benchmark Test Suite

This directory contains automated resilience, fault injection, and performance experiment test scripts for the EpiNet microservices platform under Kubernetes.

---

## Prerequisites

1. **Kubernetes Cluster**: Active `kind` cluster `epinet-cluster` running with NodePort services:
   - Frontend: `http://localhost:30080`
   - Backend: `http://localhost:30081`
   - ML Service: `http://localhost:30082`
2. **Docker / Docker Compose**:
   - `epinet-mysql` on port `3307`
   - `epinet-kafka` on port `9092` / `9094`
3. **Python Environment**: Python 3.12 with dependencies (`pandas`, `numpy`, `urllib`).

---

## Test Scripts & How to Run

### 1. Pod Resilience & Mid-Job Recovery Trials
**Script**: `tests/resilience/test_pod_resilience_new.py`

**Purpose**:
Executes 3 trials of mid-job ML pod kill during active 10 MB CSV linear regression under Kubernetes with guaranteed single-replica stack.

**What it does**:
- Audits and prints `kubectl get hpa` and `kubectl get pods` before every trial to prove exactly 1 ML pod and 1 backend pod are running.
- Pauses / deletes HPA during trials to prevent horizontal auto-scaler replica skew.
- Captures unified UTC clock timestamps:
  * `t_kill`: absolute kill time (`kubectl delete pod <ml-pod> --now`)
  * `t_pod_created`: replacement pod creation timestamp from K8s API
  * `t_container_started`: container start timestamp (`startedAt`)
  * `t_pod_ready`: K8s `Ready=True` condition `lastTransitionTime`
  * `t_terminal`: job status transition to `COMPLETED`
- Measures both:
  * **Terminal from Upload**: from initial `POST /api/uploads` to `COMPLETED` (~14.4 - 15.2s)
  * **Terminal from Kill**: from `kubectl delete pod` to `COMPLETED` (~9.7 - 10.2s)
  * **Replacement Ready**: from kill to pod `Ready` condition (~11.3 - 11.6s)
  * Explains the delta: because the ML container process connects to Kafka immediately upon start (`startedAt` ~1s), the worker fetches the uncommitted job and finishes computation ~1.5s before the K8s kubelet readiness probe flips `Ready: True`.
- Restores HPA manifest upon completion.
- Saves raw output to git-ignored `results/pod_resilience_results.json`.

**Command**:
```powershell
python tests/resilience/test_pod_resilience_new.py
```

---

### 2. Live StuckJobGuard Validation
**Script**: `tests/resilience/test_guard_live.py`

**Purpose**:
Validates that the backend `StuckJobGuard` scheduled background scanner fires live under Kubernetes and transitions stranded jobs to `FAILED` with an informative error message.

**What it does**:
- Temporarily lowers the guard timeout to 5s (`ANALYSIS_STUCK_JOB_PROCESSING_TIMEOUT_SECONDS=5`, `ANALYSIS_STUCK_JOB_PENDING_TIMEOUT_SECONDS=5`, `ANALYSIS_STUCK_JOB_CHECK_INTERVAL_MS=3000`).
- Scales `ml-service` to 0 so the uploaded job remains in `PENDING` state.
- Uploads sample CSV and waits 8 seconds for the live scheduled scan to execute.
- Verifies database status transitions to `FAILED` and captures the stored error message: `Job timed out after 5s in PENDING state (stuck job guard)`.
- Dumps live backend pod logs showing `StuckJobGuard` firing.
- Fully restores `StuckJobGuard` to default configuration (`300s` PROCESSING timeout, `1800s` PENDING timeout, `30000ms` interval) and scales `ml-service` back to 1.
- Saves raw output to git-ignored `results/guard_live_results.json`.

**Command**:
```powershell
python tests/resilience/test_guard_live.py
```

---

### 3. CPU Thread-Oversubscription Benchmark
**Script**: `tests/resilience/test_cpu_experiment.py`

**Purpose**:
Evaluates the thread-oversubscription hypothesis under Kubernetes CPU CFS quota limits (`1500m`).

**What it does**:
- Tests 40 MB linear regression 3x each across 4 conditions at 1500m limit:
  * `unset` (default, 16 threads competing across 1.5 cores)
  * `threads=1` (`OMP_NUM_THREADS=1, OPENBLAS_NUM_THREADS=1`)
  * `threads=2` (`OMP_NUM_THREADS=2, OPENBLAS_NUM_THREADS=2`)
  * `threads=4` (`OMP_NUM_THREADS=4, OPENBLAS_NUM_THREADS=4`)
- Tests 40 MB linear regression directly in Docker / Compose (no CPU limit, all 16 host cores).
- Measures for every single run:
  * `compute_sec` from ML worker STAGE TIMING logs
  * `delta_user_sec` from `/sys/fs/cgroup/cpu.stat`
  * `delta_sys_sec` from `/sys/fs/cgroup/cpu.stat`
  * `delta_nr_throttled` (CFS throttled periods) from `/sys/fs/cgroup/cpu.stat`
  * `delta_throttled_sec` from `/sys/fs/cgroup/cpu.stat`
- Saves raw output to git-ignored `results/cpu_experiment_results.json`.

**Key Empirical Findings**:
- Inside the ML container, `nproc` reports **16** cores (inherited from host Docker VM). Without explicit thread limiting, OpenMP (`libgomp`) and OpenBLAS allocate 16 worker threads.
- Under a 1500m (1.5 core) CFS quota, running 16 threads causes severe spinlock contention: system CPU time exceeds user CPU time by 2.3x (`sys=13.45s` vs `user=5.82s`), generating **121.7 throttled periods** and a compute time of **12.98s**.
- Pinning `OMP_NUM_THREADS=1, OPENBLAS_NUM_THREADS=1` completely eliminates CPU throttling (**0 throttled periods**), drops system CPU to **0.07s**, and accelerates 40 MB compute to **1.98s** (a 6.5x speedup).
- Optimal setting (`OMP_NUM_THREADS=1, OPENBLAS_NUM_THREADS=1`) is committed directly to `k8s/ml-service-deployment.template.yaml`.

**Command**:
```powershell
python tests/resilience/test_cpu_experiment.py
```

---

## Important Architectural Notes

1. **Consumer Lag in Kubernetes Mode**:
   > [!NOTE]
   > In Kubernetes mode, consumer lag is not monitored via Prometheus because `kafka-exporter` is stopped. Cluster performance and queue behavior are instead directly measured via:
   > - `STAGE TIMING: queue_wait=...s` emitted on every message by the ML service consumer loop.
   > - Kernel CFS scheduler metrics via `/sys/fs/cgroup/cpu.stat` (`nr_throttled`, `throttled_usec`, `user_usec`, `system_usec`).
   > - Pod lifecycle condition timestamps (`lastTransitionTime`).
   > - Spring Boot actuator metrics (`/actuator/prometheus`).

2. **Frontend Pod Recreation Rationale**:
   During initial stack deployment, Nginx in the frontend pod started before CoreDNS and backend pod endpoints were fully ready, encountering `host not found in upstream "backend"` and crashing into `CrashLoopBackOff`. Once backend became healthy, deleting the frontend pod allowed Kubernetes to recreate a fresh pod that resolved `backend:8080` immediately.

3. **StuckJobGuard Production Configuration**:
   The backend `StuckJobGuard` runs with decoupled timeouts:
   - `analysis.stuck-job.processing-timeout-seconds=300` (5 minutes for orphaned computations)
   - `analysis.stuck-job.pending-timeout-seconds=1800` (30 minutes to allow queue absorption during ML outages)
   - `analysis.stuck-job.check-interval-ms=30000` (runs every 30 seconds)
