#!/usr/bin/env python
"""Interactive library visualizer (Dash) — 2D + 3D sound-map with playback.

- UMAP map (2D or 3D), color by genre / intensity / cluster / aggressiveness
- 2D: scroll to zoom, drag to pan, double-click to reset view
- 3D: drag to rotate, scroll to zoom, right-drag to pan (native gestures)
- Click/search a track -> CONSTELLATION (edges to nearest neighbors) + "sounds
  like" list + an audio player that streams the file
- Zoom/rotation is preserved across clicks (uirevision)
Run:  ~/.rbx-viz/bin/python viz_app.py   ->  http://127.0.0.1:8050
"""
import os
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, dcc, html, Input, Output, no_update, ctx, ALL

HERE = os.path.dirname(os.path.abspath(__file__))
A = np.load(os.path.join(HERE, "viz_arrays.npz"))
XY, XYZ, EMB, CL = A["xy"], A["xyz"], A["emb_norm"], A["cluster"]
M = pd.read_csv(os.path.join(HERE, "viz_meta.csv")).fillna("—")
N = len(M)

# file paths for playback, keyed to M row order
import sqlite3
from flask import send_file, abort
_db = sqlite3.connect(f"file:{os.path.join(HERE, 'metadata.sqlite')}?mode=ro", uri=True)
_p = {rb: (p[7:] if p.startswith("file://") else p) for rb, p in _db.execute("SELECT rb_id,path FROM track_files")}
PATHS = [_p.get(str(rb), "") for rb in M["rb_id"]]

# artist (track table) + record label / year (discogs), aligned to M row order
_artist = {str(rb): (a or "") for rb, a in _db.execute("SELECT rb_id,artist FROM track")}
_disc = {str(rb): (lab or "", yr) for rb, lab, yr in _db.execute("SELECT rb_id,label,year FROM discogs")}
ARTIST = [_artist.get(str(rb), "") for rb in M["rb_id"]]
REC_LABEL = [_disc.get(str(rb), ("", None))[0] for rb in M["rb_id"]]
YEAR = [_disc.get(str(rb), ("", None))[1] for rb in M["rb_id"]]

# --- BPM + musical key (Camelot) from essentia_features.sqlite --------------
# essentia computed bpm/key/mode (source='essentia'); not present in viz_meta.
# Keyed by rb_id (TEXT in db) -> cast M["rb_id"] to str to match.
_PC = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
       "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
_CAMELOT = {}   # (pitch-class, mode) -> Camelot code, derived round the wheel
for _n in range(1, 13):     # 8B=C major, 8A=A minor; +1 number = +a fifth
    _CAMELOT[((7 * (_n - 8)) % 12, "major")] = f"{_n}B"
    _CAMELOT[((9 + 7 * (_n - 8)) % 12, "minor")] = f"{_n}A"

def _camelot(key, mode):
    pc = _PC.get(str(key))
    if pc is None or mode not in ("major", "minor"):
        return None
    return _CAMELOT.get((pc, mode))

_efdb = sqlite3.connect(f"file:{os.path.join(HERE, 'essentia_features.sqlite')}?mode=ro", uri=True)
_bpm_raw, _key_raw, _mode_raw = {}, {}, {}
for _rb, _name, _num, _txt in _efdb.execute(
        "SELECT rb_id,name,num,txt FROM feature WHERE source='essentia' AND name IN ('bpm','key','mode')"):
    if _name == "bpm":    _bpm_raw[_rb] = _num
    elif _name == "key":  _key_raw[_rb] = _txt
    elif _name == "mode": _mode_raw[_rb] = _txt
_efdb.close()

BPM = np.array([_bpm_raw.get(str(rb), np.nan) for rb in M["rb_id"]], dtype=float)
CAMELOT, KEYNAME = [], []   # aligned to M row order
_MODE_ABBR = {"minor": "min", "major": "maj"}
for rb in M["rb_id"]:
    k, mo = _key_raw.get(str(rb)), _mode_raw.get(str(rb))
    code = _camelot(k, mo)
    CAMELOT.append(code)
    KEYNAME.append(f"{k} {_MODE_ABBR.get(mo, '')}".strip() if code else None)
