#!/usr/bin/env bash
# run.sh — Start all EpiNet Visualizer services; Ctrl+C to stop all
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[run]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }

ROOT_DIR="$(pwd)"
PIDS=()
cleanup() {
  echo ""
  info "Stopping all services…"
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  info "All services stopped."
  exit 0
}
trap cleanup SIGINT SIGTERM

# ── MySQL (Docker) ─────────────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1; then
  info "Starting MySQL via docker-compose (waiting for healthcheck)…"
  if docker compose up -d --wait mysql 2>/dev/null; then
    info "MySQL container healthy and ready."
    USE_H2=false
  else
    warn "Docker compose start failed — falling back to H2 in-memory profile."
    USE_H2=true
  fi
else
  warn "Docker not available — backend will use H2 in-memory profile."
  USE_H2=true
fi

# ── ML service ─────────────────────────────────────────────────────────────
info "Starting ML microservice on :8000…"
cd ml-service
if [ -f "./venv/bin/uvicorn" ]; then
  UVICORN_CMD="./venv/bin/uvicorn"
elif [ -f "./venv/Scripts/uvicorn" ] || [ -f "./venv/Scripts/uvicorn.exe" ]; then
  UVICORN_CMD="./venv/Scripts/uvicorn"
elif command -v uvicorn >/dev/null 2>&1; then
  UVICORN_CMD="uvicorn"
else
  UVICORN_CMD="python -m uvicorn"
fi

$UVICORN_CMD main:app --host 0.0.0.0 --port 8000 &
PIDS+=($!)
cd ..
sleep 2

# ── Backend (Spring Boot) ──────────────────────────────────────────────────
info "Starting Spring Boot backend on :8080…"
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
  MVN="mvn"
fi

cd backend
if $USE_H2; then
  $MVN spring-boot:run -q "-Dspring-boot.run.profiles=h2" &
else
  $MVN spring-boot:run -q &
fi
PIDS+=($!)
cd ..
sleep 5

# ── Frontend (Vite) ────────────────────────────────────────────────────────
info "Starting React frontend on :5173…"
cd frontend
npm run dev &
PIDS+=($!)
cd ..

echo ""
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  EpiNet Visualizer is running!${NC}"
echo -e "${CYAN}  Frontend  → http://localhost:5173${NC}"
echo -e "${CYAN}  Backend   → http://localhost:8080${NC}"
echo -e "${CYAN}  ML Svc    → http://localhost:8000${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "  Press ${YELLOW}Ctrl+C${NC} to stop all services."
echo ""

wait
