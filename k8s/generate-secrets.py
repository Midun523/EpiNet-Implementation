"""
k8s/generate-secrets.py — Generate git-ignored k8s/secret.yaml from local .env (Phase 4)
WHY: Automates injecting local secrets into Kubernetes without hardcoding or committing credentials.
"""
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
TEMPLATE_FILE = REPO_ROOT / "k8s" / "secret.template.yaml"
OUTPUT_FILE = REPO_ROOT / "k8s" / "secret.yaml"

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

def main():
    env = parse_env(ENV_FILE)
    if not env:
        print(f"Warning: {ENV_FILE} not found or empty. Using default credentials.", file=sys.stderr)
        env = {
            "MYSQL_ROOT_PASSWORD": "root_pass",
            "MYSQL_PASSWORD": "epinet_pass",
            "DB_PASS": "epinet_pass",
        }
    
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    
    # Replace placeholders
    content = template
    for key in ["MYSQL_ROOT_PASSWORD", "MYSQL_PASSWORD", "DB_PASS"]:
        val = env.get(key, "epinet_pass")
        content = content.replace(f"${{{key}}}", val)
    
    OUTPUT_FILE.write_text(content, encoding="utf-8")
    print(f"Generated {OUTPUT_FILE} from {ENV_FILE}")

if __name__ == "__main__":
    main()
