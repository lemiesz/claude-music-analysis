#!/usr/bin/env python
"""Parallel, optimized audio analysis: effnet EMBEDDINGS + mood + genre + energy.

Speed: multiprocessing across CPU cores + decode only ~90s of audio (not the whole
track). Coverage: samples N spread-out short segments across the track (5%..90%),
so long build-ups AND drops are represented (not one contiguous chunk).

Stores per track:
  - embedding table: the 1280-dim discogs-effnet embedding (float32 blob)
  - feature table (source='essentia_ml'): mood_happy/sad/aggressive/relaxed/party,
    danceability_ml, genre_discogs, energy

Output: features_v2.sqlite  (merge into metadata.sqlite later).
Run:  ~/.essentia-venv/bin/python analyze_fast.py [--workers 5] [--limit N] [--refetch]
Incremental: skips tracks that already have an embedding.
"""
import os, sys, json, sqlite3, subprocess, datetime, argparse
import numpy as np
import multiprocessing as mp

HERE = os.path.dirname(os.path.abspath(__file__)); M = os.path.join(HERE, "models")
SRC_DB = os.path.join(HERE, "metadata.sqlite"); OUT_DB = os.path.join(HERE, "features_v2.sqlite")
SR = 16000
N_SEG = 6          # number of segments sampled across the track
SEG_DUR = 15       # seconds per segment  (6 x 15 = 90s sampled, spread over the whole track)
PATCH, BATCH = 128, 64

G = {}   # per-worker globals (models)

def init_worker():
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
    import warnings; warnings.filterwarnings("ignore")
    import essentia; essentia.log.infoActive = False; essentia.log.warningActive = False
    import essentia.standard as es
    import tensorflow as tf
    tf.config.threading.set_intra_op_parallelism_threads(1)   # avoid oversubscription
    tf.config.threading.set_inter_op_parallelism_threads(1)
    def load(pb):
        gd = tf.compat.v1.GraphDef(); gd.ParseFromString(open(os.path.join(M, pb), "rb").read())
        g = tf.Graph()
        with g.as_default(): tf.import_graph_def(gd, name="")
        return tf.compat.v1.Session(graph=g)
    G["es"] = es
    G["inp"] = es.TensorflowInputMusiCNN()
    G["eff"] = load("discogs-effnet-bs64-1.pb")
    G["genres"] = json.load(open(os.path.join(M, "discogs-effnet-bs64-1.json")))["classes"]
    heads = {}
    for m in ["mood_happy", "mood_sad", "mood_aggressive", "mood_relaxed", "mood_party", "danceability"]:
        cls = json.load(open(os.path.join(M, f"{m}-discogs-effnet-1.json")))["classes"]
        pos = next(i for i, c in enumerate(cls) if not (c.startswith("non_") or c.startswith("not_")))
        heads[m] = (load(f"{m}-discogs-effnet-1.pb"), pos)
    G["heads"] = heads

def _dur(path):
    try:
        o = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30).stdout.strip()
        return float(o) if o else 0.0
    except Exception:
        return 0.0

def _decode(path, start, dur):
    try:
        raw = subprocess.run(["ffmpeg", "-v", "quiet", "-ss", f"{start:.2f}", "-t", f"{dur}",
                              "-i", path, "-ar", str(SR), "-ac", "1", "-f", "f32le", "-"],
                             capture_output=True, timeout=60).stdout
        return np.frombuffer(raw, dtype=np.float32)
    except Exception:
        return np.array([], dtype=np.float32)

def _patches(audio):
    es = G["es"]
    if audio.size < 512:
        return []
    mel = np.array([G["inp"](fr) for fr in es.FrameGenerator(audio, frameSize=512, hopSize=256)],
                   dtype=np.float32)
    if mel.shape[0] < PATCH:
        if mel.shape[0] == 0:
            return []
        mel = np.pad(mel, ((0, PATCH - mel.shape[0]), (0, 0)))
    n = mel.shape[0] // PATCH
    return [mel[i * PATCH:(i + 1) * PATCH] for i in range(max(n, 1))]   # non-overlapping, no cross-segment patches

