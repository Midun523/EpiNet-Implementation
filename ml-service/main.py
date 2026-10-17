"""
EpiNet Visualizer - ML Microservice
------------------------------------
This service performs classical pairwise interaction regression (epistasis detection)
on pharmacogenomic data.

Method:
For every gene pair (gene_i, gene_j), it compares an additive baseline model:
    phenotype ~ 1 + gene_i + gene_j
against a non-additive interaction model:
    phenotype ~ 1 + gene_i + gene_j + (gene_i * gene_j)
scoring each pair by the delta R^2 (incremental variance explained by the product term).
Scores are symmetrized, zeroed on the diagonal, and scaled to [0, 1].

Architecture note:
This classical regression on synthetic data is designed so a trained graph-attention
or transformer model can be dropped in as future scope.

Phase 1 addition — Kafka integration:
In addition to the existing POST /analyze HTTP endpoint, this service now also
runs a Kafka consumer loop that listens to "analysis.requested" events. When an
event arrives, it fetches the CSV from the Spring Boot backend, runs the analysis
in a thread executor (to avoid blocking the asyncio loop / /health endpoint),
and publishes the result to "analysis.completed".
"""

import asyncio
import io
import json
import logging
import os
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import MinMaxScaler
from starlette.responses import Response

import sys

# Configure logging to write to sys.stdout and to ML_LOG_PATH if set
ML_LOG_PATH = os.getenv("ML_LOG_PATH")
handlers = [logging.StreamHandler(sys.stdout)]
if ML_LOG_PATH:
    os.makedirs(os.path.dirname(os.path.abspath(ML_LOG_PATH)), exist_ok=True)
    handlers.append(logging.FileHandler(ML_LOG_PATH, encoding="utf-8"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", handlers=handlers, force=True)
logger = logging.getLogger("epinet.ml")

# ── Prometheus Observability Metrics (Phase 2) ──────────────────────────────
# WHY: Exposes ML analysis duration histogram, processed jobs counter
# (labelled by status="success" / "failure"), and currently in-progress gauge.
ANALYSIS_DURATION_SECONDS = Histogram(
    "epinet_ml_analysis_duration_seconds",
    "Duration of ML analysis regression computation in seconds",
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0),
)

JOBS_PROCESSED_TOTAL = Counter(
    "epinet_ml_jobs_processed_total",
    "Total count of ML analysis jobs processed",
    ["status"],
)

JOBS_IN_PROGRESS = Gauge(
    "epinet_ml_jobs_in_progress",
    "Number of ML analysis jobs currently being processed",
)

# ── Configuration ────────────────────────────────────────────────────────────
# WHY env vars: lets Docker Compose / Kubernetes override these without editing code.
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8080")
KAFKA_ENABLED = os.getenv("KAFKA_ENABLED", "true").lower() == "true"

# The 10 pharmacogenomic genes this service expects
EXPECTED_GENES = [
    "CYP2D6", "CYP2C19", "CYP3A4", "CYP2C9",
    "VKORC1", "SLCO1B1", "TPMT", "DPYD", "UGT1A1", "ABCB1",
]
TOP_PAIRS_COUNT = 15

# WHY a thread pool: the scikit-learn regression is CPU-bound. Running it in a
# thread executor prevents it from blocking the asyncio event loop, which would
# make the /health endpoint unresponsive during analysis.
_analysis_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analysis")


# ── Core analysis functions (unchanged from original) ────────────────────────

def _compute_interaction_matrix(gene_df: pd.DataFrame, phenotype: pd.Series) -> np.ndarray:
    """
    Computes pairwise epistasis interaction scores using pairwise linear regression with
    an interaction product term (delta R^2).

    For each gene pair (i, j):
      Base additive model: y = b0 + b1*x_i + b2*x_j
      Full epistatic model: y = b0 + b1*x_i + b2*x_j + b3*(x_i * x_j)
      Score = delta R^2 = max(0, (RSS_base - RSS_full) / TSS)
    """
    n = len(EXPECTED_GENES)
    matrix = np.zeros((n, n), dtype=float)

    y = phenotype.values
    tss = float(np.sum((y - np.mean(y)) ** 2))
    if tss == 0:
        tss = 1e-9

    lr_base = LinearRegression()
    lr_full = LinearRegression()

    X_genes = {g: gene_df[g].values for g in EXPECTED_GENES}

    for i in range(n):
        gene_i = EXPECTED_GENES[i]
        x_i = X_genes[gene_i]
        for j in range(i + 1, n):
            gene_j = EXPECTED_GENES[j]
            x_j = X_genes[gene_j]
            x_int = x_i * x_j

            # Base model: [x_i, x_j]
            X_base = np.column_stack([x_i, x_j])
            lr_base.fit(X_base, y)
            pred_base = lr_base.predict(X_base)
            rss_base = float(np.sum((y - pred_base) ** 2))

            # Full model: [x_i, x_j, x_i * x_j]
            X_full = np.column_stack([x_i, x_j, x_int])
            lr_full.fit(X_full, y)
            pred_full = lr_full.predict(X_full)
            rss_full = float(np.sum((y - pred_full) ** 2))

            # Partial variance explained by interaction term (delta R^2)
            delta_r2 = max(0.0, (rss_base - rss_full) / tss)
            matrix[i, j] = delta_r2
            matrix[j, i] = delta_r2

    # Symmetrize and zero diagonal
    np.fill_diagonal(matrix, 0.0)

    # Scale off-diagonal values to [0, 1]
    scaler = MinMaxScaler()
    flat = matrix.flatten().reshape(-1, 1)
    scaled = scaler.fit_transform(flat).reshape(n, n)
    np.fill_diagonal(scaled, 0.0)

    return scaled


def _validate_and_load(content: bytes) -> tuple[pd.DataFrame, pd.Series, int]:
    """Parse CSV, validate columns and variance, return (gene_df, phenotype, rows_processed)."""
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as exc:
        raise ValueError(f"Cannot parse CSV: {exc}")

    df.columns = df.columns.str.strip()

    if "sample_id" not in df.columns:
        raise ValueError("Missing required column: sample_id")
    if "phenotype" not in df.columns:
        raise ValueError("Missing required column: phenotype")

    missing_genes = [g for g in EXPECTED_GENES if g not in df.columns]
    if missing_genes:
        raise ValueError(f"Missing gene columns: {missing_genes}. Expected: {EXPECTED_GENES}")

    if len(df) < 3:
        raise ValueError(
            f"CSV has only {len(df)} row(s). At least 3 rows are required for interaction regression."
        )

    gene_df = df[EXPECTED_GENES].apply(pd.to_numeric, errors="coerce")
    phenotype = pd.to_numeric(df["phenotype"], errors="coerce")

    if gene_df.isnull().all().any():
        raise ValueError("One or more gene columns are entirely non-numeric.")

    # Impute missing cells if any
    gene_df = gene_df.fillna(gene_df.mean())
    phenotype = phenotype.fillna(phenotype.mean())

    # Variance validation: constant columns cause singular matrices
    zero_var_genes = [g for g in EXPECTED_GENES if float(gene_df[g].std(ddof=0)) == 0.0]
    if zero_var_genes:
        raise ValueError(
            f"Zero-variance detected in gene column(s): {zero_var_genes}. All genes must have variance."
        )

    if float(phenotype.std(ddof=0)) == 0.0:
        raise ValueError(
            "Zero-variance detected in phenotype column. Phenotype values must vary across samples."
        )

    return gene_df, phenotype, len(df)


def _run_full_analysis(content: bytes, analysis_id: Optional[str] = None) -> dict[str, Any]:
    """
    Synchronous function that validates CSV and runs the full epistasis analysis.
    WHY a separate function: called from both the HTTP endpoint and the Kafka
    consumer (via run_in_executor) so the logic isn't duplicated.
    """
    t0 = time.time()
    gene_df, phenotype, rows_processed = _validate_and_load(content)

    logger.info("Analyzing %d rows", rows_processed)
    matrix = _compute_interaction_matrix(gene_df, phenotype)

    # Build top pairs list
    pairs: list[dict] = []
    n = len(EXPECTED_GENES)
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append({
                "gene_a": EXPECTED_GENES[i],
                "gene_b": EXPECTED_GENES[j],
                "score": round(float(matrix[i, j]), 4),
            })
    top_pairs = sorted(pairs, key=lambda x: x["score"], reverse=True)[:TOP_PAIRS_COUNT]
    t_compute = time.time() - t0
    logger.info("STAGE TIMING: Job %s compute=%.3fs (rows=%d)", analysis_id, t_compute, rows_processed)

    return {
        "genes": EXPECTED_GENES,
        "matrix": [[round(float(v), 4) for v in row] for row in matrix],
        "top_pairs": top_pairs,
        "meta": {
            "rows_processed": rows_processed,
            "simulated": True,
            "model": "interaction-regression-v1",
        },
    }


class TransientBackendError(ValueError):
    """WHY: Distinguishes transient infrastructure outages (e.g. backend pod restart)
    from permanent data errors (e.g. malformed CSV), preventing premature job failure.
    Inherits from ValueError for backward compatibility with caller exception hierarchies."""
    pass


def _fetch_csv_from_backend(
    analysis_id: str,
    max_attempts: int = 15,
    backoffs: tuple[int, ...] = (2, 3, 5, 5, 5, 5, 5, 5, 10, 10),
    timeout: int = 60,
) -> bytes:
    """
    WHY: Kafka messages only carry the analysisId (not CSV data) to stay under
    Kafka's 1 MB message limit. This fetches the CSV bytes from the backend's
    internal endpoint using stdlib urllib.

    Resilience: Retries over a >= 60 s window (up to 15 attempts, total backoff > 65s)
    to gracefully absorb a full backend JVM pod restart / cold start (~31-35s)
    without terminating the analysis as FAILED.
    """
    url = f"{BACKEND_URL}/api/internal/analyses/{analysis_id}/data"
    req = urllib.request.Request(url)
    last_error: Optional[Exception] = None
    t0 = time.time()

    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read()
                t_fetch = time.time() - t0
                logger.info(
                    "STAGE TIMING: Job %s csv_fetch=%.3fs (size=%.2f MB)",
                    analysis_id, t_fetch, len(data) / (1024 * 1024),
                )
                return data
        except Exception as e:
            last_error = e
            if attempt < max_attempts:
                sleep_sec = backoffs[attempt - 1] if attempt - 1 < len(backoffs) else backoffs[-1]
                logger.warning(
                    "Fetch attempt %d/%d failed for job %s: %s. Retrying in %ds...",
                    attempt, max_attempts, analysis_id, e, sleep_sec,
                )
                time.sleep(sleep_sec)
            else:
                logger.error(
                    "Fetch attempt %d/%d failed for job %s: %s. All retries exhausted.",
                    attempt, max_attempts, analysis_id, e,
                )

    raise TransientBackendError(f"Failed to fetch CSV for {analysis_id} after {max_attempts} attempts: {last_error}")


def _fetch_status_from_backend(analysis_id: str) -> Optional[str]:
    """
    WHY: Idempotency. Before running a 30-second CPU-bound regression, check if
    the job was already finalized (COMPLETED or FAILED). If so, we skip it
    rather than wasting compute on re-delivered or replayed Kafka events.
    """
    url = f"{BACKEND_URL}/api/analyses/{analysis_id}/status"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("status")
    except Exception as e:
        logger.debug("Could not check status for %s: %s", analysis_id, e)
        return None


def _fetch_and_analyze(analysis_id: str) -> dict[str, Any]:
    """
    Synchronous helper: fetch CSV from backend + run analysis.
    WHY a combined function: both steps are blocking I/O + CPU, so they run
    together in the thread pool executor to keep the asyncio loop free.
    """
    csv_bytes = _fetch_csv_from_backend(analysis_id)
    return _run_full_analysis(csv_bytes, analysis_id=analysis_id)


# ── Kafka consumer loop ─────────────────────────────────────────────────────

async def _kafka_consumer_loop():
    """
    WHY THIS FUNCTION EXISTS:
    Listens to "analysis.requested" events from Kafka, runs the analysis in a
    thread executor (because scikit-learn is CPU-bound), and publishes the
    result (or error) to "analysis.completed".

    Retry/DLQ: if the analysis fails, we publish a FAILED event rather than
    letting the message sit unconsumed. This ensures the backend marks the job
    as FAILED and the frontend stops polling.
    """
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

    # WHY retry loop: Kafka may not be ready when the service starts
    consumer = None
    producer = None
    for attempt in range(30):
        try:
            consumer = AIOKafkaConsumer(
                "analysis.requested",
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                group_id="epinet-ml-service",
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                key_deserializer=lambda m: m.decode("utf-8") if m else None,
                auto_offset_reset="earliest",
                # WHY enable_auto_commit=False: guarantees at-least-once processing.
                # Offsets are committed ONLY after the analysis result is published or terminal state confirmed.
                enable_auto_commit=False,
            )
            producer = AIOKafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )
            await consumer.start()
            await producer.start()
            logger.info("Kafka consumer and producer started successfully")
            break
        except Exception as e:
            logger.warning("Kafka not ready (attempt %d/30): %s", attempt + 1, e)
            if consumer:
                try:
                    await consumer.stop()
                except Exception:
                    pass
            if producer:
                try:
                    await producer.stop()
                except Exception:
                    pass
            consumer = None
            producer = None
            await asyncio.sleep(2)
    else:
        logger.error("Could not connect to Kafka after 30 attempts. Consumer loop will not run.")
        return

    loop = asyncio.get_event_loop()

    try:
        async for msg in consumer:
            event = msg.value
            analysis_id = event.get("analysisId")
            if not analysis_id:
                logger.warning("Received event without analysisId: %s", event)
                await consumer.commit()
                continue

            logger.info("Received analysis.requested for %s", analysis_id)

            queue_wait = (time.time() - (msg.timestamp / 1000.0)) if msg.timestamp else 0.0
            logger.info("STAGE TIMING: Job %s queue_wait=%.3fs", analysis_id, queue_wait)

            # WHY skip COMPLETED or FAILED: if Kafka re-delivers an event after a consumer
            # restart or crash, we must not repeat a heavy 30s analysis for a finalized job.
            current_status = await loop.run_in_executor(
                _analysis_executor, _fetch_status_from_backend, analysis_id
            )
            if current_status in ("COMPLETED", "FAILED"):
                logger.info(
                    "Skipping analysis %s: already in terminal state %s",
                    analysis_id, current_status,
                )
                await consumer.commit()
                continue

            JOBS_IN_PROGRESS.inc()
            t_start = time.time()
            try:
                # WHY run_in_executor: scikit-learn regression is CPU-bound.
                # Running it on the asyncio loop would block /health responses.
                result = await loop.run_in_executor(
                    _analysis_executor, _fetch_and_analyze, analysis_id
                )
                duration = time.time() - t_start
                ANALYSIS_DURATION_SECONDS.observe(duration)
                JOBS_PROCESSED_TOTAL.labels(status="success").inc()

                result_json = json.dumps(result)

                completion_event = {
                    "analysisId": analysis_id,
                    "status": "COMPLETED",
                    "resultJson": result_json,
                    "errorMessage": None,
                }
                t_pub_start = time.time()
                await producer.send_and_wait(
                    "analysis.completed",
                    key=analysis_id,
                    value=completion_event,
                )
                t_pub = time.time() - t_pub_start
                logger.info("STAGE TIMING: Job %s result_write_back_publish=%.3fs", analysis_id, t_pub)

                # WHY commit after publish: message is acknowledged only once the downstream
                # event is confirmed written to Kafka. If this pod is killed before this point,
                # Kafka redelivers the message to a replacement worker.
                await consumer.commit()
                logger.info("Committed Kafka offset for completed analysis %s", analysis_id)

            except TransientBackendError as te:
                # Transient error: backend is restarting or temporarily unreachable.
                # Do NOT publish FAILED. Do NOT commit offset so Kafka will redeliver to
                # this or a replacement worker once the backend is ready.
                logger.warning(
                    "Transient backend error for %s: %s. Leaving offset uncommitted for redelivery.",
                    analysis_id, te,
                )
                await asyncio.sleep(5)

            except Exception as e:
                # Permanent data error (e.g. malformed CSV, invalid columns, math failure):
                # Publish FAILED and commit offset so poison messages are not retried in an infinite loop.
                duration = time.time() - t_start
                ANALYSIS_DURATION_SECONDS.observe(duration)
                JOBS_PROCESSED_TOTAL.labels(status="failure").inc()

                logger.error("Permanent analysis failure for %s: %s", analysis_id, str(e), exc_info=True)
                failure_event = {
                    "analysisId": analysis_id,
                    "status": "FAILED",
                    "resultJson": None,
                    "errorMessage": str(e),
                }
                try:
                    await producer.send_and_wait(
                        "analysis.completed",
                        key=analysis_id,
                        value=failure_event,
                    )
                    await consumer.commit()
                    logger.info("Published FAILED event and committed offset for %s", analysis_id)
                except Exception as pub_err:
                    logger.error(
                        "Could not publish FAILED event for %s: %s",
                        analysis_id, pub_err,
                    )
            finally:
                JOBS_IN_PROGRESS.dec()
    except asyncio.CancelledError:
        logger.info("Kafka consumer loop cancelled (shutdown)")
    except Exception as e:
        logger.error("Kafka consumer loop crashed: %s", e, exc_info=True)
    finally:
        await consumer.stop()
        await producer.stop()
        logger.info("Kafka consumer and producer stopped")


