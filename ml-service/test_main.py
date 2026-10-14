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
