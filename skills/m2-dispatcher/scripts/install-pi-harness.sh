#!/usr/bin/env bash
# Install and sync `skill-slash-expander.ts` into both native Pi CLI (`~/.pi/agent`)
# and Cindy's managed package store (`CindyGlobal/pi-package-home`), recomputing
# `cindy-pi-package-fingerprint-v1`. Never modifies any global AGENTS.md / CLAUDE.md files.
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

mkdir -p "$PI_AGENT_DIR/extensions" "$PI_AGENT_DIR/prompts"
cp "$EXT_SRC" "$PI_AGENT_DIR/extensions/skill-slash-expander.ts"

write_shortcut_prompts() {
  local prompts_dir="$1"
  mkdir -p "$prompts_dir"

  cat > "$prompts_dir/m2.md" <<'EOF'
---
description: "Route a cross-M2 instruction through m2-router.py --plan, Owner approval, then --apply --approve."
argument-hint: "<cross_m2_instruction>"
---
/m2 $@
EOF

  cat > "$prompts_dir/status.md" <<'EOF'
---
description: "Run goal-supervisor.py status --check and display the 'needs a person' pinned status view."
argument-hint: "[optional filter or goal]"
---
/status $@
EOF

  cat > "$prompts_dir/ingest.md" <<'EOF'
---
description: "Route inspiration text or image to design-studio (Entry A) design_catalog.py vault-ingest (auto-adds --measure-palette for images)."
argument-hint: "<text_or_image>"
---
/ingest $@
EOF

  cat > "$prompts_dir/learn.md" <<'EOF'
---
description: "Route topic or URL to knowledge-digest dual-channel digest, archive card, and sync knowledge-vault/index.md."
argument-hint: "<topic_or_url>"
---
/learn $@
EOF

  cat > "$prompts_dir/dash.md" <<'EOF'
---
description: "Trigger ~/Development/AIDash/aidata refresh and push today's AIDash briefing (python3 cli.py digest --date today --aidash)."
argument-hint: "[optional context]"
---
/dash $@
EOF

  cat > "$prompts_dir/retro.md" <<'EOF'
---
description: "Run retro-loop retro_story_engine.py agent-efficiency --auto-propose and discover."
argument-hint: "[optional scope]"
---
/retro $@
EOF
}

write_shortcut_prompts "$PI_AGENT_DIR/prompts"

if [[ -d "$CINDY_PKG_HOME" ]]; then
  mkdir -p "$CINDY_PKG_DIR/extensions" "$CINDY_PKG_DIR/prompts"
  cp "$EXT_SRC" "$CINDY_PKG_DIR/extensions/skill-slash-expander.ts"
  write_shortcut_prompts "$CINDY_PKG_DIR/prompts"

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
