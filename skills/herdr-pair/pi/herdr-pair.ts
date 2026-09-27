/** Pi compaction bridge for herdr-pair. Install as a symlink in ~/.pi/agent/extensions. */
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { realpathSync } from "node:fs";

const helper = resolve(dirname(realpathSync(fileURLToPath(import.meta.url))), "../scripts/pi_compact_bridge.py");

export default function (pi: ExtensionAPI) {
  let recovery: string | undefined;
  pi.on("session_compact", async (event, ctx) => {
    // A retrying overflow turn must not race Herdr's idle continuation.
    if (event.willRetry) return;
    const pane = process.env.HERDR_PANE_ID;
    if (!pane) return;
    try {
      const result = await pi.exec("python3", [helper, "--cwd", ctx.cwd,
        "--pane", pane, "--session-id", ctx.sessionManager.getSessionId(),
        "--event", "session_compact"], { timeout: 30000 });
      if (result.code === 0) {
        const answer = JSON.parse(result.stdout);
        if (answer.status === "rollover_recorded") recovery = answer.context;
      }
    } catch { /* watcher remains the fallback for an unadvanced epoch */ }
  });
  pi.on("before_agent_start", async () => {
    if (!recovery) return;
    const content = recovery;
    recovery = undefined;
    return { message: { customType: "herdr-pair-recovery", content, display: false } };
  });
  pi.on("session_start", async () => { recovery = undefined; });
}
