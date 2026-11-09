#!/usr/bin/env bash
# Install and sync `skill-slash-expander.ts` into both native Pi CLI (`~/.pi/agent`)
# and Cindy's managed package store (`CindyGlobal/pi-package-home`), recomputing
# `cindy-pi-package-fingerprint-v1` and cleaning legacy global dotfile instruction copies.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_SRC="$SCRIPT_DIR/skill-slash-expander.ts"

if [[ ! -f "$EXT_SRC" ]]; then
  echo "error: missing source extension at $EXT_SRC" >&2
  exit 1
fi

PI_AGENT_DIR="${PI_AGENT_DIR:-$HOME/.pi/agent}"
CINDY_PKG_HOME="${CINDY_PKG_HOME:-$HOME/Library/Application Support/CindyGlobal/pi-package-home}"
CINDY_PKG_DIR="$CINDY_PKG_HOME/cindy-skill-slash-expander"

mkdir -p "$PI_AGENT_DIR/extensions"
cp "$EXT_SRC" "$PI_AGENT_DIR/extensions/skill-slash-expander.ts"

# Clean legacy standalone global dotfile instruction copies (routed via repo AGENTS.md -> user-skills)
rm -f "$HOME/.claude/CLAUDE.md" \
      "$HOME/.codex/AGENTS.md" \
      "$HOME/.copilot/copilot-instructions.md" \
      "$PI_AGENT_DIR/AGENTS.md"

if [[ -d "$CINDY_PKG_HOME" ]]; then
  mkdir -p "$CINDY_PKG_DIR/extensions" "$CINDY_PKG_DIR/prompts"
  cp "$EXT_SRC" "$CINDY_PKG_DIR/extensions/skill-slash-expander.ts"

  node - "$CINDY_PKG_HOME" "$CINDY_PKG_DIR" <<'NODE_EOF'
const fs = require("fs");
const crypto = require("crypto");
const path = require("path");

const [pkgHome, pkgDir] = process.argv.slice(2);

function updateField(hash, value) {
  const buf = Buffer.from(value, "utf8");
  hash.update(`${buf.length}:`);
  hash.update(buf);
}

function fingerprintPiPackageTree(rootDir) {
  const canonicalRoot = fs.realpathSync(rootDir);
  const hash = crypto.createHash("sha256");
  updateField(hash, "cindy-pi-package-fingerprint-v1");
  function walk(absPath, relPath) {
    const stat = fs.statSync(absPath);
    const label = relPath || ".";
    if (stat.isDirectory()) {
      updateField(hash, `directory:${label}:${stat.mode & 511}`);
      const entries = fs.readdirSync(absPath).sort();
      for (const name of entries) {
        walk(path.join(absPath, name), relPath ? path.join(relPath, name) : name);
      }
      return;
    }
    if (stat.isFile()) {
      updateField(hash, `file:${label}:${stat.mode & 511}:${stat.size}`);
      hash.update(fs.readFileSync(absPath));
    }
  }
  walk(canonicalRoot, "");
  return hash.digest("hex");
}

const digest = fingerprintPiPackageTree(pkgDir);
const statePath = path.join(pkgHome, "cindy-package-state.json");
const sources = [
  "./cindy-skill-slash-expander",
  pkgDir,
  "cindy-skill-slash-expander",
];
const state = {
  version: 4,
  disabledSources: [],
  approvedExtensionSources: sources,
  approvedExtensionFingerprints: Object.fromEntries(sources.map((s) => [s, digest])),
  snapshotUnavailablePackages: [],
};
fs.writeFileSync(statePath, JSON.stringify(state, null, 2) + "\n", "utf8");

const token = `runtime:${Date.now()}-${process.pid}-${crypto.randomUUID()}\n`;
for (const name of ["cindy-package-change-token", "cindy-package-runtime-change-token"]) {
  fs.writeFileSync(path.join(pkgHome, name), token, { encoding: "utf8", mode: 0o600 });
}
console.log(`Updated Cindy package fingerprint: ${digest}`);
NODE_EOF
fi

echo "Installed skill-slash-expander.ts to $PI_AGENT_DIR/extensions and $CINDY_PKG_DIR/extensions"
