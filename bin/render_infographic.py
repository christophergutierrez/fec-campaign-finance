#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Render an angle JSON (from `influence.py --export-infographic`) to an exact SVG.

This is the deterministic "truth layer": every number, bar, and dot is computed
from the angle JSON, so the figures and geometry cannot be wrong. Visual
embellishment (portrait, background art) is a separate compositing step that
layers *behind* this output — the SVG is always stamped on top, so a generative
image model can never corrupt a digit.

Architecture, built to grow to many graphic types:
  * `Svg` — a tiny builder with primitives (text/rect/circle/line) plus reusable
    chart components (waffle, stacked_bar, scaled_columns, legend).
  * `draw_shell()` — the chrome every angle shares (headline, candidate card,
    callout strip, footnotes/source), all read straight from the JSON schema.
  * `@angle(id)` registry — each graphic is a small body function that draws only
    the middle region from chart primitives. Add one to support a new angle.

    uv run bin/render_infographic.py infographics/leger-fernandez-nm03/01-donor-size.json
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

# ── Canvas + palette ────────────────────────────────────────────────────────
W, H = 1400, 920
MARGIN = 30
BODY_TOP, BODY_BOTTOM = 258, 775          # region handed to each angle body
INK, MUTE, ACCENT = "#0f172a", "#64748b", "#b3122e"
# A neutral slate ramp ending in the accent — reused wherever a few ordered
# categories need coloring (gift size, channels, …).
RAMP = ["#cbd5e1", "#94a3b8", "#64748b", ACCENT]

_KEY = re.compile(r"(\$[\d,]+(?:\.\d+)?|\d[\d,]*\.?\d*%|\$0)")


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def money(x: float) -> str:
    return f"${float(x):,.0f}"


