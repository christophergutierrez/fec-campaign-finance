# INFOGRAPHIC_EXPORT.md — the `--export-infographic` contract

`bin/influence.py <candidate> --export-infographic DIR` writes one **chart-ready
JSON file per relevant funding "angle"** for a single candidate. Each file is a
self-contained brief for one static chart, designed to hand directly to an
infographic maker.

```bash
uv run bin/influence.py S6MI00426 --export-infographic infographics/stevens
uv run bin/influence.py H0NM03102 --export-infographic infographics/leger-fernandez-nm03 \
    --region-note "Represents Taos, NM (NM-03)"
```

Output goes to `DIR` as `NN-<angle-id>.json` (e.g. `01-donor-size.json`). `DIR` is
created if needed. **`infographics/` is git-ignored** — these files are regenerable
output, not source; the generator (this mode) is the durable artifact. `--region-note`
is an optional locality string for the candidate header; it defaults to the seat.

This doc describes the *output contract*. The underlying metrics, denominators, and
materiality floors are defined once in `docs/CALCULATIONS.md` and the `sql/` layer —
this mode only selects, frames, and serializes them. It does not redefine any formula.

## Angle catalog

| `NN` | id | kind | metric | emitted when | `featured` when |
|---|---|---|---|---|---|
| 01 | `donor-size` | structural | `small_dollar_share` | individual money > 0 | small-dollar < 10% (concentrated) |
| 02 | `geography` | structural | `out_of_state_share` | individual money > 0 | ≥ 40% out-of-state |
| 03 | `composition` | structural | `outside_share` | any money | ≥ 30% outside (IE) |
| 04 | `pac-roster` | structural | `pac_share` | direct PAC > 0 | ≥ 30% PAC of raised |
| 05 | `interest-blocs` | signal | `interest_share` | a curated bloc ≥ 8% of backing | always (it cleared the floor) |
| 06 | `ie-air-war` | signal | `outside_share` | IE support ≥ 30% of backing, or material opposing IE | ≥ 30% outside support |
| 07 | `donor-blocs` | signal | `donor_bloc_share` | a keyword bloc ≥ 8% of individual money | always (it cleared the floor) |

Numbers are fixed per angle, so gaps are expected (a candidate with no IE air war
has no `06`). Floors come from the `FLOOR_*` constants in `influence.py`, which are
the same thresholds the `--rank` mode uses.

## Inclusion rule (why some angles are skipped)

- **Structural angles (01–04)** are emitted whenever their channel has money — even
  *below* their headline floor — because the inverse is itself informative ("rooted
  at home", "no air war", "PAC roster, not PAC-dependent").
- **Signal angles (05–07)** are emitted **only when they clear a materiality floor**,
  so the export never ships a chart of noise (e.g. a single 0.2% interest bloc).

Skipped angles are reported on stderr with the reason, e.g.
`· skipped  interest-blocs   (largest bloc 0.2% < 8.0% floor)`.

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
    "metric": "small_dollar_share",
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
