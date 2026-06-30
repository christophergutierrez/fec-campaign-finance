"""Infographic export artifact naming and angle assembly."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

NEAR_MAX = 3500

FLOOR_OUT_OF_STATE = 0.40
FLOOR_LOW_SMALL = 0.90

# Viability floors for the infographic export: below these, an angle has too
# little money/data behind it to be worth a graphic. Applied per channel, so a
# candidate thin on one channel but heavy on another still gets its real angles;
# a candidate clearing none of them produces no files at all.
MIN_INDIVIDUAL = 25_000   # donor-size, geography (individual $)
MIN_DONORS = 50           # donor-size needs enough positive gifts for a waffle

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

OFFICE_SLUG = {"H": "house", "S": "senate", "P": "president"}

BUCKET_LABEL = {
    "1": "Small (≤$200)", "2": "Mid ($201–$999)",
    "3": "Large ($1k–$3.4k)", "4": "Maxed out (≥$3,500)",
    "5": "Refunds/negative",
}


def money(x) -> str:
    return f"${float(x):,.0f}" if x is not None else "$0"


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


def canonical_dirname(office: str, state: str, district: str, fec_name: str) -> str:
    """Standard artifact folder name: <st>-<chamber>[-<dd>]-<last-first>."""
    name_slug = re.sub(r"[^a-z0-9]+", "-", fec_name.lower()).strip("-")
    parts = [state.lower(), OFFICE_SLUG.get(office, office.lower())]
    if office == "H":
        parts.append(str(district).zfill(2))
    parts.append(name_slug)
    return "-".join(parts)


def _p1(x: float) -> str:
    return f"{100.0 * x:.1f}%"


def _r4(x: float) -> float:
    return round(float(x), 4)


def build_angles(cand: dict, d: dict) -> tuple[list[dict], list[tuple[str, str]]]:
    """Assemble infographic angles from a gathered profile.

    Returns (emitted_angles, skipped) where each emitted angle is a self-contained,
    chart-ready dict and `skipped` records (id, why) for transparency.

    Only angles with SVG renderers are exported. Each emitted angle is structural:
    it appears whenever its channel clears the viability floor, even below the
    headline floor, because the inverse story is still useful context.
    """
    ind = d["ind"]
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

    emitted.sort(key=lambda a: a["_n"])
    return emitted, skipped


def write_infographic_export(
    con,
    candidate: str,
    out_dir: Path | None,
    region_note: str | None,
    *,
    root: Path,
    resolve_candidate,
    committee_count,
    gather_profile,
) -> int:
    cand_id, name, office, state, district, party = resolve_candidate(con, candidate)
    if not committee_count(con, cand_id):
        sys.exit(f"{name} ({cand_id}) has no attributable committees — nothing to export.")
    # Default to the canonical artifact folder under infographics/.
    if out_dir is None:
        out_dir = root / "infographics" / canonical_dirname(office, state, district, name)
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
