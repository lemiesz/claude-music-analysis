# claude-music-analysis

Tools, scripts, and Claude Code skills from a project that reorganized an
**entire rekordbox DJ library** with code: folder restructure, tag
scheme, Discogs/Spotify metadata enrichment, local ML audio analysis
(embeddings + mood), clustering-based auto-sorting, a browsable sound map, and
programmatic population of rekordbox's Matching feature all the way onto USB
export drives.

**Start with the report:** [`report/rekordbox-library-report.html`](report/rekordbox-library-report.html)
tells the whole story — what was built, in what order, and why — plus a
step-by-step guide to recreating it and the gotchas learned along the way.
A rendered version is published as a Claude artifact:
[claude.ai/code/artifact/d5db8c08-5ec5-4ce6-80f4-2796530eb4f7](https://claude.ai/code/artifact/d5db8c08-5ec5-4ce6-80f4-2796530eb4f7)
(GitHub shows HTML as source; the artifact link renders it).

## The one-paragraph version

A rekordbox library is just a database. `pyrekordbox` (≥ 0.4.4) opens the
encrypted `master.db` read/write. Once you treat the library like a database —
automatic backups, dry-run-by-default scripts, fill-empty-only writes,
provenance logs — you can safely automate months of organization work:
Discogs fills in genre/label/year/catalog, the free MTG `discogs-effnet` model
turns every track into a 1280-dim embedding (whole library in ~80 minutes),
and the embeddings power mood tags, "sounds like" clustering, a UMAP sound
map, and auto-generated Matching suggestions on the players themselves.

## Repo layout

```
report/     The full write-up (self-contained HTML — open in a browser)
skills/     Claude Code agent skills distilled from this project (see below)
scripts/    Reference implementations (Python + zsh)
```

## The skills

Each directory under `skills/` is a Claude Code skill: a `SKILL.md` with
frontmatter that codifies what we learned so an agent (or a human) can apply
the same playbook to another library. To use them in your own project, copy
the directories into your repo's `.claude/skills/`:

```
cp -r skills/* /path/to/your/project/.claude/skills/
```

| Skill | What it codifies |
|---|---|
| `rekordbox-db-safety` | The write contract: backups, dry-runs, WAL checkpoint, fill-empty, count verification. **Read this one first.** |
| `rekordbox-structure-tags` | Folders = crates, tags = attributes; deriving tags from structure; comment-hashtag mirroring; the 4-bank hardware limit. |
| `rekordbox-metadata-enrichment` | The fetch → local cache → confidence-gated apply pipeline for Discogs and Spotify. |
| `rekordbox-audio-analysis` | Running MTG effnet + mood models in plain TensorFlow, fast segment sampling, which scores are actually usable. |
| `rekordbox-embedding-clustering` | Feel-BPM folding, 2-level cosine k-means, the "meaningfully nameable → own folder" rule, the UMAP sound map. |
| `rekordbox-matching-usb` | Building Matching pairs from embeddings and writing USB export DBs directly when device sync won't. |
| `rekordbox-ingest-pipeline` | The incremental ingest chain for new imports — correct ordering, frozen clusters, no recomputation. |

## The scripts

The scripts in `scripts/` are the actual working implementations from the
project, lightly sanitized. They are **reference implementations, not a
polished CLI**: paths (e.g. `~/.rekordbox-venv`, external-drive locations) and
a few playlist IDs are specific to the original library and need adapting.
The skills describe the transferable logic; the scripts show it running.

Highlights:

- `analyzer.py` — metadata enrichment: `fetch` (Discogs/Spotify → local
  `metadata.sqlite`), `apply` (confidence-gated, fill-empty), `report`,
  plus merge commands for the audio pipeline.
- `analyze_fast.py` — parallel embedding + mood extraction (~5.5 tracks/sec on
  an M1 Pro via 6×15s ffmpeg segment sampling).
- `essentia_features.py` — independent BPM/key/danceability estimates.
- `ingest_tags.py` — incremental My Tag filler (Genre/Energy/Vibe + hashtag
  comments) for untagged tracks only.
- `bpm_halftime_fix.py` — halve stored BPM where the ML tempo confirms
  half-time feel; idempotent.
- `recluster_nocategory.py` / `rebuild_nocat_bpm.py` — the by-genre / by-bpm /
  by-sound auto-sorting of an unsorted pile.
- `build_matching.py` — diff-based population of rekordbox Matching from
  embedding similarity + BPM/key compatibility.
- `push_matching_to_usb.py` / `push_matching_legacy.py` — write Matching
  directly into a stick's `exportLibrary.db` / `exportExt.pdb`.
- `viz_precompute.py` / `viz_app.py` — the UMAP sound map with in-browser
  playback.
- `tag_and_merge.py`, `reorg.py`, `genre_situation.py`,
  `mytag_consolidate.py` — the one-time structure/tagging passes.
- `ingest_new_tracks.sh` — the ongoing ingest orchestrator.

## Requirements

- Python 3.11+, [`pyrekordbox`](https://github.com/dylanljones/pyrekordbox) ≥ 0.4.4
- `tensorflow`, `numpy`, `ffmpeg` for audio analysis;
  `scikit-learn`, `umap-learn`, `dash`, `plotly` for clustering/visualization;
  `sqlcipher3` for the OneLibrary export DB
- ML models: download `discogs-effnet-bs64-1` and the
  `*-discogs-effnet-1` heads from the
  [Essentia model zoo](https://essentia.upf.edu/models.html) into
  `scripts/models/` (CC BY-NC-SA — not redistributed here)
- A Discogs API token (`scripts/.discogs_token`) and optionally Spotify
  client credentials (`scripts/.spotify_creds`) — both gitignored
- The Device Library Plus key for the USB scripts (`RB_DLP_KEY` env var or
  `scripts/.dlp_key`) — not distributed here

## Safety

Every write script here backs up `master.db` first, refuses to run while
rekordbox is open, dry-runs by default, and only fills empty fields. Keep it
that way in anything you build on top. The original project ran ~15 write
operations over the full library with zero tracks lost — because of the
contract, not luck.

## License

MIT — see [LICENSE](LICENSE). The MTG/Essentia models have their own
(non-commercial) license and are not included.
