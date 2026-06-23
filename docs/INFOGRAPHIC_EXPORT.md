# INFOGRAPHIC_EXPORT.md — the `--export-infographic` contract

`bin/influence.py <candidate> --export-infographic DIR` writes one **chart-ready
JSON file per relevant funding "angle"** for a single candidate. Each file is a
self-contained brief for one static chart, designed to hand directly to an
infographic maker.

```bash
# Canonical path (recommended): pass --export-infographic with no DIR.
uv run bin/influence.py S0NM00058 --export-infographic \
    --region-note "U.S. Senator for New Mexico"
# -> infographics/nm-senate-lujan-ben-ray/{geography,pac-roster,...}.json

# Or pass an explicit directory:
uv run bin/influence.py H0NM03102 --export-infographic /tmp/leger
```

**Naming convention.** With no `DIR`, the export writes to a standard per-candidate
folder under `infographics/`:

```
infographics/<st>-<chamber>[-<dd>]-<last-first>/<angle-id>.{json,svg}
   nm-senate-lujan-ben-ray/geography.json
   nm-house-03-leger-fernandez-teresa/donor-size.json   # district zero-padded, House only
   us-president-<last-first>/...
```

`<chamber>` is `house` / `senate` / `president` (not "congress" — both chambers are
Congress). The name slugs from the raw FEC `LAST, FIRST`, so folders sort by surname.
**`infographics/` is git-ignored** — the JSON/SVG are regenerable artifacts that live
in this directory structure locally but are never committed; the generator is the
durable source. `--region-note` is an optional locality string for the candidate
header; it defaults to the seat.

This doc describes the *output contract*. The underlying metrics, denominators, and
materiality floors are defined once in `docs/CALCULATIONS.md` and the `sql/` layer —
this mode selects, frames, and serializes them. It reimplements no channel formula;
the one deliberate, documented exception is that the donor-size graphic reports
`small_gift_share` over a positive-itemized-gift denominator so its chart reconciles
to 100% (see the `small_gift_share` note in `docs/CALCULATIONS.md`).

## Angle catalog

| `NN` | id | kind | metric | emitted when (viability floor) | `featured` when |
|---|---|---|---|---|---|
| 01 | `donor-size` | structural | `small_gift_share` | itemized gifts ≥ $25k and ≥ 50 gifts | small-gift share < 10% (concentrated) |
| 02 | `geography` | structural | `out_of_state_share` | individual ≥ $25k | ≥ 40% out-of-state |
| 03 | `composition` | structural | `outside_share` | total backing ≥ $25k | ≥ 30% outside (IE) |
| 04 | `pac-roster` | structural | `pac_share` | direct PAC ≥ $10k | ≥ 30% PAC of raised |
| 05 | `interest-blocs` | signal | `interest_share` | backing ≥ $25k and a curated bloc ≥ 8% of backing | always (it cleared the floor) |
| 06 | `ie-air-war` | signal | `outside_share` | backing ≥ $25k and IE support ≥ 30% (or material opposing IE) | ≥ 30% outside support |
| 07 | `donor-blocs` | signal | `donor_bloc_share` | individual ≥ $25k and a keyword bloc ≥ 8% of individual money | always (it cleared the floor) |

Numbers are fixed per angle, so gaps are expected (a candidate with no IE air war
has no `06`). Headline floors come from the `FLOOR_*` constants in `influence.py`
(the same thresholds `--rank` uses); viability floors from the `MIN_*` constants
(see below).

## Inclusion rule (why some angles are skipped)

