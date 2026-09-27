import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const moduleUrl = pathToFileURL(resolve('skills/herdr-pair/pi/herdr-pair.ts'));
const { default: extension } = await import(moduleUrl.href);
const handlers = new Map();
const calls = [];
const pi = {
  on(name, handler) { handlers.set(name, handler); },
  async exec(command, args) {
    calls.push([command, args]);
    return { code: 0, stdout: JSON.stringify({ status: 'rollover_recorded', context: 'CHECKPOINT recovery' }) };
  },
};
extension(pi);
process.env.HERDR_PANE_ID = 'pane-a';
const ctx = { cwd: '/work', sessionManager: { getSessionId: () => 'session-a' } };
assert.equal(handlers.has('session_compact_failed'), false);
await handlers.get('session_compact')({ reason: 'overflow', willRetry: true }, ctx);
assert.equal(calls.length, 0);
assert.equal(await handlers.get('before_agent_start')(), undefined);
await handlers.get('session_compact')({ reason: 'manual', willRetry: false }, ctx);
assert.equal(calls.length, 1);
assert.deepEqual(calls[0][1].slice(-6), ['--pane', 'pane-a', '--session-id', 'session-a', '--event', 'session_compact']);
assert.match((await handlers.get('before_agent_start')()).message.content, /CHECKPOINT recovery/);
assert.equal(await handlers.get('before_agent_start')(), undefined);
console.log('Pi fake-event extension test passed');