def worker(item):
    rb, path = item
    try:
        d = _dur(path)
        if d < 40:                                    # short track: just decode what's there
            segs = [_decode(path, 0, 90)]
        else:
            last = max(0.0, d * 0.90 - SEG_DUR)
            starts = np.linspace(d * 0.05, last, N_SEG)     # spread 5%..90% across the track
            segs = [_decode(path, float(s), SEG_DUR) for s in starts]
        patches, audios = [], []
        for s in segs:
            if s.size:
                audios.append(s)
                patches.extend(_patches(s))
        if not patches:
            return (rb, None, None)
        P = np.stack(patches)
        idx = (np.resize(np.arange(P.shape[0]), BATCH) if P.shape[0] < BATCH
               else np.linspace(0, P.shape[0] - 1, BATCH).astype(int))
        P = P[idx]
        emb, genres = G["eff"].run(["PartitionedCall:1", "PartitionedCall:0"],
                                   {"serving_default_melspectrogram:0": P})
        emb_mean = emb.mean(0)
        au = np.concatenate(audios)
        out = {"genre_discogs": G["genres"][int(np.argmax(genres.mean(0)))],
               "energy": round(float(np.sqrt(np.mean(au ** 2))), 5)}
        for m, (sess, pos) in G["heads"].items():
            pred = sess.run("model/Softmax:0", {"model/Placeholder:0": emb_mean[None, :]})[0]
            out["danceability_ml" if m == "danceability" else m] = round(float(pred[pos]), 3)
        return (rb, out, emb_mean.astype(np.float32).tobytes())
    except Exception as e:
        return (rb, {"_error": f"{type(e).__name__}: {str(e)[:40]}"}, None)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refetch", action="store_true")
    args = ap.parse_args()

    odb = sqlite3.connect(OUT_DB)
    odb.execute("""CREATE TABLE IF NOT EXISTS feature (rb_id TEXT, source TEXT, name TEXT,
        num REAL, txt TEXT, fetched_at TEXT, PRIMARY KEY(rb_id,source,name))""")
    odb.execute("""CREATE TABLE IF NOT EXISTS embedding (rb_id TEXT PRIMARY KEY, model TEXT,
        dim INT, vec BLOB, fetched_at TEXT)""")
    odb.commit()
    have = set(r[0] for r in odb.execute("SELECT rb_id FROM embedding"))

    rdb = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True, timeout=60)
    tracks = []
    for rb, p in rdb.execute("SELECT rb_id, path FROM track_files"):
        pp = p[7:] if p.startswith("file://") else p
        if (args.refetch or rb not in have) and os.path.exists(pp):
            tracks.append((rb, pp))
    if args.limit:
        tracks = tracks[:args.limit]
    total = len(tracks)
    print(f"analyze_fast: {total} tracks | {args.workers} workers | {N_SEG}x{SEG_DUR}s spread sampling", flush=True)

    n_ok = n_err = 0
    pending = 0
    with mp.Pool(args.workers, initializer=init_worker) as pool:
        for rb, out, emb in pool.imap_unordered(worker, tracks, chunksize=1):
            if not out or "_error" in out:
                n_err += 1; continue
            ts = datetime.datetime.now().isoformat()
            for k, v in out.items():
                num = float(v) if isinstance(v, (int, float)) else None
                odb.execute("INSERT OR REPLACE INTO feature VALUES (?,?,?,?,?,?)",
                            (rb, "essentia_ml", k, num, None if num is not None else str(v), ts))
            odb.execute("INSERT OR REPLACE INTO embedding VALUES (?,?,?,?,?)",
                        (rb, "discogs-effnet", 1280, emb, ts))
            n_ok += 1; pending += 1
            if pending >= 20:
                odb.commit(); pending = 0
            if n_ok % 100 == 0:
                print(f"  {n_ok}/{total} ok ({n_err} err)  {datetime.datetime.now():%H:%M:%S}", flush=True)
    odb.commit()
    print(f"done: ok={n_ok} err={n_err}", flush=True)

if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