CAMELOT = np.array(CAMELOT, dtype=object)


def _cam_compatible(a, b):
    """Camelot harmonic match: same code, ±1 same letter (wrap 12<->1), or relative maj/min."""
    if not a or not b:
        return False
    if a == b:
        return True
    na, la = int(a[:-1]), a[-1]
    nb, lb = int(b[:-1]), b[-1]
    if la == lb and abs(na - nb) in (1, 11):      # adjacent on the wheel
        return True
    if na == nb and la != lb:                     # relative major/minor
        return True
    return False


def _bpm_compatible(a, b):
    """Within ±6% BPM, allowing half/double-time (ratio ~0.5 / ~2.0)."""
    if not (np.isfinite(a) and np.isfinite(b)) or a <= 0 or b <= 0:
        return False
    return any(abs(a / (b * m) - 1.0) <= 0.06 for m in (0.5, 1.0, 2.0))

# ---------------------------------------------------------------- palette ---
# Validated dark-surface palette (8 categorical slots, CVD-checked as a set;
# fixed slot order is the colorblind-safety mechanism — do not reshuffle).
CAT8 = ["#3987e5", "#199e70", "#c98500", "#008300",
        "#9085e9", "#e66767", "#d55181", "#d95926"]
OTHER_C = "#5c5b55"                                   # muted: "other" / noise
INT_ORDER = ["warmup", "groovy", "driving", "peak"]
INT_C = dict(zip(INT_ORDER, ["#184f95", "#2a78d6", "#5598e7", "#86b6ef"]))
SEQ = ["#0d366b", "#184f95", "#256abf", "#3987e5",    # sequential blue ramp,
       "#5598e7", "#86b6ef", "#b7d3f6", "#cde2fb"]    # dark(low) -> light(high)
SURFACE, PAGE = "#1a1a19", "#0d0d0d"
INK, INK2, MUTED = "#ffffff", "#c3c2b7", "#898781"
HAIR = "rgba(255,255,255,0.10)"
ACCENT = "#fab219"                                    # selected track
NEIGHBOR = "#d55181"                                  # constellation neighbors
FONT = 'Inter, system-ui, -apple-system, "Segoe UI", sans-serif'

# --- genre families: hue = family (validated slot), shade = subgenre -------
def _family(g):
    s = str(g).lower()
    if "trance" in s or "goa" in s: return "Trance"
    if "techno" in s or "schranz" in s: return "Techno"
    if "house" in s or ("garage" in s and "uk" not in s): return "House"
    if "hip hop" in s or "rap" in s or "trap" in s or "r&b" in s: return "Hip Hop"
    if ("drum n bass" in s or "jungle" in s or "halftime" in s
            or "neurofunk" in s or "liquid" in s): return "DnB"
    if ("bassline" in s or "dubstep" in s or "grime" in s or "riddim" in s
            or "uk garage" in s or s == "bass music" or "breakbeat" in s
            or "breaks" in s): return "Bass"
    if ("ambient" in s or "downtempo" in s or "chill" in s or "experimental" in s
            or "idm" in s or "drone" in s or "new age" in s or "trip hop" in s): return "Ambient"
    if ("hardcore" in s or "hardstyle" in s or "gabber" in s or "electro" in s
            or "edm" in s or "dance" in s or "dub" in s): return "Electro/Hard"
    return "Other"

FAM_C = {"House": "#3987e5", "Techno": "#e66767", "Trance": "#c98500",
         "Bass": "#9085e9", "DnB": "#199e70", "Hip Hop": "#d55181",
         "Ambient": "#008300", "Electro/Hard": "#d95926", "Other": OTHER_C}

