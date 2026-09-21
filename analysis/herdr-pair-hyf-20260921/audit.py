"""Reproduce log-reported usage; no network, repository changes, or task execution."""
import collections
import csv
import hashlib
import json
import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCE = Path('/public/log/hyf/session-63ae5e07-20260921.jsonl')
OUT = Path(__file__).resolve().parent
rows = [json.loads(line) for line in SOURCE.read_text().splitlines()]
fields = ['input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens']
calls = {}
tools = {}
for line, row in enumerate(rows, 1):
    message = row.get('message', {})
    content = message.get('content', [])
    if row.get('type') == 'assistant' and message.get('model') != '<synthetic>':
        key = message['id']
        usage = message.get('usage', {})
        if key in calls:
            assert calls[key]['usage'] == usage, (line, key)
        else:
            calls[key] = dict(line=line, timestamp=row['timestamp'], id=key,
                              model=message.get('model'), usage=usage, blocks=[])
        calls[key]['blocks'].extend(content)
    if isinstance(content, list):
        for block in content:
            if block.get('type') == 'tool_use':
                assert block['id'] not in tools, (line, block['id'])
                tools[block['id']] = dict(line=line, name=block['name'], input=block.get('input', {}),
                                          result_chars=0, result_lines=[])
            if block.get('type') == 'tool_result' and block.get('tool_use_id') in tools:
                result = block.get('content', '')
                size = len(result) if isinstance(result, str) else len(json.dumps(result, ensure_ascii=False))
                tools[block['tool_use_id']]['result_chars'] += size
                tools[block['tool_use_id']]['result_lines'].append(line)

def summarize(selected):
    context = [sum(c['usage'].get(k, 0) for k in fields[:3]) for c in selected]
    sums = {k: sum(c['usage'].get(k, 0) for c in selected) for k in fields}
    return dict(calls=len(selected), **sums,
                thinking_tokens=sum(c['usage'].get('output_tokens_details', {}).get('thinking_tokens', 0) for c in selected),
                context_min=min(context, default=0), context_median=statistics.median(context) if context else 0,
                context_max=max(context, default=0))

windows = [('whole', 1, 4813), ('before_pair_request', 1, 1923),
           ('pair_request_through_push', 1924, 4717), ('pair_phase1', 1924, 3366),
           ('phase2', 3367, 4593), ('close_and_push', 4594, 4717), ('post_work_export', 4718, 4813)]
summary = {name: summarize([c for c in calls.values() if lo <= c['line'] <= hi]) for name, lo, hi in windows}
summary['source'] = dict(path=str(SOURCE), sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(), lines=len(rows))
summary['method'] = 'Unique message.id; identical usage across blocks asserted. Exclude synthetic. Windows are line ranges, not causal task attribution. thinking_tokens are a subset of output, never added again.'
summary['windows'] = windows
summary['naive_block_sum'] = {k: sum(r.get('message', {}).get('usage', {}).get(k, 0) for r in rows if r.get('type') == 'assistant') for k in fields}
summary['cache_creation'] = {k: sum(c['usage'].get('cache_creation', {}).get(k, 0) for c in calls.values()) for k in ['ephemeral_1h_input_tokens', 'ephemeral_5m_input_tokens']}
summary['high_write_calls'] = [dict(line=c['line'], timestamp=c['timestamp'], **{k: c['usage'].get(k, 0) for k in fields}) for c in calls.values() if c['usage'].get('cache_creation_input_tokens', 0) > 100000]
summary['round6_pre_dispatch_window'] = summarize([c for c in calls.values() if 3393 <= c['line'] <= 3819])
summary['round9_window'] = summarize([c for c in calls.values() if 4503 <= c['line'] <= 4593])
handoff_calls = [c for c in calls.values() if any(b.get('type') == 'tool_use' and b.get('name') == 'Write' and '/.handoff/round' in b.get('input', {}).get('file_path', '') for b in c['blocks'])]
summary['handoff_initial_write_calls'] = summarize(handoff_calls)
summary['handoff_initial_write_calls']['note'] = 'Whole-call output includes thinking and companion text, not just handoff text; excludes later edits.'
summary['phase1_large_cache_writes'] = summarize([c for c in calls.values() if 1924 <= c['line'] <= 3366 and c['usage'].get('cache_creation_input_tokens', 0) > 100000])
summary['by_date'] = {}
for zone in ['UTC', 'Asia/Shanghai']:
    daily = collections.defaultdict(list)
    for c in calls.values():
        day = datetime.fromisoformat(c['timestamp'].replace('Z', '+00:00')).astimezone(ZoneInfo(zone)).date().isoformat()
        daily[day].append(c)
    summary['by_date'][zone] = {day: summarize(group) for day, group in daily.items()}
(OUT / 'metrics.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
with (OUT / 'calls.csv').open('w') as f:
    writer = csv.DictWriter(f, fieldnames=['line', 'timestamp', 'id', 'model', *fields, 'thinking_tokens', 'context_tokens'])
    writer.writeheader()
    for c in calls.values():
        writer.writerow({**{k: c[k] for k in ['line', 'timestamp', 'id', 'model']},
                         **{k: c['usage'].get(k, 0) for k in fields},
                         'thinking_tokens': c['usage'].get('output_tokens_details', {}).get('thinking_tokens', 0),
                         'context_tokens': sum(c['usage'].get(k, 0) for k in fields[:3])})
with (OUT / 'tools.csv').open('w') as f:
    writer = csv.DictWriter(f, fieldnames=['line', 'name', 'description', 'result_chars', 'result_lines'])
    writer.writeheader()
    for t in tools.values():
        writer.writerow({k: t[k] for k in ['line', 'name', 'result_chars', 'result_lines']} |
                        {'description': t['input'].get('description', t['input'].get('file_path', ''))})
print(json.dumps({k: v for k, v in summary.items() if k not in ['high_write_calls', 'windows']}, ensure_ascii=False, indent=2))
