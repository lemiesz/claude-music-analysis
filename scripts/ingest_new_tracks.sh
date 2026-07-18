#!/bin/zsh
# Progressive ingest pipeline for newly imported rekordbox tracks.
# Every step is incremental/idempotent — nothing is recomputed for old tracks.
#
#   ./ingest_new_tracks.sh analyze   # phase A: audio analysis (rekordbox may be OPEN)
#   ./ingest_new_tracks.sh dryrun    # phase B preview: what the DB writes would do
#   ./ingest_new_tracks.sh apply     # phase B: write tags/BPM/playlists/matching
#                                    #          (rekordbox must be CLOSED)
#   ./ingest_new_tracks.sh viz       # optional: refresh visualizer arrays
set -e
cd "$(dirname "$0")"
RB=~/.rekordbox-venv/bin/python
ES=~/.essentia-venv/bin/python

case "$1" in
analyze)
  echo "== 1/5 sync track paths from rekordbox =="
  $RB analyzer.py paths
  echo "== 2/5 embeddings + ML mood/genre (incremental, skips analyzed) =="
  $ES analyze_fast.py --workers 5
  echo "== 3/5 essentia bpm/key/mode (incremental) =="
  $ES essentia_features.py
  echo "== 4/5 merge features_v2 -> metadata.sqlite =="
  $RB analyzer.py merge-v2
  echo "== 5/5 merge essentia_features -> metadata.sqlite =="
  $RB analyzer.py merge-audio
  echo "analyze done. Next: ./ingest_new_tracks.sh dryrun"
  ;;
dryrun)
  echo "===== halftime BPM fix (library-wide) ====="
  $RB bpm_halftime_fix.py --scope all
  echo; echo "===== incremental tag fill ====="
  $RB ingest_tags.py
  echo; echo "===== no-cat-sorted rebuild ====="
  $RB rebuild_nocat_bpm.py | head -40
  echo; echo "===== matching rebuild ====="
  $RB build_matching.py | head -12
  echo; echo "Review above, then: ./ingest_new_tracks.sh apply"
  ;;
apply)
  $RB bpm_halftime_fix.py --scope all --apply
  $RB ingest_tags.py --apply
  $RB rebuild_nocat_bpm.py --apply | tail -5
  $RB build_matching.py --apply | tail -3
  echo "apply done. Re-export USB for the XDJ-XZ."
  ;;
viz)
  $RB viz_precompute.py
  echo "viz arrays refreshed — restart viz_app.py if running."
  ;;
*)
  echo "usage: $0 analyze|dryrun|apply|viz"; exit 1 ;;
esac
