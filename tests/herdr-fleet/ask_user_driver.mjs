// Test driver for skills/herdr-fleet/extensions/ask-user.ts.
//
// Loads the extension through pi's own extension loader (jiti + module
// aliases, the same code path pi uses), captures `herdr:blocked` emissions on
// the real EventBus, and runs named scenarios against stub ctx.ui dialogs.
// Prints one JSON report to stdout: { loadErrors, timeline, results }.
//
// Usage: node ask_user_driver.mjs <scenario> --ext <path to ask-user.ts>
// Pi is located via PI_CODING_AGENT_DIR or ~/.pi/agent.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const argv = process.argv.slice(2);
const scenario = argv[0];
const extFlag = argv.indexOf("--ext");
const extPath = extFlag >= 0 ? argv[extFlag + 1] : process.env.ASK_USER_EXT;

if (!scenario || !extPath) {
	console.error("usage: node ask_user_driver.mjs <scenario> --ext <path>");
	process.exit(2);
}

const agentDir = process.env.PI_CODING_AGENT_DIR ?? path.join(os.homedir(), ".pi/agent");
const version = fs.readFileSync(path.join(agentDir, "install/current-version"), "utf8").trim();
const pkgDir = path.join(agentDir, "install/releases", version, "node_modules/@earendil-works/pi-coding-agent");
const { loadExtensions, createExtensionRuntime } = await import(path.join(pkgDir, "dist/core/extensions/loader.js"));
const { createEventBus } = await import(path.join(pkgDir, "dist/core/event-bus.js"));

const timeline = [];
const eventBus = createEventBus();
eventBus.on("herdr:blocked", (data) => timeline.push({ type: "blocked-event", data }));

const { extensions, errors } = await loadExtensions([extPath], process.cwd(), eventBus, createExtensionRuntime());
const tool = extensions[0]?.tools.get("ask_user")?.definition;
const command = extensions[0]?.commands.get("ask-user");
const registered = {
	tools: extensions[0] ? [...extensions[0].tools.keys()] : [],
	commands: extensions[0] ? [...extensions[0].commands.keys()] : [],
};

const deferred = () => {
	let resolve, reject;
	const promise = new Promise((res, rej) => ((resolve = res), (reject = rej)));
	return { promise, resolve, reject };
};

const serOpts = (opts) => ({ hasSignal: opts?.signal instanceof AbortSignal, timeout: opts?.timeout });

function makeUi(behavior) {
	const record = (kind, openInfo) => {
		const gate = deferred();
		timeline.push({ type: `${kind}.open`, ...openInfo });
		return gate;
	};
	return {
		select: async (title, options, opts) => {
			const gate = record("select", { title, options, opts: serOpts(opts) });
			const value = behavior.select ? await behavior.select(title, options, opts, gate) : undefined;
			timeline.push({ type: "select.close", value });
			return value;
		},
		input: async (title, placeholder, opts) => {
			const gate = record("input", { title, placeholder, opts: serOpts(opts) });
			const value = behavior.input ? await behavior.input(title, placeholder, opts, gate) : undefined;
			timeline.push({ type: "input.close", value });
			return value;
		},
		confirm: async () => {
			throw new Error("confirm() is not used by ask_user");
		},
		notify: (message, notifyType) => timeline.push({ type: "notify", message, notifyType }),
	};
}

function makeCtx(behavior, { mode = "tui", hasUI = true, signal } = {}) {
	return { mode, hasUI, cwd: process.cwd(), signal, isIdle: () => false, ui: makeUi(behavior) };
}

const summarize = (result) => ({
	content: result.content.map((c) => (c.type === "text" ? { type: "text", text: c.text } : { type: c.type })),
	details: result.details,
	structuredContent: result.structuredContent,
	isError: result.isError === true,
});

const run = async (toolDef, params, ctx, signal) =>
	summarize(await toolDef.execute("call-1", params, signal, (partial) => timeline.push({ type: "update", partial: summarize(partial) }), ctx));

