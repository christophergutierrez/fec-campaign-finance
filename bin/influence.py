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
import sys
from pathlib import Path

from infographic_export import NEAR_MAX, write_infographic_export
from sqlutil import install_temp_views, run_sql

ROOT = Path(__file__).resolve().parent.parent

FLOOR_OUT_OF_STATE = 0.40
FLOOR_PAC = 0.30
FLOOR_OUTSIDE = 0.30
FLOOR_LOW_SMALL = 0.90
FLOOR_BLOC = 0.08


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
            "cat_blocs": {c: (cats.get(c, 0.0) / backing if backing else 0.0)
                          for c in all_cats},
        })

    pf = {k: percentile_fn([x[k] for x in recs]) for k in (
        "out-of-state donors", "donor concentration (low small-$)",
        "PAC/party-funded", "outside-funded (IE)")}
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
        "cblocs": cblocs,
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

    if d["cblocs"]:
        header("4. COMMITTEE INTEREST BLOCS  (curated tags; direct PAC + IE support)")
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
        return write_infographic_export(
            con,
            args.candidate,
            out_dir,
            args.region_note,
            root=ROOT,
            resolve_candidate=resolve_candidate,
            committee_count=committee_count,
            gather_profile=gather_profile,
        )
    return profile_mode(con, args.candidate)


if __name__ == "__main__":
    raise SystemExit(main())
