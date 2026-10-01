#!/usr/bin/env bash
# Part 1B: ask for a made-up number and save the spoken reply as a WAV file.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$SCRIPT_DIR/.."
VOICES_DIR="$LAB_DIR/voices"
RECORDINGS_DIR="$LAB_DIR/recordings"
mkdir -p "$RECORDINGS_DIR"

python3 -m piper \
  --model en_US-lessac-medium \
  --data-dir "$VOICES_DIR" \
  --output-raw \
  -- "Please say a four digit number you make up. Speak after I finish. You have six seconds." \
  | aplay -r 22050 -f S16_LE -t raw -

OUTPUT="$RECORDINGS_DIR/number-answer-$(date +%Y%m%d-%H%M%S).wav"
if [[ -e "$OUTPUT" ]]; then
  printf 'Recording already exists: %s\n' "$OUTPUT" >&2
  exit 1
fi

arecord -D default -f S16_LE -r 16000 -c 1 -d 6 "$OUTPUT"
printf 'Saved spoken answer to %s\n' "$OUTPUT"
