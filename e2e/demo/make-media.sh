#!/usr/bin/env bash
# Turn the Playwright recordings into the README media.
#   npm run demo && bash demo/make-media.sh
# Output: docs/media/<name>.mp4 (H.264), docs/media/hero.gif (cut of the review video),
#         docs/media/<name>.png (a still from each video).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/../docs/media"
mkdir -p "$out"
gifsicle="$here/node_modules/gifsicle/vendor/gifsicle"

for dir in "$here"/demo-output/*/; do
  video=$(ls "$dir"*.webm 2>/dev/null | head -n 1) || continue
  [ -n "$video" ] || continue
  case "$dir" in
    *straight-through*) name="1-straight-through" ;;
    *review*) name="2-review" ;;
    *adversarial*) name="3-adversarial" ;;
    *owner*) name="4-owner" ;;
    *) continue ;;
  esac
  ffmpeg -loglevel error -y -i "$video" -c:v libx264 -pix_fmt yuv420p -crf 23 -preset slow \
    -movflags +faststart "$out/$name.mp4"
  ffmpeg -loglevel error -y -ss 6 -i "$out/$name.mp4" -frames:v 1 "$out/$name.png"
  echo "$name: $(du -h "$out/$name.mp4" | cut -f1)"
done

# Hero: from the review video, the part where a field is clicked, fixed and approved.
start="${HERO_START:-9}"
length="${HERO_LENGTH:-17}"
filters="fps=12,scale=960:-1:flags=lanczos"
ffmpeg -loglevel error -y -ss "$start" -t "$length" -i "$out/2-review.mp4" \
  -vf "$filters,palettegen=stats_mode=diff:max_colors=128" "$here/demo-output/palette.png"
ffmpeg -loglevel error -y -ss "$start" -t "$length" -i "$out/2-review.mp4" -i "$here/demo-output/palette.png" \
  -lavfi "$filters [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
  "$here/demo-output/hero-raw.gif"
"$gifsicle" -O3 --lossy=40 --loopcount=forever "$here/demo-output/hero-raw.gif" -o "$out/hero.gif"
echo "hero.gif: $(du -h "$out/hero.gif" | cut -f1)"
