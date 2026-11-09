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
	"M2-Dispatcher Rule: M2 is only allowed to edit simple spec files (spec.md / design.md). Code and other files must be modified in a worktree by dispatching Herdr TL via herdr-dispatch.sh.";

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
		refreshCatalog(ctx);
		scanSessionEntries(ctx);
		scannedHistory = true;
	});

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
