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
"""

import hashlib
import io
import logging
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import MinMaxScaler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="EpiNet ML Microservice",
    description="Pairwise interaction regression epistasis scorer.",
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# The 10 pharmacogenomic genes this service expects
EXPECTED_GENES = [
    "CYP2D6", "CYP2C19", "CYP3A4", "CYP2C9",
    "VKORC1", "SLCO1B1", "TPMT", "DPYD", "UGT1A1", "ABCB1",
]
TOP_PAIRS_COUNT = 15


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
        raise HTTPException(status_code=400, detail=f"Cannot parse CSV: {exc}")

    df.columns = df.columns.str.strip()

    if "sample_id" not in df.columns:
        raise HTTPException(status_code=400, detail="Missing required column: sample_id")
    if "phenotype" not in df.columns:
        raise HTTPException(status_code=400, detail="Missing required column: phenotype")

    missing_genes = [g for g in EXPECTED_GENES if g not in df.columns]
    if missing_genes:
        raise HTTPException(
            status_code=400,
            detail=f"Missing gene columns: {missing_genes}. Expected: {EXPECTED_GENES}",
        )

    if len(df) < 3:
        raise HTTPException(
            status_code=400,
            detail=f"CSV has only {len(df)} row(s). At least 3 rows are required for interaction regression.",
        )

    gene_df = df[EXPECTED_GENES].apply(pd.to_numeric, errors="coerce")
    phenotype = pd.to_numeric(df["phenotype"], errors="coerce")

    if gene_df.isnull().all().any():
        raise HTTPException(status_code=400, detail="One or more gene columns are entirely non-numeric.")

    # Impute missing cells if any
    gene_df = gene_df.fillna(gene_df.mean())
    phenotype = phenotype.fillna(phenotype.mean())

    # Variance validation: constant columns cause singular matrices
    zero_var_genes = [g for g in EXPECTED_GENES if float(gene_df[g].std(ddof=0)) == 0.0]
    if zero_var_genes:
        raise HTTPException(
            status_code=400,
            detail=f"Zero-variance detected in gene column(s): {zero_var_genes}. All genes must have variance.",
        )

    if float(phenotype.std(ddof=0)) == 0.0:
        raise HTTPException(
            status_code=400,
            detail="Zero-variance detected in phenotype column. Phenotype values must vary across samples.",
        )

    return gene_df, phenotype, len(df)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)) -> dict[str, Any]:
    """
    Accept a pharmacogenomic CSV, run pairwise interaction regression,
    and return an epistasis interaction matrix plus top pair scores.
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted.")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

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
