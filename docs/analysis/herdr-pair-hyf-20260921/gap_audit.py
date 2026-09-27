"""Cache-rebuild vs. call-gap analysis and pre-pairing context attribution.

Reads calls.csv (from audit.py) and cost-matches.csv (from cost_audit.py). Rates per
million tokens are derived from the matched bills, not assumed. Estimates for calls
outside the billed dates (2026-09-18 phase 1) are extrapolations at those rates.
"""
import collections
import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parent
FIELDS = ['input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens']
WINDOWS = [('before_pair_request', 1, 1923), ('pair_phase1', 1924, 3366), ('phase2', 3367, 4593),
           ('close_and_push', 4594, 4717), ('post_work_export', 4718, 4813)]
PRE_PAIR_CONTEXT = 540258   # context of the first pairing call, JSONL line 1926
CAP = 150000                # counterfactual context ceiling

calls = []
for row in csv.DictReader((OUT / 'calls.csv').open()):
    for k in ['line', 'context_tokens', *FIELDS]:
        row[k] = int(row[k])
    row['t'] = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
    calls.append(row)
calls.sort(key=lambda r: r['line'])

# Rates: total cost / total tokens per category over matched bills.
cost_cols = {'input_tokens': 'input_cost', 'output_tokens': 'output_cost',
             'cache_read_input_tokens': 'cache_read_cost', 'cache_creation_input_tokens': 'cache_creation_cost'}
tok = collections.Counter()
fee = collections.defaultdict(Decimal)
for row in csv.DictReader((OUT / 'cost-matches.csv').open()):
    for k, c in cost_cols.items():
        tok[k] += int(row[k])
        fee[k] += Decimal(row[c])
rate = {k: float(fee[k] / tok[k] * 1_000_000) for k in FIELDS}

def full_rebuild(c):
    return c['cache_creation_input_tokens'] > 0.9 * c['context_tokens']

def est(c, cap=None):
    """Estimated charge for one call; with cap, pretend context never exceeded cap."""
    read = c['cache_read_input_tokens']
    create = c['cache_creation_input_tokens']
    if cap is not None:
        read = min(read, cap)
        if full_rebuild(c):
            create = min(create, cap)
    return (c['input_tokens'] * rate['input_tokens'] + c['output_tokens'] * rate['output_tokens']
            + read * rate['cache_read_input_tokens'] + create * rate['cache_creation_input_tokens']) / 1e6

def window(line):
    return next((n for n, lo, hi in WINDOWS if lo <= line <= hi), 'other')

def bucket(gap):
    return '>5min' if gap > 300 else ('2-5min' if gap > 120 else '<=2min')

gaps = collections.defaultdict(lambda: dict(calls=0, full_rebuilds=0, cache_creation_tokens=0))
prev = None
for c in calls:
    if prev is not None:
        g = gaps[(window(c['line']), bucket((c['t'] - prev['t']).total_seconds()))]
        g['calls'] += 1
        g['full_rebuilds'] += full_rebuild(c)
        g['cache_creation_tokens'] += c['cache_creation_input_tokens']
    prev = c
overall = collections.defaultdict(lambda: dict(calls=0, full_rebuilds=0))
for (w, b), g in gaps.items():
    overall[b]['calls'] += g['calls']
    overall[b]['full_rebuilds'] += g['full_rebuilds']

per_window = {}
for name, lo, hi in WINDOWS:
    sel = [c for c in calls if lo <= c['line'] <= hi]
    reb = [c for c in sel if full_rebuild(c)]
    per_window[name] = dict(
        calls=len(sel),
        estimated_total=round(sum(est(c) for c in sel), 2),
        estimated_full_rebuild_creation=round(sum(c['cache_creation_input_tokens'] for c in reb) * rate['cache_creation_input_tokens'] / 1e6, 2),
        estimated_cache_read=round(sum(c['cache_read_input_tokens'] for c in sel) * rate['cache_read_input_tokens'] / 1e6, 2),
        estimated_output=round(sum(c['output_tokens'] for c in sel) * rate['output_tokens'] / 1e6, 2),
        full_rebuild_calls=len(reb),
        estimated_if_context_capped=round(sum(est(c, CAP) for c in sel), 2),
    )

p1 = [c for c in calls if 1924 <= c['line'] <= 3366]
p1_reb = [c for c in p1 if full_rebuild(c)]
pre_pair = dict(
    context_tokens=PRE_PAIR_CONTEXT,
    rebuild_cost_attributable=round(sum(min(c['cache_creation_input_tokens'], PRE_PAIR_CONTEXT) for c in p1_reb) * rate['cache_creation_input_tokens'] / 1e6, 2),
    read_cost_attributable=round(sum(min(c['cache_read_input_tokens'], PRE_PAIR_CONTEXT) for c in p1) * rate['cache_read_input_tokens'] / 1e6, 2),
)
pre_pair['total_attributable'] = round(pre_pair['rebuild_cost_attributable'] + pre_pair['read_cost_attributable'], 2)
pre_pair['share_of_phase1_estimate'] = round(pre_pair['total_attributable'] / per_window['pair_phase1']['estimated_total'], 3)

summary = dict(
    method=('Full rebuild = cache_creation > 90% of that call\'s context. Gap = seconds since the previous '
            'deduplicated call in line order. Rates are matched-bill fee / tokens per category; phase 1 on '
            '2026-09-18 is not billed, so its estimate is an extrapolation. Counterfactual cap keeps the same '
            'call sequence and only clamps read/rebuild context to CAP.'),
    rates_per_million=rate,
    gap_buckets_overall=dict(overall),
    gap_buckets_by_window={f'{w}|{b}': g for (w, b), g in sorted(gaps.items())},
    per_window=per_window,
    pre_pair_attribution_phase1=pre_pair,
    counterfactual_cap=CAP,
)
(OUT / 'gap-metrics.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(summary, ensure_ascii=False, indent=2))