const scenarios = {
	"select-answer": async () => {
		const ctx = makeCtx({ select: async () => "B" });
		return { results: [await run(tool, { question: "Pick one", options: ["A", "B"] }, ctx)] };
	},
	"input-answer": async () => {
		const ctx = makeCtx({ input: async () => "  bob  " });
		return { results: [await run(tool, { question: "Name?" }, ctx)] };
	},
	"input-placeholder": async () => {
		const ctx = makeCtx({ input: async () => "x" });
		return { results: [await run(tool, { question: "Name?", placeholder: "your name" }, ctx)] };
	},
	"select-cancel": async () => {
		const ctx = makeCtx({ select: async () => undefined });
		return { results: [await run(tool, { question: "Pick one", options: ["A", "B"] }, ctx)] };
	},
	"input-cancel": async () => {
		const ctx = makeCtx({ input: async () => undefined });
		return { results: [await run(tool, { question: "Name?" }, ctx)] };
	},
	"input-blank": async () => {
		const ctx = makeCtx({ input: async () => "   " });
		return { results: [await run(tool, { question: "Name?" }, ctx)] };
	},
	"no-ui": async () => {
		const ctx = makeCtx({}, { mode: "print", hasUI: false });
		return { results: [await run(tool, { question: "Name?" }, ctx)] };
	},
	"empty-options": async () => {
		const ctx = makeCtx({ select: async () => "A" });
		return { results: [await run(tool, { question: "Pick", options: [] }, ctx)] };
	},
	"dialog-throw": async () => {
		const ctx = makeCtx({ input: async () => Promise.reject(new Error("boom")) });
		return { results: [await run(tool, { question: "Name?" }, ctx)] };
	},
	"update-throw": async () => {
		const ctx = makeCtx({ input: async () => "x" });
		const result = await tool.execute("call-1", { question: "Name?" }, undefined, () => {
			throw new Error("update boom");
		}, ctx);
		return { results: [summarize(result)] };
	},
	"pre-aborted": async () => {
		const ctl = new AbortController();
		ctl.abort();
		const ctx = makeCtx({ input: async () => "x" }, { signal: ctl.signal });
		return { results: [await run(tool, { question: "Name?" }, ctx, ctl.signal)] };
	},
	"abort-during-wait": async () => {
		const ctl = new AbortController();
		const ctx = makeCtx(
			{
				input: async (_t, _p, opts) => {
					await new Promise((res) => {
						if (opts?.signal?.aborted) return res();
						opts?.signal?.addEventListener("abort", res, { once: true });
					});
					return undefined;
				},
			},
			{ signal: ctl.signal },
		);
		const pending = run(tool, { question: "Name?" }, ctx, ctl.signal);
		setTimeout(() => ctl.abort(), 30);
		return { results: [await pending] };
	},
	"timeout-forward": async () => {
		const ctx = makeCtx({ select: async () => undefined });
		return { results: [await run(tool, { question: "Pick", options: ["A", "B"], timeout: 1500 }, ctx)] };
	},
	concurrent: async () => {
		const gates = [];
		const behavior = {
			select: async (_t, _o, _opts, gate) => {
				gates.push(gate);
				if (gates.length === 2) {
					gates[0].resolve("A");
					gates[1].resolve("B");
				}
				return gate.promise;
			},
		};
		const ctx = makeCtx(behavior);
		const results = await Promise.all([
			run(tool, { question: "First?", options: ["A", "B"] }, ctx),
			run(tool, { question: "Second?", options: ["A", "B"] }, ctx),
		]);
		return { results };
	},
	"command-select": async () => {
		const ctx = makeCtx({ select: async () => "A" });
		await command.handler("Pick one | A | B", ctx);
		return { results: [] };
	},
	"command-input": async () => {
		const ctx = makeCtx({ input: async () => "typed answer" });
		await command.handler("What now?", ctx);
		return { results: [] };
	},
	"command-usage": async () => {
		const ctx = makeCtx({});
		await command.handler("   ", ctx);
		return { results: [] };
	},
};

if (!(scenario in scenarios)) {
	console.error(`unknown scenario ${scenario}; have: ${Object.keys(scenarios).join(", ")}`);
	process.exit(2);
}

const out = { loadErrors: errors, registered, timeline, ...(await scenarios[scenario]()) };
console.log(JSON.stringify(out));