import colorsys
def _shade(hexc, rank):
    """Subgenre shade: same hue, lightness stepped by within-family rank."""
    if rank == 0:
        return hexc
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if l < 0.35:   # dark hue: only lighter steps stay distinct on a dark surface
        l2 = l + {1: 0.14, 2: 0.27, 3: 0.40, 4: -0.08}[rank]
    else:
        anchor = min(max(l, 0.40), 0.60)
        l2 = anchor + {1: 0.15, 2: -0.14, 3: 0.28, 4: -0.24}[rank]
    l2 = min(max(l2, 0.16), 0.86)
    s2 = s if l2 <= l else min(s, 0.72)   # desaturate lighter steps (avoid neon)
    r, g, b = colorsys.hls_to_rgb(h, l2, s2)
    return "#%02x%02x%02x" % tuple(int(v * 255) for v in (r, g, b))

M["family"] = M["genre"].map(_family)
FAM_ORDER = [f for f in M["family"].value_counts().index if f != "Other"] + ["Other"]
GENRE_C = {}   # exact genre -> shaded hex
for f in FAM_ORDER:
    subs = M.loc[M["family"] == f, "genre"].value_counts().index
    for rank, g in enumerate(subs):
        GENRE_C[g] = OTHER_C if f == "Other" else _shade(FAM_C[f], rank if rank < 5 else 0)

M["cluster_lbl"] = ["noise" if c < 0 else f"cluster {c}" for c in CL]
_LBL, _G, _I, _MO = (M["label"].values, M["genre"].values, M["intensity"].values, M["mood"].values)


def _coords(mode):
    return XYZ if mode == "3D" else XY


def _scatter(mode, idxs, **kw):
    """Markers trace over row-indices; customdata = global index (for clicks)."""
    c = _coords(mode)[idxs]
    txt = [f"<b>{_LBL[i]}</b><br>{_G[i]} · {_I[i]} · {_MO[i]}" for i in idxs]
    d = dict(x=c[:, 0], y=c[:, 1], mode="markers", text=txt,
             customdata=[int(i) for i in idxs], hovertemplate="%{text}<extra></extra>", **kw)
    d["meta"] = {"bs": kw.get("marker", {}).get("size", 4.5)}   # base size, for zoom scaling
    if mode == "3D":
        d["z"] = c[:, 2]; return go.Scatter3d(**d)
    return go.Scattergl(**d)


def base_figure(color_by, mode, dim=False):
    """dim=True fades the base cloud so a constellation reads on top of it."""
    is3d = mode == "3D"
    sz = 2.5 if is3d else 4.5
    op = 0.18 if dim else (0.75 if is3d else 0.85)
    fig = go.Figure()
    if color_by == "aggressive":
        fig.add_trace(_scatter(mode, np.arange(N), showlegend=False,
                               marker=dict(size=sz, opacity=op, color=M["aggressive"],
                                           colorscale=[[i / (len(SEQ) - 1), c] for i, c in enumerate(SEQ)],
                                           showscale=not dim,
                                           colorbar=dict(title=dict(text="aggression", font=dict(color=MUTED, size=11)),
                                                         tickfont=dict(color=MUTED, size=10),
                                                         thickness=10, len=0.45, outlinewidth=0, x=1.0))))
    elif color_by == "genre":
        # one trace per (family, shade); legend = one entry per family
        gcol = M["genre"].map(GENRE_C)
        for f in FAM_ORDER:
            first = True
            fam_mask = (M["family"] == f).values
            for c in gcol[fam_mask].value_counts().index:   # biggest shade first
                idxs = np.where(fam_mask & (gcol == c).values)[0]
                if len(idxs):
                    fig.add_trace(_scatter(mode, idxs, name=f, legendgroup=f,
                                           showlegend=first and not dim,
                                           marker=dict(size=sz, opacity=op, color=c)))
                    first = False
    else:
        col = {"intensity": "intensity", "cluster": "cluster_lbl"}[color_by]
        if color_by == "intensity":
            cats = INT_ORDER + sorted(set(M[col].unique()) - set(INT_ORDER))
            cmap = {**INT_C}
        else:  # cluster — hues cycle; position + hover disambiguate, legend hidden
            cats = sorted(M[col].unique())
            cmap = {c: (OTHER_C if c == "noise" else CAT8[int(c.split()[-1]) % 8]) for c in cats}
        show_legend = color_by != "cluster" and not dim
        for c in cats:
            idxs = np.where((M[col] == c).values)[0]
            if len(idxs):
                fig.add_trace(_scatter(mode, idxs, name=str(c), showlegend=show_legend,
                                       marker=dict(size=sz, opacity=op, color=cmap.get(c, OTHER_C))))
    fig.update_layout(
        template=None, autosize=True, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family=FONT, color=INK2),
        uirevision="keep",   # <- preserves zoom / 3D camera across updates
        dragmode="pan" if not is3d else "orbit",
        legend=dict(itemsizing="constant", font=dict(size=11, color=INK2),
                    bgcolor="rgba(26,26,25,0.75)", bordercolor=HAIR, borderwidth=1,
                    x=0.01, y=0.99, xanchor="left", yanchor="top"),
        hoverlabel=dict(bgcolor="#242422", bordercolor="rgba(255,255,255,0.15)",
                        font=dict(family=FONT, size=12, color=INK)))
    hide = dict(showticklabels=False, showgrid=False, zeroline=False, title="")
    if is3d:
        fig.update_layout(scene=dict(xaxis=hide, yaxis=hide, zaxis=hide, bgcolor=SURFACE))
    else:
        fig.update_layout(xaxis=dict(visible=False),
                          yaxis=dict(visible=False, scaleanchor="x", scaleratio=1))
    return fig


