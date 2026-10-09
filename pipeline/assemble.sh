#!/usr/bin/env bash
# assemble.sh SEG_DIR SCORE.wav FONT.ttf OUT.mp4
# Joins the rendered shot segments, adds film grain, title card, fades and the score.
set -euo pipefail
SEG_DIR="$1"; SCORE="$2"; FONT="$3"; OUT="$4"
LIST=$(mktemp)
for f in $(ls "$SEG_DIR"/seg_*.mp4 | sort); do echo "file '$(realpath "$f")'" >> "$LIST"; done
cat "$LIST"
DUR=60
VF="eq=contrast=1.03:saturation=1.04,"
VF+="noise=c0s=5:c0f=t+u,"
VF+="drawtext=fontfile=${FONT}:text='SHRI RAM':fontcolor=0xF6E3B4:fontsize=h/7.5:x=(w-text_w)/2:y=(h-text_h)/2-h*0.04:"
VF+="alpha='if(lt(t,55.8),0,if(lt(t,57.3),(t-55.8)/1.5,1))':shadowcolor=black@0.5:shadowx=2:shadowy=2,"
VF+="drawtext=fontfile=${FONT}:text='MARYADA PURUSHOTTAM':fontcolor=0xE9CF95:fontsize=h/26:x=(w-text_w)/2:y=(h/2)+h*0.08:"
VF+="alpha='if(lt(t,56.6),0,if(lt(t,58),(t-56.6)/1.4,1))',"
VF+="fade=t=in:st=0:d=0.8,fade=t=out:st=$((DUR-1)):d=1,format=yuv420p"
ffmpeg -y -f concat -safe 0 -i "$LIST" -i "$SCORE" \
  -vf "$VF" -r 24 -c:v libx264 -preset slow -crf 15 -profile:v high -tune film \
  -c:a aac -b:a 320k -ar 48000 -shortest -movflags +faststart \
  -metadata title="Shri Ram - Cinematic Introduction" "$OUT"
ffprobe -v error -show_entries format=duration:stream=width,height,r_frame_rate -of default=nw=1 "$OUT"
