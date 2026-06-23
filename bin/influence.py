#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb>=1.1",
# ]
# ///
"""
Influence profile for a single candidate, or a cohort ranking.

SQL lives under sql/views and sql/queries. This script installs the canonical
read-only temp views, binds parameters, and handles presentation only.
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from pathlib import Path

from sqlutil import install_temp_views, run_rendered_sql, run_sql

ROOT = Path(__file__).resolve().parent.parent

NEAR_MAX = 3500

BLOCS: list[dict] = [
    {
        "label": "Tribal / Indian gaming",
        "field": "contributor_name",
        "patterns": ["tribe", "nation", "band of", "pueblo", "indian",
                     "rancheria", "pomo", "navajo", "apache", "shoshone",
                     "cherokee", "choctaw", "chickasaw", "seminole", "sioux"],
    },
    {"label": "Oil & gas / energy", "field": "employer",
     "patterns": ["oil", "gas", "energy", "petroleum", "exxon", "chevron",
                  "halliburton", "conoco", "occidental"]},
    {"label": "Crypto / digital assets", "field": "employer",
     "patterns": ["crypto", "blockchain", "coinbase", "digital asset", "web3"]},
    {"label": "Pharma / health", "field": "employer",
     "patterns": ["pharma", "pfizer", "merck", "biotech", "health system",
                  "hospital"]},
    {"label": "Law / litigation", "field": "employer",
     "patterns": ["law firm", "llp", "attorneys", "trial law", "law offices"]},
    {"label": "Real estate / development", "field": "employer",
     "patterns": ["real estate", "realty", "development", "properties",
                  "construction"]},
    {"label": "Finance / investment", "field": "employer",
     "patterns": ["capital", "partners", "investment", "ventures", "equity",
                  "asset management", "hedge"]},
    {"label": "Tech", "field": "employer",
     "patterns": ["google", "meta", "microsoft", "amazon", "apple", "alphabet",
                  "software", "technologies"]},
]

FLOOR_OUT_OF_STATE = 0.40
FLOOR_PAC = 0.30
FLOOR_OUTSIDE = 0.30
FLOOR_LOW_SMALL = 0.90
FLOOR_BLOC = 0.08

# Viability floors for the infographic export: below these, an angle has too
# little money/data behind it to be worth a graphic. Applied per channel, so a
# candidate thin on one channel but heavy on another still gets its real angles;
# a candidate clearing none of them produces no files at all.
MIN_INDIVIDUAL = 25_000   # donor-size, geography, donor-blocs (individual $)
MIN_DONORS = 50           # donor-size needs enough positive gifts for a waffle
MIN_BACKING = 25_000      # composition, interest-blocs, ie-air-war (total backing $)
MIN_PAC = 10_000          # pac-roster (direct PAC $)


def pattern_clause(bloc: dict) -> str:
    col = bloc["field"]
    if col not in {"contributor_name", "employer"}:
        raise ValueError(f"Unsupported bloc field: {col}")
    clauses = []
    for pattern in bloc["patterns"]:
        escaped = pattern.replace("'", "''")
        clauses.append(f"{col} ILIKE '%{escaped}%'")
    return " OR ".join(clauses)


def money(x) -> str:
    return f"${float(x):,.0f}" if x is not None else "$0"


def pct(part, whole) -> str:
    whole = float(whole or 0)
    if not whole:
        return "  n/a"
    return f"{100.0 * float(part) / whole:5.1f}%"


def resolve_candidate(con, query: str):
    looks_like_id = query[:1] in "HSP" and query[1:2].isdigit() and len(query) >= 8
    if looks_like_id:
        rows = con.execute(
            "SELECT CAND_ID, CAND_NAME, CAND_OFFICE, CAND_OFFICE_ST, "
            "CAND_OFFICE_DISTRICT, CAND_PTY_AFFILIATION "
            "FROM dim_candidates WHERE CAND_ID = ?", [query.upper()]).fetchall()
    else:
        rows = con.execute(
            "SELECT CAND_ID, CAND_NAME, CAND_OFFICE, CAND_OFFICE_ST, "
            "CAND_OFFICE_DISTRICT, CAND_PTY_AFFILIATION "
            "FROM dim_candidates WHERE CAND_NAME ILIKE ? ORDER BY CAND_NAME",
            ["%" + query + "%"]).fetchall()

    if not rows:
        sys.exit(f"No candidate matches {query!r}.")
    by_id = {r[0]: r for r in rows}
    if len(by_id) > 1:
        print(f"{query!r} matches {len(by_id)} candidates — be more specific "
              f"or pass a CAND_ID:\n")
        for r in by_id.values():
            print(f"  {r[0]}  {r[1]:<30} {r[2]}-{r[4]} {r[3]} ({r[5]})")
        sys.exit(1)
    return next(iter(by_id.values()))


def committee_count(con, cand_id: str) -> int:
    return con.execute(
        "SELECT count(*) FROM candidate_committees WHERE CAND_ID = ?",
        [cand_id],
    ).fetchone()[0]


def header(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def percentile_fn(values: list[float]):
    s = sorted(values)
    n = max(1, len(s))
    return lambda x: bisect.bisect_left(s, x) / n


def rank_mode(con, args) -> int:
    rows = run_sql(
        con,
        "queries/rank_base.sql",
        office=args.office,
        state=args.state.upper() if args.state else None,
        incumbents=args.incumbents,
        min_individual=args.min,
    ).fetchall()
    if not rows:
        sys.exit("No candidates matched the cohort filters.")

    keyword_amounts: dict[str, dict[str, float]] = {}
    for bloc in BLOCS:
        for cid, amt in run_rendered_sql(
            con,
            "queries/rank_keyword_bloc.sql",
            {"pattern_clause": pattern_clause(bloc)},
        ).fetchall():
            keyword_amounts.setdefault(cid, {})[bloc["label"]] = float(amt or 0)

    cat_by_cand: dict[str, dict[str, float]] = {}
    all_cats: set[str] = set()
    for cid, cat, amt in run_sql(con, "queries/rank_interest_categories.sql").fetchall():
        cat_by_cand.setdefault(cid, {})[cat] = float(amt or 0)
        all_cats.add(cat)

    recs = []
    for r in rows:
        cid = r[0]
        name, office, st, dist, party = r[1], r[2], r[3], r[4], r[5]
        indiv = float(r[6] or 0)
        in_state = float(r[7] or 0)
        small = float(r[8] or 0)
        pac = float(r[9] or 0)
        ie_s = float(r[10] or 0)
        if indiv <= 0:
            continue
        raised = indiv + pac
        backing = raised + ie_s
        cats = cat_by_cand.get(cid, {})
        kws = keyword_amounts.get(cid, {})
        recs.append({
            "cid": cid,
            "name": name,
            "seat": f"{office}-{dist or '00'} {st}",
            "party": party,
            "indiv": indiv,
            "raised": raised,
            "backing": backing,
            "out-of-state donors": (indiv - in_state) / indiv,
            "donor concentration (low small-$)": 1 - small / indiv,
            "PAC/party-funded": (pac / raised) if raised else 0.0,
            "outside-funded (IE)": (ie_s / backing) if backing else 0.0,
            "blocs": {b["label"]: kws.get(b["label"], 0.0) / indiv for b in BLOCS},
            "cat_blocs": {c: (cats.get(c, 0.0) / backing if backing else 0.0)
                          for c in all_cats},
        })

    pf = {k: percentile_fn([x[k] for x in recs]) for k in (
        "out-of-state donors", "donor concentration (low small-$)",
        "PAC/party-funded", "outside-funded (IE)")}
    bloc_pf = {b["label"]: percentile_fn([x["blocs"][b["label"]] for x in recs])
               for b in BLOCS}
    cat_pf = {c: percentile_fn([x["cat_blocs"][c] for x in recs]) for c in all_cats}

    for x in recs:
        candidates = []
        if x["out-of-state donors"] >= FLOOR_OUT_OF_STATE:
            v = x["out-of-state donors"]
            candidates.append(("out-of-state donors", v, pf["out-of-state donors"](v)))
        if x["PAC/party-funded"] >= FLOOR_PAC:
            v = x["PAC/party-funded"]
            candidates.append(("PAC-funded", v, pf["PAC/party-funded"](v)))
        if x["outside-funded (IE)"] >= FLOOR_OUTSIDE:
            v = x["outside-funded (IE)"]
            candidates.append(("outside-funded (IE)", v, pf["outside-funded (IE)"](v)))
        if x["donor concentration (low small-$)"] >= FLOOR_LOW_SMALL:
            v = x["donor concentration (low small-$)"]
            candidates.append(("few small-$ donors", v,
                               pf["donor concentration (low small-$)"](v)))
        for label, share in x["blocs"].items():
            if share >= FLOOR_BLOC:
                candidates.append((f"donors: {label}", share, bloc_pf[label](share)))
        for cat, share in x["cat_blocs"].items():
            if share >= FLOOR_BLOC:
                candidates.append((cat, share, cat_pf[cat](share)))
        x["headline"] = max(candidates, key=lambda c: c[2]) if candidates else (
            "(balanced / no standout)", 0.0, 0.0)

    recs.sort(key=lambda x: x["headline"][2], reverse=True)
    if args.limit:
        recs = recs[:args.limit]

    scope = []
    if args.incumbents:
        scope.append("incumbents")
    if args.office:
        scope.append({"H": "House", "S": "Senate", "P": "President"}[args.office])
    if args.state:
        scope.append(args.state.upper())
    scope_s = ", ".join(scope) if scope else "all candidates"
    print("=" * 80)
    print(f"FUNDING HEADLINE RANKING — {scope_s}  (>= {money(args.min)} individual)")
    print(f"{len(recs)} members; backing = raised + IE support; "
          f"headline = most-extreme signal vs. cohort")
    print("=" * 80)
    print(f"{'#':>3}  {'candidate':<26}{'seat':<9}{'backing$':>11}  "
          f"headline  (value · cohort pctile)")
    print("-" * 80)
    for i, x in enumerate(recs, 1):
        label, val, p = x["headline"]
        vs = f"{100 * val:.0f}%" if val else "—"
        print(f"{i:>3}  {(x['name'] or '')[:25]:<26}{x['seat']:<9}"
              f"{money(x['backing']):>11}  {label}  ({vs} · p{int(round(p * 100))})")
    print()
    return 0


def gather_profile(con, cand_id: str, state: str) -> dict:
    """Run the canonical queries once and return raw rows + derived shares.

    Single source for both the printed profile and the infographic export, so
    the two presentations cannot drift. All formulas stay in the SQL layer; this
    only assembles results and divides the shares the docs already define.
    """
    totals = run_sql(con, "queries/candidate_totals.sql", cand_id=cand_id).fetchone()
    ind = float(totals[0] or 0)
    dpac_amt = float(totals[2] or 0)
    ie_s = float(totals[4] or 0)
    ie_o = float(totals[5] or 0)
    raised = float(totals[6] or 0)
    backing = float(totals[7] or 0)

    state_rows = run_sql(
        con, "queries/candidate_top_states.sql", cand_id=cand_id, limit=8).fetchall()
    instate = sum(float(amt or 0) for st, amt, _ in state_rows if st == state)
    # If home state is not in top 8, get the exact number.
    if not any(st == state for st, _, _ in state_rows):
        instate = float(con.execute(
            "SELECT coalesce(sum(amount), 0) FROM candidate_money "
            "WHERE cand_id = ? AND channel = 'individual' AND donor_state = ?",
            [cand_id, state],
        ).fetchone()[0])

    buckets = run_sql(
        con, "queries/candidate_buckets.sql",
        cand_id=cand_id, small_max=200, near_max=NEAR_MAX).fetchall()
    by_bucket = {str(b)[:1]: (b, int(n or 0), float(amt or 0))
                 for b, n, amt in buckets}
    small_amt = by_bucket.get("1", (None, 0, 0.0))[2]
    maxplus = by_bucket.get("4", (None, 0, 0.0))

    keyword_blocs = []
    for bloc in BLOCS:
        amt, n, oos = run_rendered_sql(
            con, "queries/candidate_keyword_bloc.sql",
            {"pattern_clause": pattern_clause(bloc)},
            cand_id=cand_id, home_state=state).fetchone()
        keyword_blocs.append({"label": bloc["label"], "amount": float(amt or 0),
                              "count": int(n or 0), "oos_amount": float(oos or 0)})

    cblocs = run_sql(
        con, "queries/candidate_interest_blocs.sql", cand_id=cand_id).fetchall()

    oos_rows = run_sql(
        con, "queries/candidate_oos_states.sql", cand_id=cand_id, home_state=state
    ).fetchall()
    oos_total = float(sum(a for _, a in oos_rows) or 0)
    hhi = (sum((float(a) / oos_total) ** 2 for _, a in oos_rows)
           if oos_total else 0.0)

    top_pacs = run_sql(
        con, "queries/candidate_top_direct_pac.sql", cand_id=cand_id, limit=12
    ).fetchall()
    top_ie = run_sql(
        con, "queries/candidate_top_ie_spenders.sql", cand_id=cand_id, limit=8
    ).fetchall()

    return {
        "ind": ind, "ind_count": int(totals[1] or 0),
        "dpac_amt": dpac_amt, "dpac_count": int(totals[3] or 0),
        "ie_s": ie_s, "ie_o": ie_o, "raised": raised, "backing": backing,
        "state_rows": state_rows, "instate": instate,
        "buckets": buckets,
        "small_amt": small_amt, "maxplus": maxplus,
        "keyword_blocs": keyword_blocs, "cblocs": cblocs,
        "oos_rows": oos_rows, "oos_total": oos_total, "hhi": hhi,
        "top_pacs": top_pacs, "top_ie": top_ie,
        # Shares as defined in docs/CALCULATIONS.md.
        "out_of_state_share": ((ind - instate) / ind) if ind else 0.0,
        "small_dollar_share": (small_amt / ind) if ind else 0.0,
        "low_small_share": (1 - small_amt / ind) if ind else 0.0,
        "maxplus_share": (maxplus[2] / ind) if ind else 0.0,
        "pac_share": (dpac_amt / raised) if raised else 0.0,
        "outside_share": (ie_s / backing) if backing else 0.0,
    }


def profile_mode(con, candidate: str) -> int:
    cand_id, name, office, state, district, party = resolve_candidate(con, candidate)
    n_cmtes = committee_count(con, cand_id)

    print("=" * 64)
    print(f"INFLUENCE PROFILE — {name}")
    print(f"{office}-{district}  {state}  ({party})   CAND_ID {cand_id}")
    print(f"{n_cmtes} attributable committee(s); home state = {state}")
    print("=" * 64)

    if not n_cmtes:
        print("\nNo attributable committees — no individual money to analyze.")
        return 0

    d = gather_profile(con, cand_id, state)
    ind, raised, backing = d["ind"], d["raised"], d["backing"]
    dpac_amt, ie_s, ie_o = d["dpac_amt"], d["ie_s"], d["ie_o"]

    header("1. MONEY RAISED  (what the candidate receives & controls)")
    print(f"  Individuals -> committees : {money(ind):>14}  "
          f"{pct(ind, raised)}  ({d['ind_count']:,} gifts)")
    print(f"  Direct PAC/party -> cand  : {money(dpac_amt):>14}  "
          f"{pct(dpac_amt, raised)}  ({d['dpac_count']:,} txns; excludes IE 24E/24A)")
    print(f"  {'RAISED TOTAL':<25} : {money(raised):>14}")
    if ie_s or ie_o:
        print(f"  (+ {money(ie_s)} outside IE support advancing them — see section 7)")

    header("2. GEOGRAPHIC ALIGNMENT  (constituent proxy: in-state donors)")
    instate = d["instate"]
    print(f"  In-state ({state})   : {money(instate):>14}  {pct(instate, ind)}")
    print(f"  Out-of-state    : {money(ind - instate):>14}  {pct(ind - instate, ind)}")
    print("  Top donor states:")
    for st, amt, n in d["state_rows"]:
        tag = " <- home" if st == state else ""
        print(f"    {str(st or '?'):<4} {money(amt):>13}  {pct(amt, ind)}  "
              f"({n:,}){tag}")

    header("3. GRASSROOTS vs CONCENTRATED  (check-size distribution)")
    for bucket, n, amount in d["buckets"]:
        print(f"  {bucket:<22} {money(amount):>13}  {pct(amount, ind)}  ({n:,} gifts)")

    header("4. INTEREST BLOCS  (identifiable constituencies; fuzzy match)")
    print(f"  {'bloc':<26} {'amount':>12} {'share':>7} {'out-of-state':>13}")
    any_bloc = False
    for b in d["keyword_blocs"]:
        if b["amount"] > 0:
            any_bloc = True
            print(f"  {b['label']:<26} {money(b['amount']):>12} "
                  f"{pct(b['amount'], ind):>7} "
                  f"{pct(b['oos_amount'], b['amount']):>12} ({b['count']:,})")
    if not any_bloc:
        print("  (no configured bloc matched)")
    print("  NB: name/employer keyword match — estimates, not audited.")

    if d["cblocs"]:
        header("4b. COMMITTEE INTEREST BLOCS  (curated tags; direct PAC + IE support)")
        print(f"  denominator = total backing {money(backing)} "
              f"(raised + IE support)")
        print(f"  {'category':<28} {'direct PAC':>12} {'IE support':>12} "
              f"{'total':>11} {'share':>7}")
        for cat, p, i in d["cblocs"]:
            total = float(p or 0) + float(i or 0)
            print(f"  {cat[:28]:<28} {money(p):>12} {money(i):>12} "
                  f"{money(total):>11} {pct(total, backing):>7}")

    header("5. OUT-OF-STATE CONCENTRATION  (diffuse vs dominated)")
    oos_rows, oos_total = d["oos_rows"], d["oos_total"]
    if oos_total:
        hhi = d["hhi"]
        top = oos_rows[0]
        print(f"  Out-of-state pool : {money(oos_total)} across {len(oos_rows)} states")
        print(f"  Largest single   : {top[0]} {money(top[1])} "
              f"({pct(top[1], oos_total)} of out-of-state)")
        print(f"  Concentration HHI: {hhi:.3f}  "
              f"({'concentrated' if hhi > 0.25 else 'moderate' if hhi > 0.15 else 'diffuse'})")
    else:
        print("  (no out-of-state individual money)")

    header("6. PAC / PARTY MIX  (top DIRECT givers; excludes IE)")
    if dpac_amt:
        for nm, tp, amt, _ in d["top_pacs"]:
            print(f"  {money(amt):>9} [{tp or '?'}] {(nm or '')[:52]}")
    else:
        print("  (no direct PAC/party money)")

    header("7. OUTSIDE SPENDING  (independent expenditures — uncapped, NOT controlled)")
    if ie_s or ie_o:
        print(f"  Supporting them : {money(ie_s):>14}  "
              f"{pct(ie_s, backing)} of total backing")
        print(f"  Opposing them   : {money(ie_o):>14}  "
              f"(works for their opponent; not in backing)")
        print(f"  {'TOTAL BACKING':<15} : {money(backing):>14}  "
              f"(raised {pct(raised, backing)} · IE support {pct(ie_s, backing)})")
        if d["top_ie"]:
            print("  Top outside spenders:")
            for nm, so, amount in d["top_ie"]:
                print(f"    [{so}] {money(float(amount)):>12}  {(nm or '')[:48]}")
    else:
        print("  (no independent expenditures for or against this candidate)")

    print()
    return 0


OFFICE_LABEL = {"H": "U.S. House", "S": "U.S. Senate", "P": "President"}
ICI_LABEL = {"I": "Incumbent", "C": "Challenger", "O": "Open seat"}
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "Washington, D.C.", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico", "VI": "U.S. Virgin Islands", "GU": "Guam",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}


def format_name(fec_name: str) -> str:
    """'LEGER FERNANDEZ, TERESA' -> 'Teresa Leger Fernandez' for display.

    Best-effort title-casing of the raw FEC name; the raw string is kept as
    candidate.name_fec for traceability. Accents the FEC omits are not restored.
    """
    if not fec_name:
        return fec_name
    if "," in fec_name:
        last, first = (p.strip() for p in fec_name.split(",", 1))
        s = f"{first} {last}".strip()
    else:
        s = fec_name
    return " ".join(w.capitalize() if w.isupper() else w for w in s.split())


OFFICE_SLUG = {"H": "house", "S": "senate", "P": "president"}


def canonical_dirname(office: str, state: str, district: str, fec_name: str) -> str:
    """Standard artifact folder name: <st>-<chamber>[-<dd>]-<last-first>.

    e.g. nm-senate-lujan-ben-ray, nm-house-03-leger-fernandez-teresa. The FEC
    name is already 'LAST, FIRST', so slugging it yields last-first ordering.
    """
    name_slug = re.sub(r"[^a-z0-9]+", "-", fec_name.lower()).strip("-")
    parts = [state.lower(), OFFICE_SLUG.get(office, office.lower())]
    if office == "H":
        parts.append(str(district).zfill(2))
    parts.append(name_slug)
    return "-".join(parts)


BUCKET_LABEL = {
    "1": "Small (≤$200)", "2": "Mid ($201–$999)",
    "3": "Large ($1k–$3.4k)", "4": "Maxed out (≥$3,500)",
    "5": "Refunds/negative",
}


def _p1(x: float) -> str:
    return f"{100.0 * x:.1f}%"


def _r4(x: float) -> float:
    return round(float(x), 4)


def build_angles(cand: dict, d: dict) -> tuple[list[dict], list[tuple[str, str]]]:
    """Assemble infographic angles from a gathered profile.

    Returns (emitted_angles, skipped) where each emitted angle is a self-contained,
    chart-ready dict and `skipped` records (id, why) for transparency.

    Inclusion rule: STRUCTURAL angles (composition, donor-size, geography,
    pac-roster) are emitted whenever their channel clears its viability floor
    (MIN_*) — even below their headline floor, because the inverse ('rooted at
    home', 'no air war') is itself informative. SIGNAL angles (interest blocs, IE
    air war, donor keyword blocs) are emitted only when they also clear a
    materiality share floor, so we never ship a chart of noise. A candidate below
    every viability floor yields no angles at all (not viable for a graphic).
    """
    ind, raised, backing = d["ind"], d["raised"], d["backing"]
    dpac, ie_s, ie_o = d["dpac_amt"], d["ie_s"], d["ie_o"]
    name, state = cand["name"], cand["state"]
    emitted: list[dict] = []
    skipped: list[tuple[str, str]] = []

    def angle(num, aid, title, reason, metric, value, threshold, featured,
              headline, subhead, chart, footnotes, source):
        emitted.append({
            "_n": num,
            "candidate": cand,
            "angle": {"id": aid, "title": title, "reason": reason, "metric": metric,
                      "metric_value": _r4(value), "threshold": threshold,
                      "featured": featured},
            "headline": headline, "subhead": subhead, "chart": chart,
            "footnotes": footnotes, "source": source,
        })

    # 01 — donor size (structural). Distribution of POSITIVE itemized gifts by
    # size. The bucket count is contribution rows (gifts), not unique donors — FEC
    # bulk data has no donor identity — so everything is labeled "gifts". The
    # negative refund and zero-dollar rows are excluded and the denominator is the
    # positive-gift universe, so the displayed shares reconcile to 100%.
    by_pre = {str(b)[:1]: (int(n), float(amt)) for b, n, amt in d["buckets"]}
    pos = [(b, int(n), float(amt)) for b, n, amt in d["buckets"]
           if str(b)[:1] != "5" and float(amt) > 0]
    pos_amt = sum(a for _, _, a in pos)
    pos_n = sum(n for _, n, _ in pos)
    refund_n, refund_amt = by_pre.get("5", (0, 0.0))
    if pos_amt >= MIN_INDIVIDUAL and pos_n >= MIN_DONORS:
        small_amt = by_pre.get("1", (0, 0.0))[1]
        mp_amt = by_pre.get("4", (0, 0.0))[1]
        ss, mp_share = small_amt / pos_amt, mp_amt / pos_amt
        featured = (1 - ss) >= FLOOR_LOW_SMALL
        rows = [{"bucket": BUCKET_LABEL.get(str(b)[:1], str(b)), "amount": amt,
                 "count": n, "share_of_individual": _r4(amt / pos_amt)}
                for b, n, amt in pos]
        reason = (
            f"The largest gifts (≥${NEAR_MAX:,}) account for {money(mp_amt)} "
            f"({_p1(mp_share)}) of itemized money, while small-dollar gifts (≤$200) "
            f"are only {_p1(ss)} — clears the concentration flag (small-dollar under "
            f"{_p1(1 - FLOOR_LOW_SMALL)})."
            if featured else
            f"Small-dollar gifts (≤$200) are {_p1(ss)} of itemized money; below the "
            f"concentration flag, shown as the gift-size distribution.")
        notes = [
            "Counts are itemized contributions (gifts), not unique donors — FEC bulk "
            "data has no donor identity. Gifts under $200 are not itemized, so the "
            "true small-dollar share is higher than shown.",
            "Shares are of positive itemized gifts; PAC money is excluded."]
        if refund_amt:
            notes.append(f"Excludes {money(refund_amt)} in refunds/negative "
                         f"adjustments ({refund_n} rows), not part of the gift universe.")
        angle(
            "01", "donor-size", "Big checks vs. grassroots", reason,
            # small_gift_share: small-gift dollars / positive itemized-gift dollars
            # (distinct from the ranker's net-based small_dollar_share — see
            # docs/CALCULATIONS.md), so the chart reconciles to 100%.
            "small_gift_share", ss,
            f"Flagged as concentrated when the small-gift share (≤$200) is < "
            f"{_p1(1 - FLOOR_LOW_SMALL)}; this candidate is at {_p1(ss)}.",
            featured,
            (f"{_p1(ss)} small-dollar, {_p1(mp_share)} from the largest gifts"
             if featured else "Where the itemized money came from, by gift size"),
            f"How {name}'s {money(pos_amt)} in itemized gifts breaks down by gift size",
            {"type": "bar", "unit": "USD",
             "denominator": {"label": "Total itemized gifts", "amount": pos_amt,
                             "count": pos_n},
             "rows": rows},
            notes,
            "FEC 2026 cycle; sql/queries/candidate_buckets.sql via candidate_money view")
    elif pos_amt > 0:
        skipped.append(("donor-size", f"itemized gifts {money(pos_amt)} / {pos_n} "
                        f"below viability floor "
                        f"({money(MIN_INDIVIDUAL)} / {MIN_DONORS})"))

    # 02 — geography (structural)
    if ind >= MIN_INDIVIDUAL:
        oos = d["out_of_state_share"]
        featured = oos >= FLOOR_OUT_OF_STATE
        rows, top_amt, top_n = [], 0.0, 0
        for st, amt, n in d["state_rows"]:
            amt, n = float(amt or 0), int(n or 0)
            top_amt += amt
            top_n += n
            sname = STATE_NAMES.get(st, st or "?")
            rows.append({"state": st,
                         "label": f"{sname} (home)" if st == state else sname,
                         "amount": amt, "count": n,
                         "share_of_individual": _r4(amt / ind), "in_state": st == state})
        rem = ind - top_amt
        if rem > 0.5:
            rows.append({"state": None, "label": "Other states", "amount": float(rem),
                         "count": d["ind_count"] - top_n,
                         "share_of_individual": _r4(rem / ind), "in_state": False})
        reason = (
            f"{_p1(oos)} of individual money comes from outside {state} — clears the "
            f"{_p1(FLOOR_OUT_OF_STATE)} out-of-state flag."
            if featured else
            f"{_p1(1 - oos)} of individual money is from {state}; out-of-state is "
            f"{_p1(oos)}, below the {_p1(FLOOR_OUT_OF_STATE)} flag — a locally-rooted base.")
        angle(
            "02", "geography", "In-state vs. out-of-state", reason,
            "out_of_state_share", oos,
            f"Flagged as out-of-state funded when share ≥ {_p1(FLOOR_OUT_OF_STATE)}; "
            f"this candidate is at {_p1(oos)}.",
            featured,
            (f"{_p1(oos)} out-of-state money" if featured
             else f"{_p1(1 - oos)} {STATE_NAMES.get(state, state)} money"),
            f"Where {name}'s {money(ind)} in individual donations came from, by donor state",
            {"type": "bar", "unit": "USD",
             "denominator": {"label": "Total individual donations",
                             "amount": float(ind), "count": d["ind_count"]},
             "rows": rows,
             "summary": {"in_state_amount": float(d["instate"]),
                         "in_state_share": _r4(1 - oos),
                         "out_of_state_amount": float(ind - d["instate"]),
                         "out_of_state_share": _r4(oos)}},
            ["Geography is available for individual donors only; PAC/IE money has no "
             "donor state and is excluded.",
             "Donor state is a proxy for locality — FEC gives state, not district."],
            "FEC 2026 cycle; sql/queries/candidate_top_states.sql via candidate_money view")
    elif ind > 0:
        skipped.append(("geography", f"individual {money(ind)} below "
                        f"{money(MIN_INDIVIDUAL)} floor"))

    # 03 — composition (structural)
    if backing >= MIN_BACKING:
        out_share = d["outside_share"]
        featured = out_share >= FLOOR_OUTSIDE
        rows = [
            {"channel": "Individual donations", "amount": float(ind),
             "count": d["ind_count"], "share_of_backing": _r4(ind / backing) if backing else 0.0,
             "controlled_by_candidate": True},
            {"channel": "Direct PAC contributions", "amount": float(dpac),
             "count": d["dpac_count"], "share_of_backing": _r4(dpac / backing) if backing else 0.0,
             "controlled_by_candidate": True},
            {"channel": "Independent expenditures supporting", "amount": float(ie_s),
             "share_of_backing": _r4(ie_s / backing) if backing else 0.0,
             "controlled_by_candidate": False},
            {"channel": "Independent expenditures opposing", "amount": float(ie_o),
             "share_of_backing": None, "controlled_by_candidate": False,
             "note": "Works for the opponent; excluded from total backing."},
        ]
        no_ie = not (ie_s or ie_o)
        reason = (
            f"Independent expenditures are {_p1(out_share)} of total backing "
            f"({money(ie_s)}) — uncapped outside money the candidate does not control, "
            f"clearing the {_p1(FLOOR_OUTSIDE)} outside-money flag."
            if featured else
            f"Raised {money(raised)} that the campaign fully controls, with $0 "
            f"independent-expenditure money for or against — no outside air war. PACs "
            f"are {_p1(d['pac_share'])} of money raised."
            if no_ie else
            f"Raised {money(raised)} the campaign controls; outside IE is {_p1(out_share)} "
            f"of total backing, below the {_p1(FLOOR_OUTSIDE)} flag.")
        angle(
            "03", "composition", "Money raised vs. outside spending", reason,
            "outside_share", out_share,
            f"Flagged as outside-funded when IE/total_backing ≥ {_p1(FLOOR_OUTSIDE)}; "
            f"this candidate is at {_p1(out_share)}.",
            featured,
            ("100% money the campaign controls, $0 super-PAC" if no_ie
             else f"{_p1(out_share)} from outside super-PAC spending"),
            f"Every dollar working for or against {name}, by channel",
            {"type": "stacked_bar", "unit": "USD",
             "denominator": {"label": "Total backing (money working to elect)",
                             "amount": float(backing)},
             "rows": rows,
             "summary": {"raised": float(raised), "total_backing": float(backing),
                         "pac_share_of_raised": _r4(d["pac_share"]),
                         "outside_share_of_backing": _r4(out_share)}},
            ["Raised = individual + direct PAC (money the candidate receives and controls).",
             "Total backing = raised + IE support; IE opposing is excluded (it aids the opponent).",
             "Direct PAC excludes IE/coordinated types (24E/24A/24C/24N) to avoid double-counting."],
            "FEC 2026 cycle; sql/queries/candidate_totals.sql via candidate_money view")
    elif raised > 0 or ie_s or ie_o:
        skipped.append(("composition", f"total backing {money(backing)} below "
                        f"{money(MIN_BACKING)} floor"))

    # 04 — PAC roster (structural)
    if dpac >= MIN_PAC:
        ps = d["pac_share"]
        featured = ps >= FLOOR_PAC
        rows, top_amt, top_n = [], 0.0, 0
        for nm, tp, amt, n in d["top_pacs"]:
            amt, n = float(amt or 0), int(n or 0)
            top_amt += amt
            top_n += n
            rows.append({"pac": nm, "cmte_tp": tp, "amount": amt, "count": n})
        rem = dpac - top_amt
        if rem > 0.5:
            rows.append({"pac": "All other PACs", "cmte_tp": None, "amount": float(rem),
                         "count": d["dpac_count"] - top_n})
        reason = (
            f"PAC money is {_p1(ps)} of what the campaign raised ({money(dpac)}) — "
            f"clears the {_p1(FLOOR_PAC)} PAC-dependence flag."
            if featured else
            f"PAC money is {_p1(ps)} of money raised ({money(dpac)}, {d['dpac_count']} "
            f"contributions), below the {_p1(FLOOR_PAC)} dependence flag — shown as a "
            f"roster of who gave, not a dependence headline.")
        angle(
            "04", "pac-roster", "Who the PACs are", reason,
            "pac_share", ps,
            f"Flagged as PAC-dependent when PAC/raised ≥ {_p1(FLOOR_PAC)}; "
            f"this candidate is at {_p1(ps)}.",
            featured,
            f"Top PACs: {money(dpac)} from {d['dpac_count']} contributions",
            f"Top PAC contributors to {name} ({money(dpac)} total direct PAC money)",
            {"type": "bar", "unit": "USD",
             "denominator": {"label": "Total direct PAC contributions",
                             "amount": float(dpac), "count": d["dpac_count"]},
             "rows": rows},
            ["Direct PAC contributions only (committee→candidate); excludes IE/coordinated "
             "types (24E/24A/24C/24N).",
             "cmte_tp is the FEC committee type (Q = qualified non-party PAC)."],
            "FEC 2026 cycle; sql/queries/candidate_top_direct_pac.sql")
    elif dpac > 0:
        skipped.append(("pac-roster", f"direct PAC {money(dpac)} below "
                        f"{money(MIN_PAC)} floor"))
    elif raised > 0:
        skipped.append(("pac-roster", "no direct PAC money"))

    # 05 — committee interest blocs (signal: must clear FLOOR_BLOC)
    cbloc_rows = [(cat, float(p or 0), float(i or 0)) for cat, p, i in d["cblocs"]]
    cbloc_max = max(((p + i) / backing for _, p, i in cbloc_rows), default=0.0) if backing else 0.0
    if cbloc_rows and backing >= MIN_BACKING and cbloc_max >= FLOOR_BLOC:
        rows = [{"category": cat, "direct_pac_amount": p, "ie_support_amount": i,
                 "total": p + i, "share_of_backing": _r4((p + i) / backing)}
                for cat, p, i in cbloc_rows]
        top = max(cbloc_rows, key=lambda r: r[1] + r[2])
        angle(
            "05", "interest-blocs", "Interest blocs behind the candidacy",
            f"{top[0]} is {_p1(cbloc_max)} of total backing — clears the "
            f"{_p1(FLOOR_BLOC)} interest-bloc floor.",
            "interest_share", cbloc_max,
            f"Emitted only when a curated bloc reaches ≥ {_p1(FLOOR_BLOC)} of total backing.",
            True,
            f"{top[0]}: {_p1(cbloc_max)} of total backing",
            f"Curated interest categories backing {name} (direct PAC + IE support)",
            {"type": "bar", "unit": "USD",
             "denominator": {"label": "Total backing", "amount": float(backing)},
             "rows": rows},
            ["Curated committee tags from dim_group_mappings; stances are separate "
             "categories and are never summed together."],
            "FEC 2026 cycle; sql/queries/candidate_interest_blocs.sql")
    elif cbloc_rows and backing >= MIN_BACKING:
        skipped.append(("interest-blocs", f"largest bloc {_p1(cbloc_max)} < {_p1(FLOOR_BLOC)} floor"))

    # 06 — IE air war (signal: material outside spending)
    ie_material = (d["outside_share"] >= FLOOR_OUTSIDE) or (backing and ie_o > 0.05 * backing)
    if ie_material and d["top_ie"] and backing >= MIN_BACKING:
        rows = [{"spender": nm, "support_oppose": so, "amount": float(amt)}
                for nm, so, amt in d["top_ie"]]
        angle(
            "06", "ie-air-war", "The outside air war",
            f"Outside independent expenditures are material here (support {_p1(d['outside_share'])} "
            f"of backing; {money(ie_o)} spent opposing) — uncapped money the campaign does "
            f"not control.",
            "outside_share", d["outside_share"],
            f"Emitted when IE support ≥ {_p1(FLOOR_OUTSIDE)} of backing or opposing IE is material.",
            d["outside_share"] >= FLOOR_OUTSIDE,
            f"{money(ie_s + ie_o)} in outside spending",
            f"Top independent-expenditure spenders for and against {name}",
            {"type": "bar", "unit": "USD",
             "denominator": {"label": "IE support + oppose", "amount": float(ie_s + ie_o)},
             "rows": rows,
             "summary": {"ie_support_amount": float(ie_s), "ie_oppose_amount": float(ie_o)}},
            ["Independent expenditures are uncapped and not controlled by the candidate.",
             "Opposing IE works for the opponent and is excluded from total backing."],
            "FEC 2026 cycle; sql/queries/candidate_top_ie_spenders.sql")
    elif (ie_s or ie_o) and backing >= MIN_BACKING:
        skipped.append(("ie-air-war", "IE present but below materiality; shown in composition"))

    # 07 — donor keyword blocs (signal, fuzzy: must clear FLOOR_BLOC)
    kw = ([b for b in d["keyword_blocs"] if b["amount"] / ind >= FLOOR_BLOC]
          if ind >= MIN_INDIVIDUAL else [])
    if kw:
        rows = [{"bloc": b["label"], "amount": b["amount"], "count": b["count"],
                 "share_of_individual": _r4(b["amount"] / ind),
                 "out_of_state_share_within_bloc": _r4(b["oos_amount"] / b["amount"])
                 if b["amount"] else 0.0} for b in kw]
        top = max(kw, key=lambda b: b["amount"])
        angle(
            "07", "donor-blocs", "Donor blocs (by name/employer)",
            f"{top['label']} donors are {_p1(top['amount'] / ind)} of individual money — "
            f"clears the {_p1(FLOOR_BLOC)} floor. Fuzzy keyword estimate, not audited.",
            "donor_bloc_share", top["amount"] / ind,
            f"Emitted only when a keyword bloc reaches ≥ {_p1(FLOOR_BLOC)} of individual money.",
            True,
            f"{top['label']}: {_p1(top['amount'] / ind)} of individual money",
            f"Identifiable donor blocs in {name}'s individual money (fuzzy name/employer match)",
            {"type": "bar", "unit": "USD",
             "denominator": {"label": "Total individual donations", "amount": float(ind),
                             "count": d["ind_count"]},
             "rows": rows},
            ["Fuzzy name/employer keyword match — estimates, not audited.",
             "Shares are of individual money only."],
            "FEC 2026 cycle; sql/queries/candidate_keyword_bloc.sql")

    emitted.sort(key=lambda a: a["_n"])
    return emitted, skipped


def export_infographic_mode(con, candidate: str, out_dir: Path | None,
                            region_note: str | None) -> int:
    cand_id, name, office, state, district, party = resolve_candidate(con, candidate)
    if not committee_count(con, cand_id):
        sys.exit(f"{name} ({cand_id}) has no attributable committees — nothing to export.")
    # Default to the canonical artifact folder under infographics/.
    if out_dir is None:
        out_dir = ROOT / "infographics" / canonical_dirname(office, state, district, name)
    ici, elect_yr = con.execute(
        "SELECT CAND_ICI, CAND_ELECTION_YR FROM dim_candidates WHERE CAND_ID = ?",
        [cand_id]).fetchone()
    office_label = OFFICE_LABEL.get(office, office)
    if region_note is None:
        region_note = (f"{office_label} {state}-{district}" if office == "H"
                       else f"{office_label}, {state}")
    cand = {
        "cand_id": cand_id, "name": format_name(name), "name_fec": name, "party": party,
        "office": office_label, "state": state, "district": district,
        "incumbency": ICI_LABEL.get(ici, ici), "election_year": int(elect_yr or 0),
        "region_note": region_note,
    }
    d = gather_profile(con, cand_id, state)
    angles, skipped = build_angles(cand, d)

    if not angles:
        print(f"{name} ({cand_id}) is not viable for an infographic — every angle is "
              f"below its volume floor; nothing written.")
        for aid, why in skipped:
            print(f"  · skipped  {aid:<16} ({why})", file=sys.stderr)
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Exporting infographic angles for {name} ({cand_id}) -> {out_dir}")
    for a in angles:
        a.pop("_n")
        path = out_dir / f"{a['angle']['id']}.json"
        path.write_text(json.dumps(a, indent=2, ensure_ascii=False) + "\n")
        flag = "★ featured" if a["angle"]["featured"] else "  context"
        print(f"  {flag}  {path.name}")
    for aid, why in skipped:
        print(f"  · skipped  {aid:<16} ({why})", file=sys.stderr)
    print(f"{len(angles)} angle file(s) written.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Funding-influence profile for one candidate, or --rank a cohort.")
    ap.add_argument("candidate", nargs="?",
                    help="candidate name (ILIKE) or CAND_ID (omit with --rank)")
    ap.add_argument("--db", default=str(ROOT / "fec_campaign_finance.db"))
    ap.add_argument("--rank", action="store_true",
                    help="rank a cohort and flag each member's most-extreme signal")
    ap.add_argument("--office", choices=["H", "S", "P"],
                    help="restrict cohort to one office")
    ap.add_argument("--state", help="restrict cohort to one home state (e.g. NM)")
    ap.add_argument("--incumbents", action="store_true",
                    help="restrict cohort to incumbents (CAND_ICI='I')")
    ap.add_argument("--min", type=float, default=50000,
                    help="min individual $ to include in cohort (default 50k)")
    ap.add_argument("--limit", type=int, default=0,
                    help="show only the top N most-extreme members (0 = all)")
    ap.add_argument("--export-infographic", metavar="DIR", nargs="?",
                    const="__AUTO__", default=None,
                    help="write one chart-ready JSON per relevant angle. With no DIR, "
                         "uses the canonical infographics/<st>-<chamber>[-<dd>]-<name>/")
    ap.add_argument("--region-note",
                    help="locality string for the candidate header in exports "
                         "(e.g. 'Represents Taos, NM (NM-03)')")
    args = ap.parse_args(argv)

    if args.rank and args.export_infographic:
        ap.error("--rank and --export-infographic are mutually exclusive")

    if not Path(args.db).exists():
        sys.exit(f"DB not found: {args.db}")
    import duckdb

    con = duckdb.connect(args.db, read_only=True)
    install_temp_views(con)

    if args.rank:
        return rank_mode(con, args)
    if not args.candidate:
        sys.exit("Pass a candidate name/CAND_ID, or use --rank for cohort mode.")
    if args.export_infographic:
        out_dir = None if args.export_infographic == "__AUTO__" else Path(args.export_infographic)
        return export_infographic_mode(con, args.candidate, out_dir, args.region_note)
    return profile_mode(con, args.candidate)


if __name__ == "__main__":
    raise SystemExit(main())
