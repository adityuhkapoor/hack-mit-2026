#!/usr/bin/env bash
# Copy the pipeline source to the GPU box and (re)start the API there.
#   bash pipeline/infra/win/deploy_api.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
ssh win "if not exist hackmit\\pipeline\\nimbus mkdir hackmit\\pipeline\\nimbus"
scp -q "$HERE/pyproject.toml" win:hackmit/pipeline/
scp -q "$HERE"/nimbus/*.py "$HERE"/nimbus/*.html win:hackmit/pipeline/nimbus/
ssh win "if not exist hackmit\\pipeline\\nimbus\\data mkdir hackmit\\pipeline\\nimbus\\data"
scp -q "$HERE"/nimbus/data/* win:hackmit/pipeline/nimbus/data/
scp -q "$HERE/infra/win/start_api.bat" "$HERE/infra/win/setup_api.ps1" win:hackmit/
ssh win "powershell -NoProfile -ExecutionPolicy Bypass -File hackmit\\setup_api.ps1"
