# Wait for the executor

Read this after `send-round` returns `agent_prompted`. Confirm flags with `herdr agent wait --help`;
the installed CLI is authoritative. Address the executor's pane id on the local session.
This recipe does not use `--machine`.

`herdr agent wait` is the settle signal. It is event-driven, and it returns immediately when
the agent is already in a matching state. A plugin status notice is easy to miss. An ETA
followed by a chat check races the same way. Neither replaces this wait.

## Command

Wait only after `agent_prompted`. A failed or uncertain send has no active round to settle;
follow [recovery.md](recovery.md) for pending delivery.

```bash
herdr agent wait "$EXECUTOR_PANE" \
  --until idle --until done --until blocked \
  --timeout 600000
```

`--timeout` is milliseconds. Wait commands have no default timeout, so every recipe sets one.
`600000` (10 minutes) is an example bound; choose the bound from the round. Repeat `--until`
for the exact set. The three states above make an approval dialog observable. A settle-only
wait is `--until idle --until done` with the same timeout; a dialog then surfaces as timeout
instead of its own branch.

Without `--until`, the match set is `idle`, `done`, or `blocked`. Keep `blocked` as a branch
whenever it is in the set. Do not pass `--until unknown` unless you mean to treat an
unclassified agent as settled.

Exit 0 prints JSON. The agent is `.result.agent`, and its lifecycle field is `agent_status`
(the same field `herdr agent get` returns). Exit 1 is a server error, including `timeout`,
as JSON on stderr. Exit 2 is a CLI usage error.

`herdr agent prompt --wait` does not track turns. A previous turn can satisfy it, and a
blocked agent rejects the prompt with `agent_blocked` before any input is sent. `send-round`
already delivered the handoff; settle that delivery with standalone `agent wait`. The older
`herdr wait agent-status` command is not this interface.

## Branch on `agent_status`

### `idle` or `done`

The agent can take input. `done` is the server's unseen idle; a read does not mark it seen.
Both mean the lifecycle settled. They do not mean the round is accepted.

Run artifact verification: the on-disk report, `diff-round`, and the rest of
[verification.md](verification.md). A callback, a plugin notice, and this wait are all
notifications. Acceptance still requires the report and the diff.

`agent wait` can match a state from before this turn, including the idle that was still on
screen when the prompt landed. When the report file is absent, run:

```bash
herdr agent get "$EXECUTOR_PANE"
herdr agent read "$EXECUTOR_PANE" --source visible
```

If `agent_status` is now `working`, or the screen shows this handoff in progress, run the
same `agent wait` again. If the pane is still the pre-turn idle and the new turn has not
started, leave the active round in place. Inspect once. Then one further bounded wait, or
one stop/report request. Do not resend the handoff.

### `blocked`

Herdr recognized an approval or question UI. The round is not finished. Do not
`finish-round --status accepted` from this state.

```bash
herdr agent read "$EXECUTOR_PANE" --source visible
```

The dialog is on the live screen. A `--lines` read that must scroll alternate-screen history
returns `agent_not_idle` while the agent is blocked; use `--source visible` and stop there.

Then choose one:

- The contract already states the answer. Send that answer with
  `herdr agent send-keys "$EXECUTOR_PANE" <key>...` (`enter`, `esc`, `up`, `ctrl+c`;
  `escape` is an alias of `esc`). Wait again for `idle`/`done` afterward. You may keep
  `blocked` in the `--until` set.
- The dialog is not covered by the contract. Stop and escalate to the user.

Do not `herdr agent prompt` a blocked agent. That returns `agent_blocked` and sends nothing.

### Timeout

The command exited 1 with error code `timeout`, or the bound elapsed. `send-round` may
already have delivered the prompt. Before any resend or stop:

```bash
herdr agent get "$EXECUTOR_PANE"
herdr agent read "$EXECUTOR_PANE" --source visible
```

- `working`: one more bounded `agent wait`. Do not start a sleep/poll loop.
- `blocked`: take the blocked branch above.
- `idle` or `done`: go to artifact verification.
- `unknown`, a missing agent, or a screen that disagrees with `agent_status`: the state is
  unknown. Do not clear, resend, or accept. Follow [recovery.md](recovery.md) and
  [executor-resolution.md](executor-resolution.md).

Uncertain delivery (`resolve-pending`) applies when `send-round` did not return
`agent_prompted`. A timeout after a confirmed prompt is an active round, not a second send.

## Leave these alone

- Do not tight-poll `agent get`, `agent read`, or chat.
- Do not wait only for a plugin `executor done` / `executor blocked` notice. Those notices
  still say to verify artifacts; they are not the settle signal.
- Do not omit `--timeout`.
- The compact-continue watcher and the fresh-session screen check are separate. This wait
  does not replace them.
