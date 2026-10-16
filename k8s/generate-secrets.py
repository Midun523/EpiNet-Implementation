"""
k8s/generate-secrets.py — Generate k8s secrets and template manifests with dynamic kind gateway (Phase 4)
WHY: Automates injecting local secrets from .env into Kubernetes and dynamically resolves the kind bridge
network gateway IP from `docker network inspect kind` so manifests are never hard-coded to 172.19.0.1.
"""
from pathlib import Path
import subprocess
import json
import re
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
SECRET_TEMPLATE = REPO_ROOT / "k8s" / "secret.template.yaml"
SECRET_OUTPUT = REPO_ROOT / "k8s" / "secret.yaml"

BACKEND_TEMPLATE = REPO_ROOT / "k8s" / "backend-deployment.template.yaml"
BACKEND_OUTPUT = REPO_ROOT / "k8s" / "backend-deployment.yaml"

ML_TEMPLATE = REPO_ROOT / "k8s" / "ml-service-deployment.template.yaml"
ML_OUTPUT = REPO_ROOT / "k8s" / "ml-service-deployment.yaml"

DEFAULT_GATEWAY = "172.19.0.1"

def parse_env(path: Path) -> dict[str, str]:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    return env

def discover_kind_gateway() -> str:
    # WHY: reads kind bridge network gateway from Docker daemon at deploy time
    try:
        res = subprocess.run(
            ["docker", "network", "inspect", "kind"],
            capture_output=True, text=True, check=True
        )
        data = json.loads(res.stdout)
        if data and isinstance(data, list):
            ipam_configs = data[0].get("IPAM", {}).get("Config", [])
            for cfg in ipam_configs:
                gw = cfg.get("Gateway", "")
                if "." in gw: # Look for IPv4 gateway
                    return gw
    except Exception as e:
        print(f"Warning: Could not inspect docker network 'kind' ({e}). Falling back to default gateway: {DEFAULT_GATEWAY}", file=sys.stderr)
    return DEFAULT_GATEWAY

def main():
    # 1. Generate secret.yaml from .env
    env = parse_env(ENV_FILE)
    if not env:
        print(f"Warning: {ENV_FILE} not found or empty. Using default credentials.", file=sys.stderr)
        env = {
            "MYSQL_ROOT_PASSWORD": "root_pass",
            "MYSQL_PASSWORD": "epinet_pass",
            "DB_PASS": "epinet_pass",
        }
    
    sec_content = SECRET_TEMPLATE.read_text(encoding="utf-8")
    for key in ["MYSQL_ROOT_PASSWORD", "MYSQL_PASSWORD", "DB_PASS"]:
        val = env.get(key, "epinet_pass")
        sec_content = sec_content.replace(f"${{{key}}}", val)
    SECRET_OUTPUT.write_text(sec_content, encoding="utf-8")
    print(f"Generated {SECRET_OUTPUT} from {ENV_FILE}")

    # 2. Template deployment manifests with dynamic kind network gateway
    gateway_ip = discover_kind_gateway()
    print(f"Discovered kind bridge network gateway: {gateway_ip}")

    if BACKEND_TEMPLATE.exists():
        b_content = BACKEND_TEMPLATE.read_text(encoding="utf-8")
        b_content = b_content.replace("${KIND_GATEWAY_IP}", gateway_ip)
        BACKEND_OUTPUT.write_text(b_content, encoding="utf-8")
        print(f"Templated {BACKEND_OUTPUT} with gateway {gateway_ip}")

    if ML_TEMPLATE.exists():
        m_content = ML_TEMPLATE.read_text(encoding="utf-8")
        m_content = m_content.replace("${KIND_GATEWAY_IP}", gateway_ip)
        ML_OUTPUT.write_text(m_content, encoding="utf-8")
        print(f"Templated {ML_OUTPUT} with gateway {gateway_ip}")

if __name__ == "__main__":
    main()
