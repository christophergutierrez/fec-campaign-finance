# CALCULATIONS.md — how every funding metric is defined

Plain-English guide for every calculation in this project. The executable
definitions live in `sql/views/` and `sql/queries/`; this doc explains them.

| Layer | File | Purpose |
|---|---|---|
| **Executable SQL** | `sql/views/`, `sql/queries/` | canonical definitions |
| **Explanatory** | `docs/CALCULATIONS.md` | humans; the "what & why" |
| **Semantic consumer** | `model/fec.malloy` | Malloy-facing model |
| **Runtime** | `bin/influence.py` | CLI orchestration and display |

Do not reimplement formulas in Python or prose. If a formula changes, update the
SQL first, then update this explanation and any semantic consumers.

All amounts are FEC 2026-cycle dollars. See `DATA_DICTIONARY.md` for the underlying
tables and the raw gotchas these formulas encode.

---

## The four money channels (the atoms)

Every dollar flowing to/about a candidate is exactly one of these. In
`sql/views/candidate_money.sql` they are the `channel` values of the unified
`candidate_money` source.

| Channel | Malloy measure | Definition | Source filter |
|---|---|---|---|
| Individual | `individual_amount` | Itemized individual donations into principal/authorized candidate committees | `clean_individual_contributions` joined through `candidate_committees` |
| Direct PAC | `direct_pac_amount` | Committee→candidate **contributions** | `fact_contributions` where `TRANSACTION_TP NOT IN ('24E','24A','24C','24N')` — the IE/coordinated types are excluded because they overlap the IE table |
| IE support | `ie_support_amount` | Independent expenditures **supporting** the candidate | `fact_independent_expenditures` where `AMNDT_IND='N'`, `SUPPORT_OPPOSE='S'`, real spender (joins `dim_committees`) |
| IE oppose | `ie_oppose_amount` | Independent expenditures **opposing** the candidate | same as above with `SUPPORT_OPPOSE='O'` |

**Key distinction:** Individual + Direct PAC is money the candidate *receives and
controls*. IE is money spent *about* them that they never touch (it goes to a vendor).
IE oppose is tied to the candidate but works *for their opponent* — never add it to
anything positive.

---

## Derived totals

> **Raised vs. total backing is this project's "gross vs. net".** Both are correct;
> they answer different questions. Pick deliberately and label which one you mean —
> the common error is quoting backing as if it were money raised.

### Raised — `raised`
Money the candidate **receives and controls**.
```
raised = individual_amount + direct_pac_amount
```
**Use it for:** the size of the campaign's own war chest; fundraising strength;
anything about money the candidate decides how to spend; comparisons to FEC
contribution limits.
**Don't use it for:** the total resources working to elect someone (it omits the
uncapped super-PAC air war, which is often larger than the campaign itself).

### Total backing — `total_backing`  ⭐ tracked as a first-class metric
All money **working to elect** the candidate, whether they control it or not. The
denominator for the "who is behind this candidacy / who owns this rep" question.
```
total_backing = raised + ie_support_amount
              = individual_amount + direct_pac_amount + ie_support_amount
```
Excludes `ie_oppose_amount` (that works for the opponent).
**Use it for:** the ownership/influence question; the denominator of `outside_share`
and `interest_share`; the headline "this candidacy is X% funded by Y"; comparing the
real firepower behind two candidates.
**Don't use it for:** "how much did they raise" (overstates it — most of backing can
be money they never touched); per-donor or per-limit analysis (IE has no donor/limit).

---

## The two-denominator principle

Different questions require different denominators. Using the wrong one silently
corrupts every percentage. There are exactly two:

- **Composition of money RAISED** — denominator is the candidate's own money.
  Use `raised` (or `individual_amount` for donor-attribute signals, since IE/PAC have
  no donor state or check size).
- **Who is BEHIND the candidacy** — denominator is `total_backing` (includes IE).

IE is **never** folded into the raised-money denominators (it's uncapped and would
swamp them) and **never** mixed support-with-oppose.

---

## Which metric answers which question (decision guide)

