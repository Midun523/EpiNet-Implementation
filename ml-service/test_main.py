"""
Pytest tests for the EpiNet ML microservice.
Tests: valid CSV, missing columns, variance checks, shape, symmetry, determinism, epistasis recovery.
"""

import io
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from main import EXPECTED_GENES, app

client = TestClient(app)


def _make_csv(genes=None, rows=20, include_sample_id=True, include_phenotype=True, constant_col=None) -> bytes:
    """Helper to build a valid (or deliberately invalid) CSV."""
    if genes is None:
        genes = EXPECTED_GENES
    rng = np.random.default_rng(42)
    data = {g: rng.random(rows) for g in genes}
    if constant_col and constant_col in data:
        data[constant_col] = np.ones(rows)
    if include_sample_id:
        data["sample_id"] = [f"S{i:03d}" for i in range(rows)]
    if include_phenotype:
        data["phenotype"] = rng.random(rows)
    buf = io.StringIO()
    pd.DataFrame(data).to_csv(buf, index=False)
    return buf.getvalue().encode()


# ──────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ──────────────────────────────────────────────
# Valid CSV → correct contract shape
# ──────────────────────────────────────────────

def test_valid_csv_returns_correct_shape():
    csv_bytes = _make_csv()
    r = client.post("/analyze", files={"file": ("sample.csv", csv_bytes, "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["genes"] == EXPECTED_GENES
    assert len(body["matrix"]) == 10
    assert all(len(row) == 10 for row in body["matrix"])
    assert len(body["top_pairs"]) == 15
    assert body["meta"]["simulated"] is True
    assert body["meta"]["model"] == "interaction-regression-v1"


def test_top_pairs_sorted_descending():
    csv_bytes = _make_csv()
    r = client.post("/analyze", files={"file": ("sample.csv", csv_bytes, "text/csv")})
    pairs = r.json()["top_pairs"]
    scores = [p["score"] for p in pairs]
    assert scores == sorted(scores, reverse=True)


def test_top_pairs_have_required_fields():
    csv_bytes = _make_csv()
    r = client.post("/analyze", files={"file": ("sample.csv", csv_bytes, "text/csv")})
    for pair in r.json()["top_pairs"]:
        assert "gene_a" in pair
        assert "gene_b" in pair
        assert "score" in pair
        assert 0.0 <= pair["score"] <= 1.0


# ──────────────────────────────────────────────
# Matrix symmetry and diagonal
# ──────────────────────────────────────────────

def test_matrix_is_symmetric():
    csv_bytes = _make_csv()
    r = client.post("/analyze", files={"file": ("sample.csv", csv_bytes, "text/csv")})
    mat = np.array(r.json()["matrix"])
    np.testing.assert_allclose(mat, mat.T, atol=1e-6, err_msg="Matrix is not symmetric")


def test_matrix_diagonal_is_zero():
    csv_bytes = _make_csv()
    r = client.post("/analyze", files={"file": ("sample.csv", csv_bytes, "text/csv")})
    mat = np.array(r.json()["matrix"])
    np.testing.assert_array_equal(np.diag(mat), np.zeros(10))


def test_matrix_values_in_range():
    csv_bytes = _make_csv()
    r = client.post("/analyze", files={"file": ("sample.csv", csv_bytes, "text/csv")})
    mat = np.array(r.json()["matrix"])
    assert mat.min() >= 0.0
    assert mat.max() <= 1.0


def test_same_file_gives_same_result():
    """Seeded simulation must be deterministic."""
    csv_bytes = _make_csv()
    r1 = client.post("/analyze", files={"file": ("sample.csv", csv_bytes, "text/csv")})
    r2 = client.post("/analyze", files={"file": ("sample.csv", csv_bytes, "text/csv")})
    assert r1.json()["matrix"] == r2.json()["matrix"]


# ──────────────────────────────────────────────
# Missing / invalid columns & variance → 400
# ──────────────────────────────────────────────

def test_missing_gene_columns_returns_400():
    csv_bytes = _make_csv(genes=EXPECTED_GENES[2:])
    r = client.post("/analyze", files={"file": ("bad.csv", csv_bytes, "text/csv")})
    assert r.status_code == 400
    assert "Missing gene columns" in r.json()["detail"]


def test_missing_sample_id_returns_400():
    csv_bytes = _make_csv(include_sample_id=False)
    r = client.post("/analyze", files={"file": ("bad.csv", csv_bytes, "text/csv")})
    assert r.status_code == 400
    assert "sample_id" in r.json()["detail"]


def test_missing_phenotype_returns_400():
    csv_bytes = _make_csv(include_phenotype=False)
    r = client.post("/analyze", files={"file": ("bad.csv", csv_bytes, "text/csv")})
    assert r.status_code == 400
    assert "phenotype" in r.json()["detail"]


def test_non_csv_file_returns_400():
    r = client.post("/analyze", files={"file": ("data.txt", b"not,a,csv", "text/plain")})
    assert r.status_code == 400


def test_empty_file_returns_400():
    r = client.post("/analyze", files={"file": ("empty.csv", b"", "text/csv")})
    assert r.status_code == 400


def test_too_few_rows_returns_400():
    csv_bytes = _make_csv(rows=2)
    r = client.post("/analyze", files={"file": ("short.csv", csv_bytes, "text/csv")})
    assert r.status_code == 400
    assert "At least 3 rows are required" in r.json()["detail"]


def test_zero_variance_gene_returns_400():
    csv_bytes = _make_csv(constant_col="CYP2D6")
    r = client.post("/analyze", files={"file": ("zero_var.csv", csv_bytes, "text/csv")})
    assert r.status_code == 400
    assert "Zero-variance" in r.json()["detail"]


def test_zero_variance_phenotype_returns_400():
    rng = np.random.default_rng(42)
    data = {g: rng.random(10) for g in EXPECTED_GENES}
    data["sample_id"] = [f"S{i:03d}" for i in range(10)]
    data["phenotype"] = [1.0] * 10
    buf = io.StringIO()
    pd.DataFrame(data).to_csv(buf, index=False)
    r = client.post("/analyze", files={"file": ("const_pheno.csv", buf.getvalue().encode(), "text/csv")})
    assert r.status_code == 400
    assert "phenotype column" in r.json()["detail"]


# ──────────────────────────────────────────────
# Biological recovery of planted epistasis
# ──────────────────────────────────────────────

def test_recovers_planted_epistatic_pairs():
    """Verify that sample CSV with planted CYP2C9*VKORC1 and CYP2D6*CYP3A4 interactions ranks them in top pairs."""
    sample_file = Path(__file__).resolve().parent.parent / "sample-data" / "sample_pharmacogenomics.csv"
    if not sample_file.exists():
        pytest.skip("sample_pharmacogenomics.csv not found")

    content = sample_file.read_bytes()
    r = client.post("/analyze", files={"file": ("sample.csv", content, "text/csv")})
    assert r.status_code == 200
    top_pairs = r.json()["top_pairs"]
    pair_tuples = [(p["gene_a"], p["gene_b"]) for p in top_pairs[:5]]

    # All 3 planted pairs land in the top 5 (specifically ranks 1, 2, and 4)
    planted = [("CYP2C9", "VKORC1"), ("CYP2D6", "CYP3A4"), ("SLCO1B1", "ABCB1")]
    recovered = sum(
        1 for p in planted
        if (p[0], p[1]) in pair_tuples or (p[1], p[0]) in pair_tuples
    )
    assert recovered == 3, f"Expected all 3 planted pairs in top 5, got {pair_tuples}"


# ──────────────────────────────────────────────
# Fetch CSV Retry Resilience
# ──────────────────────────────────────────────

def test_fetch_csv_retry_succeeds_after_one_failure():
    """Verify that _fetch_csv_from_backend retries on transient failure and succeeds on 2nd attempt."""
    import urllib.error
    from unittest.mock import MagicMock, patch
    from main import _fetch_csv_from_backend

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"sample_id,CYP2D6,phenotype\nS1,0.5,1.0\n"
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    side_effects = [
        urllib.error.URLError("timed out"),
        mock_resp,
    ]

    with patch("urllib.request.urlopen", side_effect=side_effects) as mock_urlopen, \
         patch("time.sleep") as mock_sleep:
        data = _fetch_csv_from_backend("test-job-uuid-123", max_attempts=3, backoffs=(2, 5, 10), timeout=60)
        assert data == b"sample_id,CYP2D6,phenotype\nS1,0.5,1.0\n"
        assert mock_urlopen.call_count == 2
        mock_sleep.assert_called_once_with(2)


def test_fetch_csv_fails_after_exhausting_all_retries():
    """Verify that _fetch_csv_from_backend raises ValueError after 3 failed attempts."""
    import urllib.error
    from unittest.mock import patch
    from main import _fetch_csv_from_backend

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")) as mock_urlopen, \
         patch("time.sleep") as mock_sleep:
        with pytest.raises(ValueError, match="Failed to fetch CSV for test-job-uuid-456 after 3 attempts"):
            _fetch_csv_from_backend("test-job-uuid-456", max_attempts=3, backoffs=(2, 5, 10), timeout=60)
        assert mock_urlopen.call_count == 3
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list == [((2,),), ((5,),)]


def test_transient_backend_error_seeks_back_and_does_not_publish_failed_or_commit():
    """
    WHY: Item 1 test. Verifies that when _fetch_csv_from_backend raises TransientBackendError,
    the consumer seeks back to the message offset, does NOT publish a FAILED event to
    'analysis.completed', and does NOT commit the consumer offset.
    This proves the consumer retries indefinitely (blocking the partition) rather than
    abandoning the job as FAILED.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from aiokafka import TopicPartition
    from main import TransientBackendError, _kafka_consumer_loop

    # Create mock message
    msg = MagicMock()
    msg.topic = "analysis.requested"
    msg.partition = 1
    msg.offset = 42
    msg.value = {"analysisId": "test-transient-123"}

    # Mock consumer
    consumer = MagicMock()
    consumer.start = AsyncMock()
    consumer.stop = AsyncMock()
    consumer.commit = AsyncMock()
    consumer.seek = MagicMock()

    # Async generator that yields msg once, then breaks by raising asyncio.CancelledError
    async def msg_generator():
        yield msg
        raise asyncio.CancelledError()

    consumer.__aiter__ = lambda self: msg_generator()

    # Mock producer
    producer = MagicMock()
    producer.start = AsyncMock()
    producer.stop = AsyncMock()
    producer.send_and_wait = AsyncMock()

    with patch("aiokafka.AIOKafkaConsumer", return_value=consumer), \
         patch("aiokafka.AIOKafkaProducer", return_value=producer), \
         patch("main._fetch_status_from_backend", return_value=None), \
         patch("main._fetch_csv_from_backend", side_effect=TransientBackendError("Connection refused")), \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        try:
            asyncio.run(_kafka_consumer_loop())
        except asyncio.CancelledError:
            pass

        # 1. Consumer seek MUST be called with partition and offset
        consumer.seek.assert_called_once_with(TopicPartition("analysis.requested", 1), 42)

        # 2. Producer send_and_wait MUST NOT be called (never publish FAILED)
        producer.send_and_wait.assert_not_called()

        # 3. Consumer commit MUST NOT be called (message remains uncommitted)
        consumer.commit.assert_not_called()


def test_transient_backend_error_publishes_failed_and_commits_when_timeout_exceeded():
    """
    WHY: Item 2 test. Verifies that when transient failures persist beyond
    TRANSIENT_RETRY_TIMEOUT_SECONDS for the same message, the consumer breaks the
    retry loop, publishes a FAILED event to 'analysis.completed', and commits
    the consumer offset so subsequent messages on the same partition are not blocked.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from aiokafka import TopicPartition
    from main import TransientBackendError, _kafka_consumer_loop

    msg = MagicMock()
    msg.topic = "analysis.requested"
    msg.partition = 1
    msg.offset = 42
    msg.timestamp = 1000000
    msg.value = {"analysisId": "test-exhausted-timeout-789"}

    consumer = MagicMock()
    consumer.start = AsyncMock()
    consumer.stop = AsyncMock()
    consumer.commit = AsyncMock()
    consumer.seek = MagicMock()

    async def msg_generator():
        yield msg
        raise asyncio.CancelledError()

    consumer.__aiter__ = lambda self: msg_generator()

    producer = MagicMock()
    producer.start = AsyncMock()
    producer.stop = AsyncMock()
    producer.send_and_wait = AsyncMock()

    with patch("aiokafka.AIOKafkaConsumer", return_value=consumer), \
         patch("aiokafka.AIOKafkaProducer", return_value=producer), \
         patch("main._fetch_status_from_backend", return_value=None), \
         patch("main._fetch_csv_from_backend", side_effect=TransientBackendError("Connection refused")), \
         patch("main.TRANSIENT_RETRY_TIMEOUT_SECONDS", -1.0):

        try:
            asyncio.run(_kafka_consumer_loop())
        except asyncio.CancelledError:
            pass

        # 1. Producer send_and_wait MUST be called to publish FAILED status
        producer.send_and_wait.assert_called_once()
        call_args = producer.send_and_wait.call_args
        topic, = call_args[0]
        assert topic == "analysis.completed"
        published_event = call_args[1]["value"]
        assert published_event["analysisId"] == "test-exhausted-timeout-789"
        assert published_event["status"] == "FAILED"
        assert "transient retries exhausted" in published_event["errorMessage"]
        assert "Partition unblocked" in published_event["errorMessage"]

        # 2. Consumer commit MUST be called to advance past this failed message
        consumer.commit.assert_called_once()

        # 3. Consumer seek MUST NOT be called because message was committed rather than seeked back
        consumer.seek.assert_not_called()



