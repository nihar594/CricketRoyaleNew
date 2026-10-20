#!/usr/bin/env bash
#
# Converts every .mp4 in assets/gifs/ to a real .gif file, then removes
# the original .mp4. Run this once locally, in the project root, wherever
# ffmpeg is installed (it already needs to be, for utils/media.py's
# ffprobe dimension-detection feature).
#
# Why: Telegram loads/plays an actual .gif noticeably faster than an .mp4
# sent as an animation -- no audio track to strip, smaller file, near-
# instant preview generation. That's the 1-2s lag you see after a shot/
# wicket before the reaction plays.
#
# Usage:
#   chmod +x convert_gifs_to_gif.sh
#   ./convert_gifs_to_gif.sh
#
# (config.py already prefers .gif over .mp4 automatically -- nothing else
# to change after running this.)

set -e

GIFS_DIR="assets/gifs"

if [ ! -d "$GIFS_DIR" ]; then
    echo "Error: $GIFS_DIR not found. Run this from the project root."
    exit 1
fi

if ! command -v ffmpeg &> /dev/null; then
    echo "Error: ffmpeg not found. Install it first:"
    echo "  Windows: https://ffmpeg.org/download.html (or 'winget install ffmpeg')"
    echo "  Mac:     brew install ffmpeg"
    echo "  Linux:   sudo apt install ffmpeg"
    exit 1
fi

count=0
for mp4_file in "$GIFS_DIR"/*.mp4; do
    [ -e "$mp4_file" ] || continue  # no .mp4 files matched
    base="${mp4_file%.mp4}"
    gif_file="${base}.gif"

    echo "Converting: $mp4_file -> $gif_file"
    # fps=15 and a capped width keep file size (and load time) down --
    # bump fps to 20-24 if a particular clip looks too choppy.
    ffmpeg -y -i "$mp4_file" -vf "fps=15,scale=480:-1:flags=lanczos" \
        -loop 0 "$gif_file" -loglevel error

    if [ -f "$gif_file" ]; then
        rm "$mp4_file"
        count=$((count + 1))
    else
        echo "  WARNING: conversion failed for $mp4_file, keeping the .mp4"
    fi
done

echo ""
echo "Done. Converted $count file(s) to .gif."
echo "Restart the bot (or it'll pick up the change on its next asset lookup, no restart needed)."
