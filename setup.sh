#!/usr/bin/env bash
# setup.sh — Idempotent first-time setup for EpiNet Visualizer
# Run from the repo root: bash setup.sh
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

ROOT_DIR="$(pwd)"

# ── 1. Check prerequisites ─────────────────────────────────────────────────

info "Checking prerequisites…"

command -v java  >/dev/null 2>&1 || error "java not found. Install JDK 17+."
command -v node  >/dev/null 2>&1 || error "node not found. Install Node 20+."

# Python: check functional python or py launcher
if command -v python3 >/dev/null 2>&1 && python3 --version >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1 && python --version >/dev/null 2>&1; then
  PYTHON="python"
elif command -v py >/dev/null 2>&1; then
  PYTHON="py -3"
else
  error "python3 not found. Install Python 3.11+."
fi

JAVA_VER=$(java -version 2>&1 | awk -F '"' '/version/ {print $2}')
NODE_VER=$(node --version)
info "Java: $JAVA_VER | Node: $NODE_VER | Python: $($PYTHON --version)"

# Maven: prefer system mvn, fall back to wrapper or MAVEN_HOME
if command -v mvn >/dev/null 2>&1; then
  MVN="mvn"
elif [ -n "${MAVEN_HOME:-}" ] && [ -f "$MAVEN_HOME/bin/mvn" ]; then
  MVN="$MAVEN_HOME/bin/mvn"
elif [ -n "${M2_HOME:-}" ] && [ -f "$M2_HOME/bin/mvn" ]; then
  MVN="$M2_HOME/bin/mvn"
elif [ -f "$ROOT_DIR/backend/mvnw" ]; then
  MVN="$ROOT_DIR/backend/mvnw"
elif [ -f "$ROOT_DIR/backend/mvnw.cmd" ]; then
  MVN="$ROOT_DIR/backend/mvnw.cmd"
else
  warn "mvn not found on PATH. Backend dependency resolution skipped."
  warn "Install Maven from https://maven.apache.org or set MAVEN_HOME."
  MVN=""
fi

if command -v docker >/dev/null 2>&1; then
  DOCKER_VER=$(docker --version)
  info "Docker: $DOCKER_VER"
else
  warn "Docker not found. Use the H2 profile: -Dspring.profiles.active=h2"
fi

# ── 2. Frontend ────────────────────────────────────────────────────────────

info "Installing frontend dependencies…"
cd frontend
npm install --silent
cd ..
info "Frontend deps installed."

# ── 3. Backend (Maven) ─────────────────────────────────────────────────────

if [ -n "$MVN" ]; then
  info "Resolving backend Maven dependencies…"
  cd backend
  $MVN -q dependency:resolve -DincludeScope=compile
  cd ..
  info "Backend deps resolved."
fi

# ── 4. Python venv ─────────────────────────────────────────────────────────

info "Setting up Python virtual environment for ML service…"
cd ml-service

if [ ! -d "venv" ]; then
  $PYTHON -m venv venv
  info "venv created."
fi

# Activate and install dependencies
if [ -f "./venv/Scripts/python.exe" ]; then
  VENV_PY="./venv/Scripts/python.exe"
elif [ -f "./venv/Scripts/python" ]; then
  VENV_PY="./venv/Scripts/python"
elif [ -f "./venv/bin/python" ]; then
  VENV_PY="./venv/bin/python"
else
  VENV_PY="$PYTHON"
fi

$VENV_PY -m pip install -r requirements.txt --quiet
if [ -f "requirements-dev.txt" ]; then
  $VENV_PY -m pip install -r requirements-dev.txt --quiet
fi
cd ..
info "ML service venv ready."

# ── 5. Sample data ─────────────────────────────────────────────────────────

if [ ! -f "sample-data/sample_pharmacogenomics.csv" ]; then
  info "Generating sample data…"
  $PYTHON sample-data/generate_sample.py
fi
info "Sample data present at sample-data/sample_pharmacogenomics.csv"

# ── 6. .env ────────────────────────────────────────────────────────────────

if [ ! -f ".env" ]; then
  cp .env.example .env
  info "Copied .env.example → .env (edit if needed for MySQL credentials)"
fi

echo ""
echo -e "${GREEN}✓ Setup complete!${NC}"
echo ""
echo "  Start everything:  bash run.sh"
echo "  Frontend only:     cd frontend && npm run dev"
echo "  ML service only:   cd ml-service && ([ -f ./venv/bin/uvicorn ] && ./venv/bin/uvicorn || ./venv/Scripts/uvicorn) main:app --reload"
echo "  Backend (H2):      cd backend && mvn spring-boot:run -Dspring-boot.run.profiles=h2"
