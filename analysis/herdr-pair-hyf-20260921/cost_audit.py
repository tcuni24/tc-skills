"""Join local usage export to the previously deduplicated session calls.

Workbook amounts have no explicit currency label. Preserve their accounting unit.
No network requests or model pricing assumptions are used.
"""
import collections
import csv
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

OUT = Path(__file__).resolve().parent
SOURCE = Path('/public/log/hyf/usage_2026-09-20_to_2026-09-21.xlsx')
TOKEN_FIELDS = {
    'input_tokens': '输入 Token', 'output_tokens': '输出 Token',
    'cache_read_input_tokens': '缓存读取 Token',
    'cache_creation_input_tokens': '缓存创建 Token',
}
FEE_FIELDS = {
    'input_cost': '输入费用', 'output_cost': '输出费用',
    'cache_read_cost': '缓存读取费用', 'cache_creation_cost': '缓存创建费用',
    'charged': '用户扣费',
}

workbook = load_workbook(SOURCE, read_only=True, data_only=True)
values = workbook['Usage'].values
headers = next(values)
bills = []
for row_number, values_row in enumerate(values, 2):
    raw = dict(zip(headers, values_row))
    bill = dict(row=row_number, time=raw['时间'], model=raw['请求'])
    bill.update({key: int(raw[column] or 0) for key, column in TOKEN_FIELDS.items()})
    bill.update({key: Decimal(raw[column] or '0') for key, column in FEE_FIELDS.items()})
    assert abs(sum(bill[k] for k in FEE_FIELDS if k != 'charged') - bill['charged']) <= Decimal('0.000002')
    assert Decimal(raw['倍率']) == Decimal(raw['账号倍率']) == 1
    assert Decimal(raw['原始']) == Decimal(raw['账号计费']) == bill['charged']
    bills.append(bill)
workbook.close()

calls = list(csv.DictReader((OUT / 'calls.csv').open()))
index = collections.defaultdict(list)
input_fields = ['input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens']
for bill in bills:
    index[(bill['model'], *(bill[k] for k in input_fields))].append(bill)

matches = []
used_rows = set()
unmatched_calls = []
for call in calls:
    day = datetime.fromisoformat(call['timestamp'].replace('Z', '+00:00')).astimezone(
        datetime.fromisoformat(bills[0]['time']).tzinfo).date().isoformat()
    if not '2026-09-20' <= day <= '2026-09-21':
        continue
    candidates = index[(call['model'], *(int(call[k]) for k in input_fields))]
    exact = [bill for bill in candidates if bill['output_tokens'] == int(call['output_tokens'])]
    candidates = exact or candidates
    if len(candidates) != 1:
        unmatched_calls.append(dict(line=int(call['line']), candidates=len(candidates)))
        continue
    bill = candidates[0]
    delta = (datetime.fromisoformat(bill['time']) - datetime.fromisoformat(call['timestamp'].replace('Z', '+00:00'))).total_seconds()
    assert abs(delta) < 120, (call['line'], bill['row'], delta)
    assert bill['row'] not in used_rows
    used_rows.add(bill['row'])
    matches.append(bill | dict(line=int(call['line']), log_time=call['timestamp'],
                              log_output_tokens=int(call['output_tokens']), delta_seconds=delta,
                              method='model+4_token_fields+time' if exact else 'model+3_input_fields+time'))

def summarize(group):
    return dict(requests=len(group), **{k: sum(b[k] for b in group) for k in TOKEN_FIELDS},
                **{k: str(sum((b[k] for b in group), Decimal(0))) for k in FEE_FIELDS})

result = {
    'source': dict(path=str(SOURCE), sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                   sheet='Usage', rows=len(bills), start=min(b['time'] for b in bills), end=max(b['time'] for b in bills)),
    'currency': 'Not explicitly specified in workbook; all amounts retain workbook units.',
    'method': 'Model and token counts, unique one-to-one match, absolute timestamp delta under 120 seconds. No shared request ID exists in session log. 3 records match input fields but differ in output usage; bill output is used for cost accounting, without inferring the cause of discrepancy.',
    'all_bills': summarize(bills),
    'matched_main_calls': summarize(matches),
    'by_model': {model: summarize([b for b in bills if b['model'] == model]) for model in sorted({b['model'] for b in bills})},
    'matched_windows': {},
    'unmatched_bills': summarize([b for b in bills if b['row'] not in used_rows]),
    'unmatched_calls_in_dates': unmatched_calls,
    'output_usage_differences': [{k: b[k] for k in ['row', 'line', 'log_output_tokens', 'output_tokens', 'delta_seconds']} for b in matches if b['method'].endswith('3_input_fields+time')],
    'sonnet5_output_7_or_8': summarize([b for b in bills if b['model'] == 'claude-sonnet-5' and b['output_tokens'] in [7, 8]]),
    'top_charged_rows': [{k: b[k] for k in ['row', 'time', 'model', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens']} | {'charged': str(b['charged'])} for b in sorted(bills, key=lambda b: b['charged'], reverse=True)[:10]],
}
for name, low, high in [('precompact_in_export', 1, 3366), ('phase2', 3367, 4593),
                        ('close_and_push', 4594, 4717), ('after_work', 4718, 4813),
                        ('pair_window_in_export', 1924, 4717)]:
    result['matched_windows'][name] = summarize([b for b in matches if low <= b['line'] <= high])
assert len(matches) == 370 and len(bills) == 736 and not unmatched_calls
assert Decimal(result['matched_main_calls']['charged']) + Decimal(result['unmatched_bills']['charged']) == Decimal(result['all_bills']['charged'])
(OUT / 'cost-metrics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
fields = ['row', 'time', 'model', 'line', 'log_time', 'method', 'delta_seconds', *TOKEN_FIELDS, 'log_output_tokens', *FEE_FIELDS]
with (OUT / 'cost-matches.csv').open('w') as file:
    writer = csv.DictWriter(file, fieldnames=fields)
    writer.writeheader()
    writer.writerows(matches)
print(json.dumps(result, ensure_ascii=False, indent=2))
