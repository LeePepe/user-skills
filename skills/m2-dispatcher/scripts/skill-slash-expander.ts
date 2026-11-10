/**
 * Skill Slash Expander & M2 Spec-Only Write Guard Extension
 *
 * 1. Automatically resolves and injects `SKILL.md` contents when user messages or
 *    seeded session turns invoke bare `/<skill-name>` or `/skill:<skill-name>`
 *    commands (including multiple slash commands on the same line, such as
 *    `/m2-dispatcher /team-lead /my-designer ...`).
 * 2. Enforces the M2-Dispatcher write boundary via `pi.on("tool_call", ...)`:
 *    when running in the interactive M2 entry session (neither `HERDR_ENV=1` nor
 *    `CINDY_PI_SUBAGENT_RUN_DIR` is set), `edit` and `write` calls are restricted
 *    strictly to simple Spec / Handoff files (`spec.md`, `design.md`, `ledger.md`,
 *    `plan.md`, `/handoff/`, `/specs/*.md`, `/briefs/*.md`). Any attempt to modify
 *    product code or other repository files is physically blocked and routed to
 *    Herdr TL (`herdr-dispatch.sh`) inside a dedicated git worktree.
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

export interface SkillMetadata {
	name: string;
	skillMdPath: string;
	skillDir: string;
}

const SLASH_TOKEN_REGEX = /(?:^|(?<=[\s,;，；(（\[【"'`]))\/(?:skill:)?([a-z0-9][a-z0-9-]{0,63})(?=$|[\s,;:，；：。)）\]】"'`!?！？])/gi;

const ALLOWED_M2_SPEC_PATTERNS: readonly RegExp[] = [
	/(?:^|\/)(?:spec|design|ledger|plan)\.md$/i,
	/\/handoff\//,
	/\/specs\/.*\.md$/i,
	/\/briefs\/.*\.md$/i,
];

export const M2_SPEC_ONLY_BLOCK_REASON =
	"M2-Dispatcher Rule: M2 is only allowed to edit simple spec files (spec.md / design.md). Code and other files must be modified in a worktree by dispatching Herdr TL via herdr-dispatch.sh. After dispatching Herdr TL, NEVER poll with 'sleep && herdr agent read' or 'herdr agent wait'; run 'python3 ~/Development/user-skills/skills/team-lead/scripts/goal-supervisor.py watch --handoff-dir ~/Development/handoff/<goal> --interval 15 --max-wait 600 --json' synchronously until EVT=GOAL_DONE.";

export function isAllowedM2SpecPath(rawPath: string, cwd?: string): boolean {
	if (!rawPath || typeof rawPath !== "string") return false;
	const trimmed = rawPath.trim();
	if (!trimmed) return false;
	const resolved = path.resolve(cwd || process.cwd(), trimmed).split(path.sep).join("/");
	const normalizedRaw = trimmed.split(path.sep).join("/");
	return ALLOWED_M2_SPEC_PATTERNS.some((re) => re.test(resolved) || re.test(normalizedRaw));
}

export function isM2DispatcherMainSession(env: NodeJS.ProcessEnv = process.env): boolean {
	if (env.HERDR_ENV === "1") return false;
	if (env.CINDY_PI_SUBAGENT_RUN_DIR && env.CINDY_PI_SUBAGENT_RUN_DIR.trim().length > 0) return false;
	return true;
}

const GEMINI_MODEL_RE = /^gemini-/i;
const ALLOWED_GEMINI_SCHEMA_KEYS = new Set([
	"type",
	"format",
	"title",
	"description",
	"nullable",
	"enum",
	"maxItems",
	"minItems",
	"properties",
	"required",
	"minProperties",
	"maxProperties",
	"minLength",
	"maxLength",
	"pattern",
	"example",
	"anyOf",
	"default",
	"items",
	"minimum",
	"maximum",
]);

export function isGeminiModel(model?: string | null): boolean {
	if (!model || typeof model !== "string") return false;
	const leaf = model.split("/").pop() ?? model;
	return GEMINI_MODEL_RE.test(leaf);
}

function isObjectRecord(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}

function sanitizeAnyOfBranches(branches: unknown[], out: Record<string, unknown>): void {
	const cleaned: unknown[] = [];
	for (const branch of branches) {
		if (isObjectRecord(branch) && branch.type === "null") {
			out.nullable = true;
		} else {
			cleaned.push(sanitizeGeminiSchema(branch));
		}
	}
	if (cleaned.length === 1 && isObjectRecord(cleaned[0])) {
		for (const [bk, bv] of Object.entries(cleaned[0])) {
			if (!(bk in out)) out[bk] = bv;
		}
	} else if (cleaned.length > 0) {
		out.anyOf = cleaned;
	}
}

export function sanitizeGeminiSchema(node: unknown): unknown {
	if (Array.isArray(node)) {
		return node.map((item) => sanitizeGeminiSchema(item));
	}
	if (!isObjectRecord(node)) {
		return node;
	}
	const out: Record<string, unknown> = {};
	if ("const" in node && !("enum" in node)) {
		out.enum = [String(node.const)];
		if (!("type" in node)) out.type = "string";
	}
	for (const [rawKey, value] of Object.entries(node)) {
		if (rawKey === "properties" && isObjectRecord(value)) {
			const props: Record<string, unknown> = {};
			for (const [pk, pv] of Object.entries(value)) {
				props[pk] = sanitizeGeminiSchema(pv);
			}
			out.properties = props;
			continue;
		}
		const key = rawKey === "oneOf" && Array.isArray(value) ? "anyOf" : rawKey;
		if (!ALLOWED_GEMINI_SCHEMA_KEYS.has(key)) continue;
		if (key === "type") {
			if (Array.isArray(value)) {
				const nonNull = value.filter((item): item is string => typeof item === "string" && item !== "null");
				out.type = nonNull[0] ?? "string";
				if (value.includes("null")) out.nullable = true;
			} else if (value === "null") {
				out.type = "string";
				out.nullable = true;
			} else {
				out.type = value;
			}
			continue;
		}
		if (key === "anyOf" && Array.isArray(value)) {
			sanitizeAnyOfBranches(value, out);
			continue;
		}
		if (key === "enum" && Array.isArray(value)) {
			const nonEmpty = value.filter((item) => item !== "");
			if (nonEmpty.length > 0) {
				out.enum = nonEmpty.map((item) => String(item));
			}
			continue;
		}
		out[key] = sanitizeGeminiSchema(value);
	}
	if ("anyOf" in out) {
		delete out.type;
	}
	if (out.type === "array") {
		const items = out.items;
		if (!isObjectRecord(items) || Object.keys(items).length === 0) {
			out.items = { type: "string" };
		} else if ("anyOf" in items) {
			out.items = { type: "object" };
		}
	}
	if (Array.isArray(out.required)) {
		const props = out.properties;
		if (isObjectRecord(props)) {
			const filtered = out.required.filter((reqKey): reqKey is string => typeof reqKey === "string" && reqKey in props);
			if (filtered.length > 0) {
				out.required = filtered;
			} else {
				delete out.required;
			}
		} else {
			delete out.required;
		}
	}
	return out;
}

export function sanitizeGeminiSchemaInPlace<T>(schema: T): T {
	if (!isObjectRecord(schema)) return schema;
	const cleaned = sanitizeGeminiSchema(schema);
	if (!isObjectRecord(cleaned)) return schema;
	for (const key of Object.keys(schema)) {
		delete schema[key];
	}
	Object.assign(schema, cleaned);
	return schema;
}

export function sanitizeRegisteredToolsInPlace(pi: unknown): number {
	const getAllTools = (pi as { getAllTools?: () => Array<{ name?: string; parameters?: unknown }> })?.getAllTools;
	if (typeof getAllTools !== "function") return 0;
	let count = 0;
	for (const tool of getAllTools.call(pi) ?? []) {
		if (tool && isObjectRecord(tool.parameters)) {
			sanitizeGeminiSchemaInPlace(tool.parameters);
			count += 1;
		}
	}
	return count;
}

export function sanitizeGeminiChatPayload<T>(payload: T, modelHint?: string | null): T {
	if (!isObjectRecord(payload)) return payload;
	const model = typeof payload.model === "string" ? payload.model : modelHint;
	if (!isGeminiModel(model)) {
		return payload;
	}
	const out: Record<string, unknown> = { ...payload };
	delete out.store;
	delete out.reasoning_effort;
	if (Array.isArray(payload.messages)) {
		out.messages = payload.messages.map((msg) => {
			if (!isObjectRecord(msg)) return msg;
			const nextMsg: Record<string, unknown> = {
				...msg,
				...(msg.role === "developer" ? { role: "system" } : {}),
			};
			if (Array.isArray(msg.tools)) {
				nextMsg.tools = msg.tools.map((tool) => {
					if (!isObjectRecord(tool)) return tool;
					if (isObjectRecord(tool.function) && isObjectRecord(tool.function.parameters)) {
						return {
							...tool,
							function: {
								...tool.function,
								parameters: sanitizeGeminiSchema(tool.function.parameters),
							},
						};
					}
					if (isObjectRecord(tool.parameters)) {
						return {
							...tool,
							parameters: sanitizeGeminiSchema(tool.parameters),
						};
					}
					return tool;
				});
			}
			return nextMsg;
		});
	}
	if (Array.isArray(payload.tools) && payload.tools.length > 0) {
		out.tools = payload.tools.map((tool) => {
			if (!isObjectRecord(tool)) return tool;
			if (isObjectRecord(tool.function) && isObjectRecord(tool.function.parameters)) {
				return {
					...tool,
					function: {
						...tool.function,
						parameters: sanitizeGeminiSchema(tool.function.parameters),
					},
				};
			}
			if (isObjectRecord(tool.parameters)) {
				return {
					...tool,
					parameters: sanitizeGeminiSchema(tool.parameters),
				};
			}
			return tool;
		});
	}
	return out as T;
}

export const COPILOT_GEMINI_DEFAULT_HEADERS: Readonly<Record<string, string>> = {
	"User-Agent": "GitHubCopilotChat/0.35.0",
	"Editor-Version": "vscode/1.107.0",
	"Editor-Plugin-Version": "copilot-chat/0.35.0",
	"Copilot-Integration-Id": "vscode-chat",
};

export const GEMINI_LONG_CONTEXT_WINDOW = 1_000_000;

export function alignGeminiModelRuntimeConfig(ctx?: ExtensionContext): void {
	const candidates: Array<Record<string, unknown>> = [];
	if (isObjectRecord(ctx?.model)) {
		candidates.push(ctx.model as Record<string, unknown>);
	}
	const registry = (ctx as { modelRegistry?: { getAll?: () => unknown[] } } | undefined)?.modelRegistry;
	if (typeof registry?.getAll === "function") {
		try {
			for (const m of registry.getAll() ?? []) {
				if (isObjectRecord(m)) candidates.push(m);
			}
		} catch {
			// Ignore registry probe errors
		}
	}
	for (const model of candidates) {
		const id = typeof model.id === "string" ? model.id : "";
		if (!isGeminiModel(id)) continue;
		if (typeof model.contextWindow !== "number" || model.contextWindow < GEMINI_LONG_CONTEXT_WINDOW) {
			model.contextWindow = GEMINI_LONG_CONTEXT_WINDOW;
		}
		const prevCompat = isObjectRecord(model.compat) ? model.compat : {};
		model.compat = {
			...prevCompat,
			supportsStore: false,
			supportsDeveloperRole: false,
			supportsReasoningEffort: false,
			supportsFinishReason: false,
		};
		const prevHeaders = isObjectRecord(model.headers) ? model.headers : {};
		model.headers = {
			...COPILOT_GEMINI_DEFAULT_HEADERS,
			...prevHeaders,
		};
	}
}

function extractFrontmatterName(content: string): string | undefined {
	const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
	if (!match?.[1]) return undefined;
	const nameMatch = match[1].match(/^\s*name\s*:\s*["']?([a-zA-Z0-9_-]+)["']?\s*$/m);
	return nameMatch?.[1]?.trim().toLowerCase();
}

function scanSkillsInDirectory(rootDir: string, out: Map<string, SkillMetadata>): void {
	if (!rootDir || !fs.existsSync(rootDir)) return;
	let entries: fs.Dirent[];
	try {
		entries = fs.readdirSync(rootDir, { withFileTypes: true });
	} catch {
		return;
	}

	for (const entry of entries) {
		if (entry.name.startsWith(".") || entry.name === "node_modules") continue;
		const entryPath = path.join(rootDir, entry.name);
		let stat: fs.Stats;
		try {
			stat = fs.statSync(entryPath);
		} catch {
			continue;
		}
		if (!stat.isDirectory()) continue;

		const skillMdCandidate = path.join(entryPath, "SKILL.md");
		let realSkillMd: string | undefined;
		try {
			if (fs.statSync(skillMdCandidate).isFile()) {
				realSkillMd = fs.realpathSync(skillMdCandidate);
			}
		} catch {
			realSkillMd = undefined;
		}

		if (realSkillMd) {
			const dirKey = entry.name.toLowerCase();
			const realSkillDir = path.dirname(realSkillMd);
			let declaredName: string | undefined;
			try {
				const head = fs.readFileSync(realSkillMd, "utf8").slice(0, 2048);
				declaredName = extractFrontmatterName(head);
			} catch {
				declaredName = undefined;
			}
			const meta: SkillMetadata = {
				name: declaredName || dirKey,
				skillMdPath: realSkillMd,
				skillDir: realSkillDir,
			};
			if (!out.has(dirKey)) out.set(dirKey, meta);
			if (declaredName && !out.has(declaredName)) out.set(declaredName, meta);
		}
	}
}

export function discoverSkills(cwd?: string): Map<string, SkillMetadata> {
	const map = new Map<string, SkillMetadata>();
	const home = os.homedir();
	const effectiveCwd = cwd || process.cwd();

	// 1. Project-local ancestors (.agents/skills, .claude/skills)
	let cursor = path.resolve(effectiveCwd);
	const visitedDirs = new Set<string>();
	while (cursor && !visitedDirs.has(cursor)) {
		visitedDirs.add(cursor);
		scanSkillsInDirectory(path.join(cursor, ".agents", "skills"), map);
		scanSkillsInDirectory(path.join(cursor, ".claude", "skills"), map);
		const parent = path.dirname(cursor);
		if (parent === cursor) break;
		cursor = parent;
	}

	// 2. Global skill roots
	const agentDir = process.env.PI_CODING_AGENT_DIR;
	if (agentDir) {
		scanSkillsInDirectory(path.join(agentDir, "skills"), map);
		scanSkillsInDirectory(path.join(agentDir, "cindy-managed-skills"), map);
	}
	scanSkillsInDirectory(path.join(home, ".agents", "skills"), map);
	scanSkillsInDirectory(path.join(home, ".claude", "skills"), map);
	scanSkillsInDirectory(path.join(home, "Development", "user-skills", "skills"), map);
	scanSkillsInDirectory(path.join(home, "Development", "Skills", "skills", "repo"), map);
	scanSkillsInDirectory(path.join(home, "Development", "Skills", "skills", "meta"), map);

	return map;
}

export function extractSkillTokens(text: string, knownSkills: ReadonlyMap<string, SkillMetadata>): string[] {
	if (!text || typeof text !== "string") return [];
	const found: string[] = [];
	const seen = new Set<string>();
	for (const match of text.matchAll(SLASH_TOKEN_REGEX)) {
		const rawName = match[1]?.toLowerCase();
		if (!rawName) continue;
		const resolved = knownSkills.get(rawName);
		if (!resolved) continue;
		const canonical = resolved.name.toLowerCase();
		if (!seen.has(canonical)) {
			seen.add(canonical);
			found.push(canonical);
		}
	}
	return found;
}

function extractTextFromContent(content: unknown): string {
	if (typeof content === "string") return content;
	if (Array.isArray(content)) {
		return content
			.flatMap((part) => {
				if (part && typeof part === "object" && (part as { type?: string }).type === "text") {
					const text = (part as { text?: unknown }).text;
					return typeof text === "string" ? [text] : [];
				}
				return [];
			})
			.join("\n");
	}
	return "";
}

function formatSkillBlock(meta: SkillMetadata): string | undefined {
	try {
		const body = fs.readFileSync(meta.skillMdPath, "utf8");
		return [
			`<skill-context name="${meta.name}" location="${meta.skillMdPath}">`,
			`Base directory for this skill: ${meta.skillDir}`,
			"",
			body.trim(),
			`</skill-context>`,
		].join("\n");
	} catch {
		return undefined;
	}
}

export default function skillSlashExpander(pi: ExtensionAPI): void {
	let skillCatalog = discoverSkills();
	const injectedSkills = new Set<string>();
	const pendingQueue = new Set<string>();
	let scannedHistory = false;

	const origRegisterTool = (pi as { registerTool?: (tool: unknown, ...rest: unknown[]) => unknown }).registerTool;
	if (typeof origRegisterTool === "function") {
		(pi as { registerTool: (tool: unknown, ...rest: unknown[]) => unknown }).registerTool = (tool: unknown, ...rest: unknown[]) => {
			if (isObjectRecord(tool) && isObjectRecord(tool.parameters)) {
				sanitizeGeminiSchemaInPlace(tool.parameters);
			}
			return origRegisterTool.call(pi, tool, ...rest);
		};
	}

	function sanitizeTranscriptSystemTools(ctx?: ExtensionContext): void {
		const sm = ctx?.sessionManager as
			| {
					getBranch?: () => unknown[];
					getEntries?: () => unknown[];
			  }
			| undefined;
		const entries = sm?.getEntries?.() ?? sm?.getBranch?.() ?? [];
		if (!Array.isArray(entries)) return;
		for (const raw of entries) {
			if (!isObjectRecord(raw)) continue;
			const msg = raw.message;
			if (!isObjectRecord(msg) || !Array.isArray(msg.toolsAdded)) continue;
			for (const tool of msg.toolsAdded) {
				if (isObjectRecord(tool) && isObjectRecord(tool.parameters)) {
					sanitizeGeminiSchemaInPlace(tool.parameters);
				}
			}
		}
	}

	function refreshCatalog(ctx?: ExtensionContext, systemPromptSkills?: ReadonlyArray<{ name: string; path?: string }>): void {
		skillCatalog = discoverSkills(ctx?.cwd);
		if (Array.isArray(systemPromptSkills)) {
			for (const s of systemPromptSkills) {
				if (!s?.name || !s?.path) continue;
				const key = s.name.toLowerCase();
				if (!skillCatalog.has(key) && fs.existsSync(s.path)) {
					skillCatalog.set(key, {
						name: key,
						skillMdPath: s.path,
						skillDir: path.dirname(s.path),
					});
				}
			}
		}
	}

	function scanSessionEntries(ctx?: ExtensionContext): void {
		const sm = ctx?.sessionManager as
			| {
					getBranch?: () => unknown[];
					getEntries?: () => unknown[];
			  }
			| undefined;
		const rawEntries = sm?.getBranch?.() ?? sm?.getEntries?.() ?? [];
		if (!Array.isArray(rawEntries)) return;

		for (const raw of rawEntries) {
			if (!raw || typeof raw !== "object") continue;
			const entry = raw as {
				type?: string;
				customType?: string;
				data?: { skills?: string[] };
				message?: { role?: string; customType?: string; content?: unknown };
			};

			if (entry.type === "custom" && entry.customType === "skill-slash-expanded-state") {
				for (const s of entry.data?.skills ?? []) {
					if (typeof s === "string") injectedSkills.add(s.toLowerCase());
				}
				continue;
			}

			const msg = entry.message;
			if (!msg || typeof msg !== "object") continue;
			const text = extractTextFromContent(msg.content);

			if (msg.customType === "skill-slash-expanded" || text.includes('<skill-context name="')) {
				for (const m of text.matchAll(/<skill-context name="([^"]+)"/g)) {
					if (m[1]) injectedSkills.add(m[1].toLowerCase());
				}
				continue;
			}

			if (msg.role === "user" && text) {
				for (const skillName of extractSkillTokens(text, skillCatalog)) {
					if (!injectedSkills.has(skillName)) {
						pendingQueue.add(skillName);
					}
				}
			}
		}
	}

	pi.on("session_start", async (_event, ctx) => {
		injectedSkills.clear();
		pendingQueue.clear();
		scannedHistory = false;
		alignGeminiModelRuntimeConfig(ctx);
		sanitizeRegisteredToolsInPlace(pi);
		sanitizeTranscriptSystemTools(ctx);
		refreshCatalog(ctx);
		scanSessionEntries(ctx);
		scannedHistory = true;
	});

	(pi as unknown as { on: (event: string, handler: (event: any, ctx: any) => unknown) => void }).on(
		"before_provider_request",
		async (event, ctx) => {
			alignGeminiModelRuntimeConfig(ctx);
			sanitizeRegisteredToolsInPlace(pi);
			const modelId = typeof ctx?.model?.id === "string" ? ctx.model.id : undefined;
			return sanitizeGeminiChatPayload(event?.payload, modelId);
		},
	);

	(pi as unknown as { on: (event: string, handler: (event: any, ctx: any) => unknown) => void }).on(
		"before_provider_headers",
		async (event, ctx) => {
			const modelId = typeof ctx?.model?.id === "string" ? ctx.model.id : undefined;
			if (!isGeminiModel(modelId) || !isObjectRecord(event?.headers)) {
				return;
			}
			for (const [hk, hv] of Object.entries(COPILOT_GEMINI_DEFAULT_HEADERS)) {
				if (!(hk in event.headers) && !(hk.toLowerCase() in event.headers)) {
					event.headers[hk] = hv;
				}
			}
		},
	);

	pi.on("tool_call", async (event, ctx) => {
		const toolName = (event as { toolName?: string }).toolName;
		if (toolName !== "edit" && toolName !== "write") {
			return undefined;
		}

		if (!isM2DispatcherMainSession()) {
			return undefined;
		}

		const input = (event as { input?: { path?: unknown; file_path?: unknown } }).input;
		const targetPath =
			typeof input?.path === "string"
				? input.path
				: typeof input?.file_path === "string"
					? input.file_path
					: "";

		if (isAllowedM2SpecPath(targetPath, ctx?.cwd)) {
			return undefined;
		}

		if (!injectedSkills.has("m2-dispatcher")) {
			pendingQueue.add("m2-dispatcher");
		}

		if (ctx?.hasUI && ctx.ui?.notify) {
			ctx.ui.notify(`M2-Dispatcher write guard blocked edit to: ${targetPath}`, "warning");
		}
		return {
			block: true,
			reason: M2_SPEC_ONLY_BLOCK_REASON,
		};
	});

	pi.on("input", async (event, ctx) => {
		if ((event as { source?: string }).source === "extension") {
			return { action: "continue" };
		}

		const text = event.text ?? "";
		if (!text.includes("/")) {
			return { action: "continue" };
		}

		refreshCatalog(ctx);
		const matchedSkills = extractSkillTokens(text, skillCatalog);
		if (matchedSkills.length === 0) {
			return { action: "continue" };
		}

		for (const skillName of matchedSkills) {
			if (!injectedSkills.has(skillName)) {
				pendingQueue.add(skillName);
			}
		}

		if (matchedSkills.length === 1) {
			const single = matchedSkills[0];
			const leadingBareRegex = new RegExp(`^(\\s*)\\/${single}(?=\\s|$)`, "i");
			if (leadingBareRegex.test(text)) {
				const transformed = text.replace(leadingBareRegex, `$1/skill:${single}`);
				return { action: "transform", text: transformed };
			}
		}

		return { action: "continue" };
	});

	pi.on("before_agent_start", async (event, ctx) => {
		alignGeminiModelRuntimeConfig(ctx);
		sanitizeRegisteredToolsInPlace(pi);
		sanitizeTranscriptSystemTools(ctx);
		const spSkills = (
			event as {
				systemPromptOptions?: { skills?: ReadonlyArray<{ name: string; path?: string }> };
			}
		).systemPromptOptions?.skills;
		refreshCatalog(ctx, spSkills);

		if (!scannedHistory) {
			scanSessionEntries(ctx);
			scannedHistory = true;
		} else {
			scanSessionEntries(ctx);
		}

		const promptText = typeof event.prompt === "string" ? event.prompt : "";
		if (promptText) {
			for (const [key, meta] of skillCatalog.entries()) {
				if (promptText.includes(meta.skillMdPath) && promptText.includes("# ")) {
					injectedSkills.add(meta.name.toLowerCase());
					pendingQueue.delete(meta.name.toLowerCase());
				} else if (key !== meta.name && promptText.includes(meta.skillDir)) {
					injectedSkills.add(meta.name.toLowerCase());
				}
			}

			for (const skillName of extractSkillTokens(promptText, skillCatalog)) {
				if (!injectedSkills.has(skillName)) {
					pendingQueue.add(skillName);
				}
			}
		}

		const toInject: SkillMetadata[] = [];
		const seenPaths = new Set<string>();
		for (const skillName of pendingQueue) {
			if (injectedSkills.has(skillName)) continue;
			const meta = skillCatalog.get(skillName);
			if (!meta || seenPaths.has(meta.skillMdPath)) continue;
			seenPaths.add(meta.skillMdPath);
			toInject.push(meta);
		}

		if (toInject.length === 0) {
			return undefined;
		}

		const blocks: string[] = [];
		const newlyInjected: string[] = [];
		for (const meta of toInject) {
			const formatted = formatSkillBlock(meta);
			if (formatted) {
				blocks.push(formatted);
				injectedSkills.add(meta.name.toLowerCase());
				pendingQueue.delete(meta.name.toLowerCase());
				newlyInjected.push(meta.name.toLowerCase());
			}
		}

		if (blocks.length === 0) {
			return undefined;
		}

		try {
			pi.appendEntry("skill-slash-expanded-state", {
				skills: [...injectedSkills],
			});
		} catch {
			// Ignore if appendEntry is unavailable in mock/test harness
		}

		const combinedPayload = [
			`The user invoked the following skill(s): ${newlyInjected.map((n) => `/${n}`).join(", ")}. Follow their instructions now.`,
			"",
			...blocks,
		].join("\n\n");

		return {
			message: {
				customType: "skill-slash-expanded",
				content: [{ type: "text", text: combinedPayload }],
				display: false,
			},
		};
	});
}
