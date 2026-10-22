#!/usr/bin/env bash
set -e

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${script_dir}${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m agent_orchestrator "$@"
