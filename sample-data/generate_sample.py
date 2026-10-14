"""
generate_sample.py
------------------
Generates a sample pharmacogenomic CSV with 200 rows for testing EpiNet Visualizer.
Each gene column contains simulated continuous expression/activity values.
The phenotype column contains a continuous drug-response score with planted
epistatic (gene-gene interaction) effects:
  - CYP2C9 * VKORC1 (classic warfarin dosing epistasis)
  - CYP2D6 * CYP3A4 (dual hepatic clearance epistasis)
  - SLCO1B1 * ABCB1 (transporter-transporter disposition epistasis)

Usage:
    python sample-data/generate_sample.py   (or run from sample-data/)
Output:
    sample-data/sample_pharmacogenomics.csv
"""

from pathlib import Path
import numpy as np
import pandas as pd

SEED = 42
N_ROWS = 200
GENES = [
    "CYP2D6", "CYP2C19", "CYP3A4", "CYP2C9",
    "VKORC1", "SLCO1B1", "TPMT", "DPYD", "UGT1A1", "ABCB1",
]

rng = np.random.default_rng(SEED)

# Simulate correlated gene expression / activity values
mean = rng.uniform(0.3, 0.7, len(GENES))
cov_base = rng.uniform(-0.2, 0.2, (len(GENES), len(GENES)))
cov = cov_base @ cov_base.T + np.eye(len(GENES)) * 0.5
gene_values = rng.multivariate_normal(mean, cov, size=N_ROWS)
gene_values = np.clip(gene_values, 0.05, 0.95)

df = pd.DataFrame(gene_values, columns=GENES)
df.insert(0, "sample_id", [f"SAMPLE_{i+1:03d}" for i in range(N_ROWS)])

# Additive linear baseline effects
main_weights = rng.uniform(0.2, 0.6, len(GENES))
additive_term = gene_values @ main_weights

# Planted biological epistatic (non-additive product) interactions:
# 1. CYP2C9 * VKORC1 (strong pharmacogenomic synergy)
# 2. CYP2D6 * CYP3A4 (dual cytochrome synergy)
# 3. SLCO1B1 * ABCB1 (transporter-transporter synergy)
epistasis_term = (
    2.2 * df["CYP2C9"] * df["VKORC1"]
    + 1.8 * df["CYP2D6"] * df["CYP3A4"]
    + 1.5 * df["SLCO1B1"] * df["ABCB1"]
)

# Residual stochastic variation
noise = rng.normal(0, 0.08, N_ROWS)

df["phenotype"] = additive_term + epistasis_term + noise

# Always write to sample-data/ directory regardless of where script is invoked from
out_path = Path(__file__).resolve().parent / "sample_pharmacogenomics.csv"
df.to_csv(out_path, index=False, float_format="%.6f")
print(f"Generated {out_path} ({N_ROWS} rows, {len(GENES)} gene columns, 1 phenotype column)")