def neighbors(idx, k, harmonic=False):
    sims = EMB @ EMB[idx]
    order = np.argsort(-sims); order = order[order != idx]
    if harmonic:   # take a deeper pool, keep only harmonically-compatible neighbors
        sc, sb = CAMELOT[idx], BPM[idx]
        pool = order[:200]
        keep = [j for j in pool if _cam_compatible(sc, CAMELOT[j]) and _bpm_compatible(sb, BPM[j])]
        order = np.array(keep[:k], dtype=int)
    else:
        order = order[:k]
    return order, sims[order]


def _line(mode, ex, ey, ez):
    d = dict(x=ex, y=ey, mode="lines", line=dict(color="rgba(255,255,255,.30)", width=1.5),
             hoverinfo="skip", showlegend=False)
    if mode == "3D": d["z"] = ez; return go.Scatter3d(**d)
    return go.Scattergl(**d)


def _mark(mode, idxs, **kw):
    c = _coords(mode)[idxs]
    d = dict(x=c[:, 0], y=c[:, 1], mode="markers", text=M["label"].values[idxs],
             customdata=[int(i) for i in idxs],
             hovertemplate="%{text}<extra></extra>", showlegend=False, **kw)
    d["meta"] = {"bs": kw.get("marker", {}).get("size", 9)}     # base size, for zoom scaling
    if mode == "3D": d["z"] = c[:, 2]; return go.Scatter3d(**d)
    return go.Scattergl(**d)


app = Dash(__name__, title="Sound Map")

# --- basic-auth gate (protects the public tunnel; change these creds) ---
AUTH = ("dj", "crate-digger-8050")
@app.server.before_request
def _basic_auth():
    from flask import request, Response
    a = request.authorization
    if not a or (a.username, a.password) != AUTH:
        return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="viz"'})


@app.server.route("/audio/<int:idx>")
def _serve_audio(idx):
    if 0 <= idx < len(PATHS) and PATHS[idx] and os.path.exists(PATHS[idx]):
        return send_file(PATHS[idx], conditional=True)
    abort(404)


