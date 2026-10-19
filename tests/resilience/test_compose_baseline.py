"""
Run 40 MB compute directly in docker compose / container with NO CPU limit.
Generates 40 MB pharmacogenomics data inside container and runs _run_full_analysis 3x.
"""

import subprocess
import re

cmd = [
    "docker", "run", "--rm",
    "epinet-ml-service:latest",
    "python3", "-c",
    '''
import time, io, numpy as np, pandas as pd
from main import EXPECTED_GENES, _run_full_analysis

print("Generating 40 MB CSV inside container...")
rows = 400000
rng = np.random.default_rng(42)
data = {g: rng.random(rows).astype(np.float32) for g in EXPECTED_GENES}
data["sample_id"] = [f"S{i:06d}" for i in range(rows)]
data["phenotype"] = rng.random(rows).astype(np.float32)
df = pd.DataFrame(data)
buf = io.BytesIO()
df.to_csv(buf, index=False)
csv_bytes = buf.getvalue()
print(f"CSV size: {len(csv_bytes)/(1024*1024):.2f} MB")

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

print("Executing Compose baseline (no CPU limit)...")
r = subprocess.run(cmd, capture_output=True, text=True)
print("STDOUT:")
print(r.stdout)
if r.stderr:
    print("STDERR:")
    print(r.stderr)
