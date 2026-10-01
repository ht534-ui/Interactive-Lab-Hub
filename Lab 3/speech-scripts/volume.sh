#!/usr/bin/env bash
# Control Orange's USB speaker, including from a Mac without deploying this file.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./volume.sh [status | 0..100 | +N | -N | mute | unmute | ui]
  status   Show the speaker volume (default).
  70       Set 70% and unmute.
  +5 / -5  Raise/lower by 5 percentage points; preserve mute state.
  mute     Silence the speaker without losing its volume setting.
  unmute   Restore sound at the existing volume.
  ui       Interactive mixer: arrows adjust, M toggles mute, Esc exits.

On macOS, commands connect to Orange through the existing SSH alias.
On Orange, commands run locally. No virtual environment or sudo required.
Uses the UACDemoV10 speaker's PCM control, not the USB microphone.
Percentages use amixer's raw scale; ui uses alsamixer's perceptual scale.
EOF
}

if (( $# > 1 )); then usage >&2; exit 2; fi
action="${1:-status}"
case "$action" in
  -h|--help|help) usage; exit 0 ;;
  status|mute|unmute|ui) ;;
  *)
    if [[ ! "$action" =~ ^[+-]?[0-9]{1,3}$ ]]; then
      echo "Invalid action: $action" >&2; usage >&2; exit 2
    fi
    digits="${action#[+-]}"
    if (( 10#$digits > 100 )); then
      echo "Volume or step must be between 0 and 100." >&2; exit 2
    fi
    # Normalize leading zeros before handing the value to amixer.
    amount=$((10#$digits))
    case "$action" in
      +*) action="+$amount" ;;
      -*) action="-$amount" ;;
      *) action="$amount" ;;
    esac
    ;;
esac

card=UACDemoV10
if [[ "$(uname -s)" == Darwin ]]; then
  ssh_options=(-S none -o BatchMode=yes -o PasswordAuthentication=no
    -o StrictHostKeyChecking=yes -o ConnectTimeout=8)
  if [[ "$action" == ui ]]; then
    exec ssh -t "${ssh_options[@]}" Orange "alsamixer -c $card"
  fi
  # Only validated actions enter the remote shell. The script travels on stdin;
  # the Pi checkout and its existing files do not need to be changed.
  exec ssh "${ssh_options[@]}" Orange "bash -s -- '$action'" < "$0"
fi

if ! command -v amixer >/dev/null; then
  echo "amixer is missing. Install alsa-utils on Orange." >&2; exit 1
fi
if ! amixer -c "$card" sget PCM >/dev/null 2>&1; then
  echo "Cannot find $card / PCM. Check the USB speaker connection (aplay -l)." >&2
  exit 1
fi
case "$action" in
  ui) exec alsamixer -c "$card" ;;
  status) ;;
  mute|unmute) amixer -q -c "$card" sset PCM "$action" ;;
  +*) amixer -q -c "$card" sset PCM "${action#+}%+" ;;
  -*) amixer -q -c "$card" sset PCM "${action#-}%-" ;;
  *) amixer -q -c "$card" sset PCM "$action%" unmute ;;
esac
amixer -c "$card" sget PCM