- **Structural angles (01–04)** are emitted whenever their channel clears its
  **viability floor** (`MIN_*`, below) — even *below* their headline floor — because
  the inverse is itself informative ("rooted at home", "no air war", "PAC roster, not
  PAC-dependent").
- **Signal angles (05–07)** are emitted only when they clear both their viability
  floor and a **materiality share floor**, so the export never ships a chart of noise
  (e.g. a single 0.2% interest bloc).
- A candidate clearing **no** viability floor produces no files (not viable).

Skipped angles are reported on stderr with the reason, e.g.
`· skipped  interest-blocs   (largest bloc 0.2% < 8.0% floor)`.

### Viability floors

Before any headline-floor logic, each angle must clear a **volume floor** on its own
denominator — below it there is too little money/data to be worth a graphic:

| Floor | Default | Applies to |
|---|---|---|
| `MIN_INDIVIDUAL` | $25,000 | donor-size, geography, donor-blocs |
| `MIN_DONORS` | 50 | donor-size (a waffle needs enough positive gifts) |
| `MIN_BACKING` | $25,000 | composition, interest-blocs, ie-air-war |
| `MIN_PAC` | $10,000 | pac-roster |

Floors are per channel, so a candidate thin on one channel but heavy on another
(e.g. a pure IE-air-war target) still gets the angles that *are* substantial. A
candidate that clears **none** of them is **not viable**: the export writes no files
and says so. (Constants live at the top of `bin/influence.py`.)

## `featured` vs context

Every emitted file carries `angle.featured` (bool):

- `featured: true` — the metric **cleared its headline floor**; this is a lead story.
- `featured: false` — emitted as **context** (a structural angle below its floor). The
  framing is the inverse story, not the headline.

So a typical safe-seat incumbent emits four files with only `donor-size` featured,
while a candidate in a money-heavy race may emit six with most featured.

## JSON schema

```jsonc
{
  "candidate": {
    "cand_id": "S6MI00426",
    "name": "Haley Stevens",        // display name (title-cased)
    "name_fec": "STEVENS, HALEY",   // raw FEC string, for traceability
    "party": "DEM",
    "office": "U.S. Senate",
    "state": "MI", "district": "00",
    "incumbency": "Challenger",     // from CAND_ICI (Incumbent/Challenger/Open seat)
    "election_year": 2026,
    "region_note": "U.S. Senate, MI"  // overridable via --region-note
  },
  "angle": {
    "id": "donor-size",
    "title": "Big checks vs. grassroots",
    "reason": "…why this angle was chosen, citing the value and the floor…",
    "metric": "small_gift_share",
    "metric_value": 0.0034,         // the metric, rounded to 4 dp
    "threshold": "…the floor this metric did or did not clear…",
    "featured": true
  },
  "headline": "…short chart title…",
  "subhead": "…one-line description…",
  "chart": {
    "type": "bar",                  // or "stacked_bar" (composition)
    "unit": "USD",
    "denominator": { "label": "…", "amount": 5522597.0, "count": 1234 },
    "rows": [ { "…": "…", "amount": 0.0, "share_of_individual": 0.0 } ],
    "summary": { }                  // present on geography / composition
  },
  "footnotes": ["…caveats…"],
  "source": "FEC 2026 cycle; sql/queries/…"
}
```

### Reconciliation invariant

For the four **partition** charts (`donor-size`, `geography`, `composition`,
`pac-roster`) the row amounts with a **non-null share** sum to
`chart.denominator.amount` — they carry an explicit remainder row ("Other states",
"All other PACs") so the chart is whole. The test suite asserts this.

Exceptions, by design:
- **`composition`** includes an `Independent expenditures opposing` row with
  `share_of_backing: null` — opposing IE works for the opponent and is *excluded*
  from total backing, so it does not count toward the sum.
- **`interest-blocs` / `donor-blocs`** show only flagged categories that are a
  *subset* of the denominator; rows are not expected to sum to it.
- **`ie-air-war`** lists the top-N spenders without a remainder row.

## What the tool will not do

It emits honest, data-driven prose and the raw `cmte_tp` for PACs. It does **not**
invent narrative explanations or editorial category tags ("labor", "trial lawyers")
that require human knowledge — enrich those by hand after export if desired.

## Rendering an angle to SVG (`bin/render_infographic.py`)

The angle JSON is the data; `bin/render_infographic.py` turns one angle into an
**exact SVG** — the "truth layer". Every number, bar, and dot is computed from the
JSON, so figures and geometry cannot be wrong. **`donor-size` and `geography` are
implemented today**; other angles exit with a clear message until a body renderer is
added (see "Adding a new graphic type" below).

```bash
uv run bin/render_infographic.py infographics/nm-senate-lujan-ben-ray/geography.json
# -> writes geography.svg alongside it; rasterize with:
rsvg-convert -w 1400 infographics/nm-senate-lujan-ben-ray/geography.svg -o geography.png
```

**Why SVG, and why it composites *on top*.** Diffusion image models (Nano Banana,
etc.) render beautiful art but cannot be trusted with exact text, counts, or bar
heights. So the pipeline is: generative model produces the *art* (portrait,
background, framing) with no load-bearing numbers, and this deterministic SVG is
layered **over** it. The model never renders a digit, so it can never corrupt one.

### Architecture (built to grow to many graphic types)

- `Svg` — a small builder: primitives (`text`/`rect`/`circle`/`line`) plus reusable
  chart components (`waffle`, `stacked_bar`, `scaled_columns`, `legend`, `donut`,
  `hbar_ranking`). The waffle auto-scales its dot unit to fit a fixed box (1 dot =
  1 gift for small fields, "≈ N gifts" for large ones) so any candidate fits.
- `draw_shell()` — the chrome every angle shares (headline + subhead with automatic
  crimson emphasis on money/percent tokens, candidate card, callout strip, footnotes,
  source), read **straight from the JSON schema**. Identical for all angles.
- `@angle("<id>")` registry — each graphic is a small body function that draws only
  the middle region from the chart components.

### Adding a new graphic type

1. Make sure `influence.py` emits the angle (or reuse an existing one's JSON).
2. Add a body function and register it:
   ```python
   @angle("geography")
   def body_geography(c: Svg, d: dict) -> None:
       ...  # compose c.stacked_bar / c.scaled_columns / c.legend over the body region
   ```
   The shell already drew the headline, card, callout, and footnotes from the schema,
   so the body only handles the angle-specific charts.
3. Add a fixture case to `tests/test_render_infographic.py`.

Like the JSON, rendered SVG/PNG live under the git-ignored `infographics/` — they are
regenerable output. The generator is the durable artifact.