# ------------------------------------------------------------------ chrome ---
app.index_string = """<!DOCTYPE html>
<html>
<head>
{%metas%}<title>{%title%}</title>{%favicon%}{%css%}
<style>
:root { --surface:#1a1a19; --page:#0d0d0d; --ink:#ffffff; --ink2:#c3c2b7;
        --muted:#898781; --hair:rgba(255,255,255,.10); --accent:#fab219; }
* { box-sizing:border-box }
html,body { margin:0; height:100%; background:var(--page); color:var(--ink);
  font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif; font-size:14px; }
#react-entry-point,#_dash-app-content { height:100% }

/* header + toolbar */
.hdr { display:flex; align-items:baseline; gap:12px; padding:14px 20px 10px; }
.hdr .brand { font-size:16px; font-weight:650; letter-spacing:.04em; }
.hdr .sub { color:var(--muted); font-size:12px; }
.bar { display:flex; align-items:center; gap:18px; flex-wrap:wrap;
  padding:0 20px 12px; border-bottom:1px solid var(--hair); }
.lbl { color:var(--muted); font-size:11px; text-transform:uppercase;
  letter-spacing:.08em; margin-right:-8px; }
.hint { color:var(--muted); font-size:11px; margin-left:auto; }

/* segmented controls (RadioItems) */
.seg { display:flex; background:var(--surface); border:1px solid var(--hair);
  border-radius:8px; padding:2px; }
.seg label { display:flex; align-items:center; padding:5px 12px; border-radius:6px;
  color:var(--ink2); font-size:12.5px; cursor:pointer; user-select:none;
  transition:background .12s,color .12s; }
.seg label:hover { color:var(--ink) }
.seg label:has(input:checked) { background:#33332f; color:var(--ink); font-weight:600;
  box-shadow:0 1px 2px rgba(0,0,0,.4); }
.seg input { position:absolute; opacity:0; width:0; height:0; }

/* clear-selection button */
.btn { background:var(--surface); border:1px solid var(--hair); border-radius:8px;
  color:var(--ink2); font-size:12.5px; padding:6px 13px; cursor:pointer;
  font-family:inherit; transition:background .12s,color .12s,border-color .12s; }
.btn:hover { background:#33332f; color:var(--ink); border-color:rgba(255,255,255,.2); }
.btn:active { background:#262623; }

/* dark dropdown (react-select v1) */
.dd .Select-control { background:var(--surface); border:1px solid var(--hair);
  border-radius:8px; color:var(--ink); height:32px; }
.dd .Select-placeholder,.dd .Select-value-label,.dd .Select-input>input { color:var(--ink2)!important;
  line-height:30px; font-size:13px; }
.dd .Select-value-label { color:var(--ink)!important }
.dd.is-focused>.Select-control,.dd .is-focused .Select-control { border-color:#3987e5!important;
  box-shadow:none!important; background:var(--surface)!important; }
.dd .Select-menu-outer { background:#222220; border:1px solid var(--hair);
  border-radius:8px; margin-top:4px; font-size:13px; overflow:hidden; }
.dd .VirtualizedSelectOption { color:var(--ink2); }
.dd .VirtualizedSelectFocusedOption { background:#33332f; color:var(--ink); }
.dd .Select-arrow { border-top-color:var(--muted) }
.dd .Select-clear { color:var(--muted); font-size:16px }

/* slider (rc-slider) */
.rc-slider-rail { background:#33332f }
.rc-slider-track { background:#3987e5 }
.rc-slider-handle { background:#e8e8e4; border:2px solid #3987e5 }
.rc-slider-mark-text { color:var(--muted); font-size:10px }
.rc-slider-mark-text-active { color:var(--ink2) }

/* main split */
.main { display:flex; height:calc(100vh - 106px); }
.mapwrap { flex:1; min-width:0; }
.panel { width:340px; border-left:1px solid var(--hair); background:var(--page);
  overflow-y:auto; padding:16px; font-size:12.5px; }
.panel::-webkit-scrollbar { width:8px }
.panel::-webkit-scrollbar-thumb { background:#33332f; border-radius:4px }

/* detail card */
.card { background:var(--surface); border:1px solid var(--hair); border-radius:10px;
  padding:14px; }
.card h4 { margin:0 0 10px; font-size:13.5px; line-height:1.35; color:var(--ink); }
.chips { display:flex; gap:6px; flex-wrap:wrap; margin:10px 0 4px; }
.chip { background:#262623; border:1px solid var(--hair); border-radius:99px;
  padding:2px 9px; font-size:11px; color:var(--ink2); }
.meter { display:flex; align-items:center; gap:8px; margin-top:6px; }
.meter .mlbl { width:52px; color:var(--muted); font-size:10.5px; text-transform:uppercase;
  letter-spacing:.06em; }
.meter .mbar { flex:1; height:4px; background:#2c2c2a; border-radius:2px; overflow:hidden; }
.meter .mfill { height:100%; background:#3987e5; border-radius:2px; }
.meter .mval { width:30px; text-align:right; color:var(--ink2); font-size:11px;
  font-variant-numeric:tabular-nums; }
.sec { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.08em;
  margin:16px 0 8px; }
.nb { display:flex; align-items:center; gap:10px; padding:7px 8px; border-radius:8px;
  cursor:pointer; transition:background .1s; }
.nb:hover { background:var(--surface) }
.nb .sim { width:34px; color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }
.nb .nlbl { flex:1; min-width:0; }
.nb .nlbl .t { color:var(--ink2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.nb .nlbl .m { color:var(--muted); font-size:10.5px; }
.empty { color:var(--muted); padding:24px 8px; line-height:1.6; }
/* now-playing line above the audio element */
.nowplaying { margin-top:12px; line-height:1.4; }
.nowplaying .np-art { color:var(--ink); font-weight:600; font-size:13px; }
.nowplaying .np-lbl { color:var(--muted); font-size:11.5px; margin-top:1px; }
audio { width:100%; height:36px; margin-top:8px; filter:invert(.88) hue-rotate(180deg); }
</style>
</head>
<body>
{%app_entry%}
<footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>"""

