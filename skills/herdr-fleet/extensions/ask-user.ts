/**
 * ask_user - ask the user a question while reporting the pane as blocked.
 *
 * While a call waits for user input, the extension emits `herdr:blocked`
 * `{ active: true, label }` on pi.events and emits `{ active: false }` exactly
 * once when the wait ends (answer, cancel, abort, or error). The herdr pi
 * integration counts active blockers and reports the pane as "blocked" while
 * the count is positive; the false emit must therefore fire only for a wait
 * that previously emitted true, so other blockers are never released early.
 * Without the integration the emits are no-ops and the tool behaves as a
 * plain question dialog.
 *
 * Also registers `/ask-user <question> [| option...]` so the same wait path
 * can be exercised by hand or in acceptance checks.
 */

import type { AgentToolResult, ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Text } from "@earendil-works/pi-tui";
import { Type } from "typebox";

const BLOCKED_EVENT = "herdr:blocked";

const AskUserParams = Type.Object({
	question: Type.String({ description: "The question shown to the user" }),
	options: Type.Optional(
		Type.Array(Type.String(), {
			minItems: 1,
			description: "Choices to pick from. Omit for a free-text answer.",
		}),
	),
	placeholder: Type.Optional(
		Type.String({ description: "Placeholder text for the free-text input; ignored when options are given" }),
	),
	timeout: Type.Optional(
		Type.Integer({
			minimum: 0,
			description: "Milliseconds to wait before the dialog auto-dismisses and the call reports cancelled",
		}),
	),
});

type AskUserStatus = "waiting" | "answered" | "cancelled" | "unavailable" | "invalid" | "error";
type AskUserKind = "select" | "input";

interface AskUserDetails {
	question: string;
	kind: AskUserKind;
	options?: string[];
	status: AskUserStatus;
	answer: string | null;
	error?: string;
}

const AskUserResultSchema = Type.Object({
	status: Type.Union([
		Type.Literal("answered"),
		Type.Literal("cancelled"),
		Type.Literal("unavailable"),
		Type.Literal("invalid"),
		Type.Literal("error"),
	]),
	answer: Type.Union([Type.String(), Type.Null()]),
});

export default function askUserExtension(pi: ExtensionAPI) {
	async function ask(
		params: { question: string; options?: string[]; placeholder?: string; timeout?: number },
		ctx: ExtensionContext,
		signal: AbortSignal | undefined,
		onUpdate?: (result: AgentToolResult<AskUserDetails>) => void,
	): Promise<AgentToolResult<AskUserDetails>> {
		const options = params.options ?? [];
		const kind: AskUserKind = options.length > 0 ? "select" : "input";
		const base = {
			question: params.question,
			kind,
			options: options.length > 0 ? options : undefined,
		};
		const result = (status: Exclude<AskUserStatus, "waiting">, answer: string | null, error?: string) => {
			const text =
				status === "answered"
					? kind === "select"
						? `User selected: ${answer}`
						: `User answered: ${answer}`
					: status === "cancelled"
						? "User cancelled without answering"
						: status === "unavailable"
							? `ask_user cannot ask the user: no interactive UI (mode: ${ctx.mode}). Ask the user directly in text instead.`
							: status === "invalid"
								? `ask_user invalid parameters: ${error}`
								: `ask_user failed: ${error}`;
			return {
				content: [{ type: "text" as const, text }],
				details: { ...base, status, answer, ...(error ? { error } : {}) },
				structuredContent: { status, answer },
				isError: status !== "answered" && status !== "cancelled",
			};
		};

		if (!ctx.hasUI) {
			return result("unavailable", null);
		}
		if (params.options !== undefined && options.length === 0) {
			return result("invalid", null, "options must be non-empty when provided");
		}
		if (signal?.aborted) {
			return result("cancelled", null);
		}

		pi.events.emit(BLOCKED_EVENT, { active: true, label: params.question });
		try {
			onUpdate?.({
				content: [{ type: "text", text: `Waiting for user: ${params.question}` }],
				details: { ...base, status: "waiting", answer: null },
			});
			const answer =
				options.length > 0
					? await ctx.ui.select(params.question, options, { signal, timeout: params.timeout })
					: await ctx.ui.input(params.question, params.placeholder, { signal, timeout: params.timeout });
			if (answer === undefined || answer.trim() === "") {
				return result("cancelled", null);
			}
			return result("answered", answer.trim());
		} catch (error) {
			return result("error", null, error instanceof Error ? error.message : String(error));
		} finally {
			pi.events.emit(BLOCKED_EVENT, { active: false });
		}
	}

	pi.registerTool({
		name: "ask_user",
		label: "Ask user",
		description:
			"Ask the user a question and wait for their answer. Shows a selection list when options are given, otherwise a free-text input. Marks the agent as blocked while waiting. Returns the user's answer, or a cancelled/unavailable status.",
		promptSnippet: "ask the user a question and wait for the answer",
		promptGuidelines: [
			"Use ask_user for decisions only the user can make; do not ask in plain text and end the turn.",
		],
		parameters: AskUserParams,
		outputSchema: AskUserResultSchema,
		exposure: "model-only",
		annotations: { readOnlyHint: true },
		executionMode: "sequential",

		async execute(_toolCallId, params, signal, onUpdate, ctx) {
			return ask(params, ctx, signal, onUpdate);
		},

		renderCall(args, theme, _context) {
			let text = theme.fg("toolTitle", theme.bold("ask_user ")) + theme.fg("muted", args.question);
			if (Array.isArray(args.options) && args.options.length) {
				text += `\n${theme.fg("dim", `  Options: ${args.options.join(", ")}`)}`;
			}
			return new Text(text, 0, 0);
		},

		renderResult(result, _options, theme, _context) {
			const details = result.details as AskUserDetails | undefined;
			if (!details) {
				const text = result.content[0];
				return new Text(text?.type === "text" ? text.text : "", 0, 0);
			}
			if (details.status === "answered" && details.answer !== null) {
				return new Text(theme.fg("success", "✓ ") + theme.fg("accent", details.answer), 0, 0);
			}
			if (details.status === "waiting") {
				return new Text(theme.fg("muted", "Waiting for user…"), 0, 0);
			}
			if (details.status === "cancelled") {
				return new Text(theme.fg("warning", "Cancelled"), 0, 0);
			}
			const text = result.content[0];
			return new Text(theme.fg("error", text?.type === "text" ? text.text : "ask_user failed"), 0, 0);
		},
	});

	pi.registerCommand("ask-user", {
		description:
			"Ask the user a question: /ask-user <question> [| option...]. Exercises the same herdr:blocked wait path as the ask_user tool.",
		handler: async (args, ctx) => {
			const parts = args
				.split("|")
				.map((part) => part.trim())
				.filter((part) => part.length > 0);
			if (parts.length === 0) {
				ctx.ui.notify("Usage: /ask-user <question> [| option...]", "error");
				return;
			}
			const [question, ...options] = parts;
			const result = await ask(options.length > 0 ? { question, options } : { question }, ctx, ctx.signal);
			const text = result.content[0];
			ctx.ui.notify(text?.type === "text" ? text.text : "ask_user done", "info");
		},
	});
}