# ── FastAPI app with lifespan ────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    WHY lifespan: starts the Kafka consumer loop as a background task when the
    app starts, and cancels it on shutdown. This is the modern FastAPI pattern
    (replaces on_startup/on_shutdown events).
    """
    kafka_task = None
    if KAFKA_ENABLED:
        kafka_task = asyncio.create_task(_kafka_consumer_loop())
        logger.info("Kafka consumer background task started")
    else:
        logger.info("Kafka disabled (KAFKA_ENABLED=false), HTTP-only mode")

    yield  # App is running

    if kafka_task and not kafka_task.done():
        kafka_task.cancel()
        try:
            await kafka_task
        except asyncio.CancelledError:
            pass
    logger.info("ML service shutdown complete")


app = FastAPI(
    title="EpiNet ML Microservice",
    description="Pairwise interaction regression epistasis scorer.",
    version="1.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── HTTP endpoints (preserved from original) ────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    """
    Prometheus metrics endpoint.

    WHY: Exposes Prometheus scrape metrics for the ML service, including
    epinet_ml_analysis_duration_seconds (histogram), epinet_ml_jobs_processed_total
    (counter), and epinet_ml_jobs_in_progress (gauge).
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)) -> dict[str, Any]:
    """
    Accept a pharmacogenomic CSV, run pairwise interaction regression,
    and return an epistasis interaction matrix plus top pair scores.

    WHY this endpoint is KEPT: the sync analysis path (analysis.mode=sync)
    uses it via direct HTTP. Both paths coexist for Phase 5 benchmarking.
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted.")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    JOBS_IN_PROGRESS.inc()
    t_start = time.time()
    try:
        # WHY run_in_executor: same CPU-bound reason as the Kafka path.
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_analysis_executor, _run_full_analysis, content)
        duration = time.time() - t_start
        ANALYSIS_DURATION_SECONDS.observe(duration)
        JOBS_PROCESSED_TOTAL.labels(status="success").inc()
        return result
    except ValueError as e:
        duration = time.time() - t_start
        ANALYSIS_DURATION_SECONDS.observe(duration)
        JOBS_PROCESSED_TOTAL.labels(status="failure").inc()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        duration = time.time() - t_start
        ANALYSIS_DURATION_SECONDS.observe(duration)
        JOBS_PROCESSED_TOTAL.labels(status="failure").inc()
        raise
    finally:
        JOBS_IN_PROGRESS.dec()