GRAPH_CFG = {"scrollZoom": True, "displaylogo": False, "responsive": True,
             "doubleClick": "reset",
             "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d",
                                        "zoomIn2d", "zoomOut2d", "toggleSpikelines"]}

app.layout = html.Div([
    html.Div(className="hdr", children=[
        html.Span("SOUND MAP", className="brand"),
        html.Span(f"{N:,} tracks · discogs-effnet embeddings · UMAP", className="sub"),
    ]),
    html.Div(className="bar", children=[
        html.Span("view", className="lbl"),
        dcc.RadioItems(id="mode", className="seg", options=["2D", "3D"], value="2D", inline=True),
        html.Span("color", className="lbl"),
        dcc.RadioItems(id="color", className="seg", value="genre", inline=True,
                       options=[{"label": x, "value": x} for x in ["genre", "intensity", "cluster", "aggressive"]]),
        html.Span("find", className="lbl"),
        html.Div(dcc.Dropdown(id="search", className="dd", placeholder="search artist / title…",
                              options=[{"label": l, "value": i} for i, l in enumerate(M["label"])]),
                 style={"width": "340px"}),
        html.Span("neighbors", className="lbl"),
        html.Div(dcc.Slider(id="k", min=5, max=30, step=5, value=15,
                            marks={5: "5", 15: "15", 30: "30"}),
                 style={"width": "160px", "paddingTop": "6px"}),
        dcc.Checklist(id="harmonic", className="seg", inline=True,
                      options=[{"label": "harmonic", "value": "on"}], value=[]),
        html.Button("✕ clear selection", id="clear", className="btn", n_clicks=0),
        html.Span("click a point to select · click empty space or ✕ to clear · double-click = reset zoom",
                  className="hint"),
    ]),
    html.Div(className="main", children=[
        html.Div(className="mapwrap", children=[
            dcc.Graph(id="map", config=GRAPH_CFG, clear_on_unhover=True,
                      style={"height": "100%", "width": "100%"}),
        ]),
        html.Div(id="panel", className="panel"),
    ]),
    dcc.Store(id="zoom-store"),
    dcc.Store(id="deselect", data=0),        # bumped by an empty-space click (clientside)
    dcc.Store(id="deselect-wire"),           # dummy output for the wiring callback
])