# ── SVG builder ───────────────────────────────────────────────────────────────
class Svg:
    def __init__(self, w: int = W, h: int = H) -> None:
        self.w, self.h = w, h
        self.parts: list[str] = [f'<rect width="{w}" height="{h}" fill="#ffffff"/>']

    def raw(self, s: str) -> None:
        self.parts.append(s)

    # primitives -------------------------------------------------------------
    def rect(self, x, y, w, h, fill="none", rx=0, stroke=None, sw=1) -> None:
        st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        self.raw(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                 f'rx="{rx}" fill="{fill}"{st}/>')

    def circle(self, cx, cy, r, fill) -> None:
        self.raw(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{fill}"/>')

    def line(self, x1, y1, x2, y2, stroke="#e2e8f0", sw=2) -> None:
        self.raw(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                 f'stroke="{stroke}" stroke-width="{sw}"/>')

    def text(self, x, y, s, size, weight=400, fill=INK, anchor="start",
             keys=False) -> None:
        """Draw text. With keys=True, money/percent tokens are accented + bold."""
        if keys:
            body = "".join(
                f'<tspan fill="{ACCENT}" font-weight="800">{esc(t)}</tspan>'
                if _KEY.fullmatch(t) else esc(t)
                for t in _KEY.split(str(s)) if t != "")
        else:
            body = esc(s)
        self.raw(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
                 f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">'
                 f'{body}</text>')

    # reusable chart components ---------------------------------------------
    def waffle(self, x, y, counts, colors, cols, pitch, r, max_rows) -> tuple:
        """Category waffle that always fits cols×max_rows.

        Uses one dot per unit, scaling the unit up only when the count
        exceeds the box (so small fields stay 1 dot = 1 gift). Returns
        (bottom_y, unit) so the caller can label the dot scale honestly.
        """
        total = sum(counts)
        capacity = cols * max_rows
        unit = max(1, -(-total // capacity))            # ceil division
        cells_b = [round(n / unit) for n in counts]
        while sum(cells_b) > capacity:                  # rounding slack guard
            unit += 1
            cells_b = [round(n / unit) for n in counts]
        bounds, acc = [], 0
        for n in cells_b:
            acc += n
            bounds.append(acc)
        out = []
        for i in range(acc):
            b = next(k for k, c in enumerate(bounds) if i < c)
            cx = x + (i % cols) * pitch + pitch / 2
            cy = y + (i // cols) * pitch + pitch / 2
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" '
                       f'fill="{colors[b]}"/>')
        self.raw("".join(out))
        bottom = y + ((acc - 1) // cols + 1) * pitch if acc else y
        return bottom, unit

    def stacked_bar(self, x, y, w, h, segments, title=None) -> None:
        """100% bar. segments: list of (frac, color, label, dark, highlight)."""
        if title:
            self.text(x, y - 8, title, 14, 700, MUTE)
        cx = float(x)
        for frac, color, label, dark, hi in segments:
            sw = frac * w
            self.rect(cx, y, sw, h, fill=color)
            if sw >= 40:
                self.text(cx + sw / 2, y + h / 2 + 5, label, 15, 700,
                          "#ffffff" if dark else INK, anchor="middle")
            elif hi:
                self.text(cx + sw / 2, y - 6, label, 14, 700, ACCENT,
                          anchor="middle")
            cx += sw
        self.rect(x, y, w, h, stroke="#ffffff", sw=2)

    def scaled_columns(self, base_y, max_h, ref, columns) -> None:
        """columns: list of (x, width, amount, color, title, subtitle)."""
        for cx, cw, amt, color, title, sub in columns:
            ch = max_h * (amt / ref) if ref else 0
            self.rect(cx, base_y - ch, cw, ch, fill=color)
            self.text(cx + cw / 2, base_y - ch - 8, money(amt), 18, 800, INK,
                      anchor="middle")
            self.text(cx + cw / 2, base_y + 20, title, 13, 700, INK, anchor="middle")
            self.text(cx + cw / 2, base_y + 38, sub, 12, 400, MUTE, anchor="middle")

    def legend(self, x, y, items, dy=22) -> None:
        """items: list of (color, text). text may contain key tokens."""
        for i, (color, label) in enumerate(items):
            yy = y + i * dy
            self.circle(x + 6, yy - 4, 6.5, color)
            self.text(x + 22, yy, label, 15, 400, INK, keys=True)

    def render(self) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} '
                f'{self.h}" font-family="Helvetica Neue, Arial, sans-serif">\n'
                + "\n".join(self.parts) + "\n</svg>\n")


# ── Shared chrome (identical for every angle, read from the schema) ───────────
def draw_shell(c: Svg, d: dict) -> None:
    cand = d["candidate"]
    c.text(MARGIN, 58, d["headline"], 35, 800, INK, keys=True)
    c.text(MARGIN, 90, d.get("subhead", ""), 17, 400, MUTE)
    c.rect(MARGIN, 104, 120, 4, fill=ACCENT)

    # Candidate card
    c.rect(MARGIN, 128, W - 2 * MARGIN, 92, fill="#f1f5f9", rx=10, stroke="#cbd5e1")
    initials = "".join(w[0] for w in cand["name"].split()[:3]).upper()
    c.circle(86, 174, 38, INK)
    c.text(86, 184, initials, 26, 700, "#ffffff", anchor="middle")
    c.text(142, 168, cand["name"], 27, 700, INK)
    meta = (f'FEC {cand["cand_id"]}   •   {cand["party"]}   •   {cand["office"]}, '
            f'{cand["state"]}-{cand["district"]}   •   {cand["incumbency"]}   •   '
            f'{cand["region_note"]}')
    c.text(142, 200, meta, 17, 400, MUTE)

    # Callout strip (angle label + threshold + denominator). "Featured" means the
    # metric cleared its headline floor; otherwise it is shown as context.
    den = d["chart"]["denominator"]
    kind = "FEATURED ANGLE" if d["angle"].get("featured") else "CONTEXT ANGLE"
    flag = (f'{kind}: {d["angle"]["title"]}     |     {d["angle"]["threshold"]}'
            f'     |     {den["label"]}: {money(den["amount"])}')
    c.rect(MARGIN, 788, W - 2 * MARGIN, 42, fill="#fff5f6", rx=8, stroke=ACCENT)
    c.text(W / 2, 814, flag, 15, 400, INK, anchor="middle")

    # Footnotes + source
    fy = 858
    for i, fn in enumerate(d.get("footnotes", []), 1):
        c.text(MARGIN, fy, f"{i}. {fn}", 12.5, 400, MUTE)
        fy += 18
    c.text(MARGIN, fy + 2, f'Source: {d["source"]}', 12.5, 400, MUTE)


# ── Angle bodies (registry) ───────────────────────────────────────────────────
REGISTRY: dict[str, callable] = {}


def angle(aid: str):
    def deco(fn):
        REGISTRY[aid] = fn
        return fn
    return deco


# Canonical gift-size buckets in display order. The label strings must match
# influence.py's BUCKET_LABEL: real exports omit empty buckets (and may include a
# "Refunds/negative" one), so rows are matched by name and missing tiers are
# zero-filled — never indexed positionally.
DONOR_BUCKETS = ["Small (≤$200)", "Mid ($201–$999)",
                 "Large ($1k–$3.4k)", "Maxed out (≥$3,500)"]


@angle("donor-size")
def body_donor_size(c: Svg, d: dict) -> None:
    names = ["Grassroots", "Mid", "Large", "Maxed-out"]
    short = ["≤$200", "$201–$999", "$1k–$3.4k", "≥$3,500"]
    den_amt = float(d["chart"]["denominator"]["amount"]) or 0.0
    den_n = int(d["chart"]["denominator"]["count"]) or 0
    by_label = {r.get("bucket"): r for r in d["chart"]["rows"]}
    counts = [int(by_label.get(lbl, {}).get("count", 0) or 0) for lbl in DONOR_BUCKETS]
    amts = [float(by_label.get(lbl, {}).get("amount", 0) or 0) for lbl in DONOR_BUCKETS]
    msh = [(a / den_amt) if den_amt else 0.0 for a in amts]   # money share
    # Counts are itemized gifts (contribution rows), not unique donors.
    gsh = [(n / den_n) if den_n else 0.0 for n in counts]     # gift-count share
    GRASS, MAX = 0, 3
    MIDX = 702
    c.line(MIDX, 250, MIDX, BODY_BOTTOM)

    # LEFT — waffle of gifts, one dot each
    bottom, unit = c.waffle(34, 306, counts, RAMP, cols=58, pitch=11.0, r=4.0,
                            max_rows=33)
    dot_scale = ("each dot is one gift" if unit == 1
                 else f"each dot ≈ {unit} gifts")
    c.text(MARGIN, 270, f"THE GIFTS — {den_n:,} ITEMIZED CONTRIBUTIONS", 19, 700, INK)
    c.text(MARGIN, 292, f"{dot_scale}, colored by gift size · crimson = largest gifts "
           f"(≥$3,500), {gsh[MAX]*100:.1f}% of gifts", 14, 400, MUTE)
    c.legend(MARGIN + 4, bottom + 24, [
        (RAMP[b], f"{names[b]} ({short[b]}) — {counts[b]:,} gifts · "
                  f"{gsh[b]*100:.1f}% of gifts · {msh[b]*100:.1f}% of money")
        for b in range(4)])

    # RIGHT — the inversion
    RX, RW = 720, 650
    c.text(RX, 270, "THE INVERSION — SHARE OF GIFTS vs. SHARE OF DOLLARS", 19, 700, INK)

    def segs(shares):
        return [(shares[b], RAMP[b], f"{shares[b]*100:.1f}%", b >= 2, b == MAX)
                for b in range(4)]
    c.stacked_bar(RX, 322, RW, 46, segs(gsh), f"SHARE OF GIFTS  ·  {den_n:,} gifts")
    c.stacked_bar(RX, 410, RW, 46, segs(msh), f"SHARE OF DOLLARS  ·  {money(den_amt)}")

    c.text(RX, 506, f"{gsh[MAX]*100:.1f}% of gifts supplied {msh[MAX]*100:.1f}% "
           f"of the money.", 20, 400, INK, keys=True)
    c.text(RX, 534, f"{gsh[GRASS]*100:.1f}% of gifts supplied just "
           f"{msh[GRASS]*100:.1f}% of the money.", 20, 400, INK, keys=True)

    ratio = amts[MAX] / amts[GRASS] if amts[GRASS] else 0
    c.text(RX, 582, f"THE TWO GROUPS’ DOLLARS, TO SCALE ({ratio:.1f}×)", 14, 700, MUTE)
    c.scaled_columns(760, 150, amts[MAX], [
        (RX + 90, 120, amts[MAX], RAMP[MAX], names[MAX].upper(),
         f"{short[MAX]} · {counts[MAX]:,} gifts"),
        (RX + 330, 120, amts[GRASS], RAMP[GRASS], names[GRASS].upper(),
         f"{short[GRASS]} · {counts[GRASS]:,} gifts")])


# ── Entry point ───────────────────────────────────────────────────────────────
def render(d: dict) -> str:
    aid = d["angle"]["id"]
    if aid not in REGISTRY:
        sys.exit(f"No SVG renderer for angle '{aid}' yet "
                 f"(have: {', '.join(sorted(REGISTRY))}).")
    c = Svg()
    draw_shell(c, d)
    REGISTRY[aid](c, d)
    return c.render()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render an angle JSON to an exact SVG.")
    ap.add_argument("angle_json", help="path to one NN-<angle>.json file")
    ap.add_argument("-o", "--out", help="output .svg path (default: alongside input)")
    args = ap.parse_args(argv)

    d = json.loads(Path(args.angle_json).read_text())
    out = Path(args.out) if args.out else Path(args.angle_json).with_suffix(".svg")
    out.write_text(render(d))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
