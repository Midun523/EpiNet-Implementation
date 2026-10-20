#!/usr/bin/env python3
"""
tests/bench/generate_bench_data.py

Generates reproducible synthetic pharmacogenomics CSV files (2 MB and 10 MB)
using the exact same logic as tests/e2e/run_e2e_tests.py.
"""

import os
import sys

DATA_DIR = os.path.abspath("tests/bench/data")
SAMPLE_CSV = os.path.abspath("sample-data/sample_pharmacogenomics.csv")


def generate_csv_bytes(target_mb: float) -> bytes:
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


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    for mb in [2.0, 10.0]:
        out_path = os.path.join(DATA_DIR, f"benchmark_{int(mb)}mb.csv")
        print(f"Generating {mb} MB CSV -> {out_path}...")
        b = generate_csv_bytes(mb)
        with open(out_path, "wb") as f:
            f.write(b)
        actual_mb = len(b) / (1024 * 1024)
        print(f"Written: {actual_mb:.2f} MB ({len(b)} bytes)")


if __name__ == "__main__":
    main()