# --- zoom-adaptive marker sizes (clientside: no server round-trip) ----------
# k grows with zoom depth (power curve, capped); bigger marks (star/neighbors)
# scale sub-linearly so the visual hierarchy is preserved.
_FULLX = float(XY[:, 0].max() - XY[:, 0].min())
app.clientside_callback(
    """
    function(relayout, fig) {
        const dc = window.dash_clientside;
        const wrap = document.getElementById('map');
        const gd = wrap && wrap.getElementsByClassName('js-plotly-plot')[0];
        if (!gd) return dc.no_update;
        const fromFig = ((dc.callback_context.triggered[0] || {}).prop_id || '')
                        .indexOf('figure') !== -1;
        const apply = () => {
            if (!gd._fullLayout) return;
            const xa = gd._fullLayout.xaxis;
            if (!xa || !xa.range) return;               // 3D scene: keep base sizes
            const f = (xa.range[1] - xa.range[0]) / %FULLX%;
            const k = Math.min(Math.pow(Math.max(1 / f, 1), 0.35), 3.2);
            if (fromFig) gd.__szK = 1;                  // re-render resets to base
            if (Math.abs(k - (gd.__szK || 1)) < 0.05) return;
            gd.__szK = k;
            const idx = [], sizes = [];
            (gd.data || []).forEach((t, i) => {
                if (t.meta && t.meta.bs) {
                    idx.push(i);
                    sizes.push(t.meta.bs * Math.pow(k, 4.5 / Math.max(t.meta.bs, 4.5)));
                }
            });
            if (idx.length && window.Plotly)
                window.Plotly.restyle(gd, {'marker.size': sizes}, idx);
        };
        setTimeout(apply, fromFig ? 60 : 0);            // let a new figure mount first
        return dc.no_update;
    }
    """.replace("%FULLX%", repr(_FULLX)),
    Output("zoom-store", "data"),
    Input("map", "relayoutData"),
    Input("map", "figure"),
    prevent_initial_call=True,
)

# --- empty-space click -> clear selection (clientside) ----------------------
# 2D uses a WebGL canvas, so a click on a point and on the background hit the
# same DOM node; we can't tell them apart by target. Instead: a plotly_click
# fires ONLY on a point, so we timestamp it, and if a raw click arrives with no
# recent point-click, it was empty space -> bump the `deselect` store.
app.clientside_callback(
    """
    function(fig) {
        const dc = window.dash_clientside;
        const wrap = document.getElementById('map');
        const gd = wrap && wrap.getElementsByClassName('js-plotly-plot')[0];
        if (!gd || gd.__deselectWired) return dc.no_update;
        gd.__deselectWired = true;
        gd.on('plotly_click', function () { gd.__ptClick = Date.now(); });
        gd.addEventListener('click', function () {
            const t = Date.now();
            setTimeout(function () {
                if (t - (gd.__ptClick || 0) > 250)      // no point-click near this click
                    dc.set_props('deselect', {data: (gd.__deselN = (gd.__deselN || 0) + 1)});
            }, 200);
        });
        return dc.no_update;
    }
    """,
    Output("deselect-wire", "data"),
    Input("map", "figure"),
    prevent_initial_call=True,
)


@app.callback(Output("search", "value"),
              Input("map", "clickData"),
              Input({"type": "nb", "index": ALL}, "n_clicks"),
              Input("clear", "n_clicks"),
              Input("deselect", "data"),
              prevent_initial_call=True)
def select_track(clickData, _nb_clicks, _clear, _deselect):
    t = ctx.triggered_id
    if t in ("clear", "deselect"):        # ✕ button or empty-space click -> deselect
        return None
    if t == "map":
        if not clickData:
            return no_update
        cd = clickData["points"][0].get("customdata")
        if cd is None:
            return no_update
        return int(cd[0]) if isinstance(cd, (list, tuple)) else int(cd)  # customdata = global index
    if isinstance(t, dict) and t.get("type") == "nb":
        if not ctx.triggered[0]["value"]:      # panel re-render, not a real click
            return no_update
        return int(t["index"])
    return no_update


def _meter(label, val):
    try:
        v = max(0.0, min(1.0, float(val)))
    except (TypeError, ValueError):
        return None
    return html.Div(className="meter", children=[
        html.Span(label, className="mlbl"),
        html.Div(html.Div(className="mfill", style={"width": f"{v*100:.0f}%"}), className="mbar"),
        html.Span(f"{v:.2f}", className="mval"),
    ])


