# ml-service/Dockerfile — Multi-stage container build for EpiNet ML microservice
# Stage 1: Build wheel dependencies in a virtual environment
# WHY python:3.12-slim: slim Debian-based image matching local Python 3.12 runtime with minimal footprint
FROM python:3.12-slim AS builder
WORKDIR /app

# WHY build-essential: provides C/C++ compiler toolchain if any package wheel requires compilation
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

# Create virtual environment for clean dependency isolation
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Minimal runtime image
FROM python:3.12-slim
WORKDIR /app

# WHY curl: required for Docker healthcheck against /health
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# WHY non-root user: container security hardening to prevent host/container privilege escalation
RUN groupadd -g 10001 epinet && \
    useradd -u 10001 -g epinet -m -s /bin/bash epinet

# Copy isolated virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

COPY main.py .
RUN chown -R epinet:epinet /app

USER epinet

EXPOSE 8000

# WHY uvicorn on 0.0.0.0: binds to all network interfaces within the Docker container network
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
