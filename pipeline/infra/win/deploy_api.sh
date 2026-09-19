#!/usr/bin/env bash
# Copy the pipeline source to the GPU box and (re)start the API there.
#   bash pipeline/infra/win/deploy_api.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
ssh win "if not exist hackmit\\pipeline\\lookcam mkdir hackmit\\pipeline\\lookcam"
scp -q "$HERE/pyproject.toml" win:hackmit/pipeline/
scp -q "$HERE"/lookcam/*.py "$HERE"/lookcam/*.html win:hackmit/pipeline/lookcam/
ssh win "if not exist hackmit\\pipeline\\lookcam\\data mkdir hackmit\\pipeline\\lookcam\\data"
scp -q "$HERE"/lookcam/data/* win:hackmit/pipeline/lookcam/data/
scp -q "$HERE/infra/win/start_api.bat" "$HERE/infra/win/setup_api.ps1" win:hackmit/
ssh win "powershell -NoProfile -ExecutionPolicy Bypass -File hackmit\\setup_api.ps1"