@app.callback(Output("map", "figure"), Output("panel", "children"),
              Input("color", "value"), Input("search", "value"), Input("k", "value"),
              Input("mode", "value"), Input("harmonic", "value"))
def render(color_by, sel, k, mode, harmonic):
    harmonic_on = bool(harmonic) and "on" in harmonic
    fig = base_figure(color_by, mode, dim=sel is not None)
    if sel is None:
        return fig, html.Div(className="empty", children=[
            html.Div("No track selected.", style={"color": INK2, "marginBottom": "6px"}),
            html.Div("Click a point on the map or use the search box. "
                     "The constellation shows its nearest neighbors by sound."),
        ])
    sel = int(sel); nb, sims = neighbors(sel, k, harmonic=harmonic_on)
    C = _coords(mode); ex, ey, ez = [], [], []
    for j in nb:
        ex += [C[sel, 0], C[j, 0], None]; ey += [C[sel, 1], C[j, 1], None]
        if mode == "3D": ez += [C[sel, 2], C[j, 2], None]
    fig.add_trace(_line(mode, ex, ey, ez))
    fig.add_trace(_mark(mode, nb, marker=dict(size=(6 if mode == "3D" else 9), color=NEIGHBOR,
                                              line=dict(color="white", width=1))))
    star = dict(size=(10 if mode == "3D" else 15), color=ACCENT,
                symbol=("diamond" if mode == "3D" else "star"), line=dict(color="black", width=1))
    fig.add_trace(_mark(mode, np.array([sel]), marker=star))
    r = M.loc[sel]
    meters = [m for m in (_meter("aggr", r["aggressive"]), _meter("relax", r["relaxed"]),
                          _meter("happy", r["happy"])) if m is not None]
    bpm_txt = f"{BPM[sel]:.0f} BPM" if np.isfinite(BPM[sel]) else "— BPM"
    key_txt = f"{CAMELOT[sel]} ({KEYNAME[sel]})" if CAMELOT[sel] else "—"
    hdr = "Sounds like · harmonic" if harmonic_on else "Sounds like"
    # now-playing: artist, then record label · year (from discogs) when known
    artist = ARTIST[sel] or "Unknown artist"
    lbl, yr = REC_LABEL[sel], YEAR[sel]
    lbl_bits = [b for b in (lbl or None, str(int(yr)) if yr else None) if b]
    now_playing = [html.Div(artist, className="np-art")]
    if lbl_bits:
        now_playing.append(html.Div(" · ".join(lbl_bits), className="np-lbl"))
    panel = [
        html.Div(className="card", children=[
            html.H4(r["label"]),
            html.Div(className="chips", children=[
                html.Span(r["genre"], className="chip"),
                html.Span(r["intensity"], className="chip"),
                html.Span(r["mood"], className="chip"),
                html.Span(bpm_txt, className="chip"),
                html.Span(key_txt, className="chip"),
            ]),
            *meters,
            html.Div(className="nowplaying", children=now_playing),
            html.Audio(src=f"/audio/{sel}", controls=True, autoPlay=True),
        ]),
        html.Div(f"{hdr} · {len(nb)}", className="sec"),
    ]
    if not len(nb):
        panel.append(html.Div("No harmonically-compatible neighbors in range." if harmonic_on
                              else "No neighbors.", className="empty"))
    for j, s in zip(nb, sims):
        rj = M.loc[j]
        parts = []
        if np.isfinite(BPM[j]): parts.append(f"{BPM[j]:.0f}")
        if CAMELOT[j]: parts.append(CAMELOT[j])
        parts += [rj["genre"], rj["intensity"]]
        panel.append(html.Div(className="nb", id={"type": "nb", "index": int(j)}, n_clicks=0, children=[
            html.Span(f"{s:.2f}", className="sim"),
            html.Div(className="nlbl", children=[
                html.Div(rj["label"], className="t", title=rj["label"]),
                html.Div(" · ".join(str(p) for p in parts), className="m"),
            ]),
        ]))
    return fig, panel


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8050, debug=False)