| If the question is… | Use | Denominator |
|---|---|---|
| "How much did they raise?" / war-chest size | `raised` | — |
| "How much money is working to elect them?" | `total_backing` | — |
| "How dependent are they on PACs?" | `pac_share` | `raised` |
| "How much is uncapped outside (super-PAC) money?" | `outside_share` | `total_backing` |
| "Are their donors local or out-of-state?" | `out_of_state_share` | `individual_amount` |
| "Is this grassroots or big-check funded?" | `small_dollar_share` (or its inverse) | `individual_amount` |
| "How much of their support is interest/industry X?" | `interest_share` | `total_backing` |
| "Is their out-of-state money diffuse or dominated?" | out-of-state HHI | out-of-state pool |

Rules of thumb:
- **Ownership / "who's behind them" → `total_backing`** (it counts the IE air war).
- **Their own choices / fundraising → `raised`** (only money they control).
- **Donor attributes (geography, check size) → `individual_amount`** (PAC and IE have
  no donor state or check size, so they can't be in the denominator).

---

## Influence signals

Each signal lists its formula, denominator, and Malloy measure. Materiality floors
(used by `influence.py --rank` to decide if a signal is "headline-worthy") are noted.

### Out-of-state share — `out_of_state_share`
Fraction of **individual** money from outside the candidate's home state (constituent
proxy; we have donor state, not donor district).
```
out_of_state_share = 1 - (in_state_amount / individual_amount)
in_state_amount    = individual donations where donor_state = candidate home_state
```
Denominator: `individual_amount`. Headline floor: 0.40.

### PAC-funded share — `pac_share`
```
pac_share = direct_pac_amount / raised
```
Denominator: `raised`. Headline floor: 0.30.

### Outside-funded share — `outside_share`
How much of everything backing the candidate is uncapped outside IE support.
```
outside_share = ie_support_amount / total_backing
```
Denominator: `total_backing`. Headline floor: 0.30.

### Small-dollar share — `small_dollar_share`
Grassroots support: fraction of **individual** money in gifts ≤ $200.
```
small_dollar_share = small_dollar_amount / individual_amount
small_dollar_amount = individual donations where 0 < amount <= 200
```
Denominator: `individual_amount`. The ranker flags the *inverse* (donor
concentration = `1 - small_dollar_share`) with floor 0.90 (i.e. <10% small-dollar).

### Interest-bloc share — `interest_share`
Share of total backing from one curated interest category (`dim_group_mappings`),
counting both direct PAC and IE support from tagged committees. This is what powers
"X% Israel-aligned"-type headlines.
```
backing_in_category = sum of direct_pac + ie_support from committees tagged <category>
interest_share      = backing_in_category / total_backing
```
Denominator: `total_backing`. In Malloy: the `interest_backing` view gives the
numerator per category; divide by `total_backing`. Headline floor: 0.08.
Stances are separate categories (e.g. Israel-aligned vs Israel-dovish vs
Israel-critical) and are never summed together.

### Donor-keyword bloc share (fuzzy)
Share of **individual** money from donors whose name/employer matches a keyword bloc
(e.g. tribes, oil & gas). Denominator: `individual_amount`. These are fuzzy estimates
(see `influence.py` `BLOCS`), labeled `donors: <bloc>` in the ranker to distinguish
them from the curated committee blocs above. Headline floor: 0.08.

### Out-of-state concentration (HHI) — from `individual_by_state`
Herfindahl index over the out-of-state individual money: is it diffuse (many states)
or dominated by one?
```
oos_total = sum of individual money from states != home_state (amount > 0)
hhi       = sum over those states of (state_amount / oos_total)^2
```
Interpretation: > 0.25 concentrated · 0.15–0.25 moderate · < 0.15 diffuse.
Malloy exposes the per-state numerator via the `individual_by_state` view; the
square-and-sum is done in `influence.py` (a two-level aggregate Malloy doesn't do
inline).

---

## Cross-check Procedure

To verify this doc still agrees with the executable layer:
1. Every channel filter in the table above matches `sql/views/candidate_money.sql`.
2. `total_backing` excludes `ie_oppose`; `raised` excludes both IE channels.
3. Spot-check one candidate: `bin/influence.py <name>` section totals should equal
   direct queries against `candidate_money`.

Both representations would have to contain the *same* error for a bug to pass — the
point of the redundancy.
