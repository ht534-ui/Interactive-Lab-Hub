#!/usr/bin/env bash
# The boot display also reads GPIO23/24; release it before taking the button.
set -euo pipefail
export PYTHONIOENCODING=utf-8

repo_dir="$(cd "$(dirname "$0")/../.." && pwd)"
was_active="$(systemctl is-active piscreen.service || true)"
restore_display() {
  if [[ "$was_active" == "active" ]]; then
    sudo systemctl start piscreen.service
  fi
}
trap restore_display EXIT

if [[ "$was_active" == "active" ]]; then
  sudo systemctl stop piscreen.service
fi
"${COACH_HARDWARE_PYTHON:-$repo_dir/.venv/bin/python}" "$repo_dir/Lab 3/speech-scripts/roast_button.py" "$@"
