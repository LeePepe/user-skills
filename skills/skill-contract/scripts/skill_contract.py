#!/usr/bin/env python3
"""Scaffold and audit progressive-disclosure skill packages."""
from __future__ import annotations
import argparse, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PRIVATE = re.compile(r"\b(?:progressRail|isUnilateral|DashboardL10n)\b")

def package_roots():
    roots = [ROOT / "skills"]
    for candidate in (ROOT / "Skills" / "skills" / "meta", ROOT / "Skills" / "skills" / "repo"):
        if candidate.is_dir(): roots.append(candidate)
    return roots

def init_skill(args):
    target = ROOT / "skills" / args.skill
    target.mkdir(parents=True, exist_ok=True)
    (target / ".claude-plugin").mkdir(exist_ok=True)
    (target / "references").mkdir(exist_ok=True)
    (target / "scripts").mkdir(exist_ok=True)
    design = " Design skills use an 80-line L0 budget." if args.design else ""
    (target / "SKILL.md").write_text(f"---\nname: {args.skill}\ndescription: \\\"{args.skill} skill.\\\"\n---\n\n# {args.skill}\n\nProgressive disclosure entry point for the {args.category} category.{design}\n\nRead references on demand; keep deterministic checks in scripts.\n", encoding="utf-8")
    (target / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name":args.skill,"description":f"{args.skill} skill","version":"1.0.0"}, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"ok":True,"skill":args.skill,"path":str(target)}))
    return 0

def sink_rule(args):
    target = ROOT / "skills" / args.skill
    if not (target / "SKILL.md").is_file():
        raise SystemExit(f"skill not found: {args.skill}")
    if args.kind == "case-card":
        ref = target / "references" / "defect-cases.json"
        doc = json.loads(ref.read_text()) if ref.exists() else {"cases":[]}
        ident = args.id or f"DC-{len(doc['cases'])+1:03d}"
        doc["cases"].append({"id":ident,"platforms":args.platforms.split(","),"dimensions":args.dimensions.split(","),"containers":args.containers.split(",")})
        ref.parent.mkdir(exist_ok=True); ref.write_text(json.dumps(doc, indent=2)+"\n")
    else:
        script = target / "scripts" / "design_contract.py"
        script.parent.mkdir(exist_ok=True)
        if not script.exists(): script.write_text("#!/usr/bin/env python3\n\"\"\"Deterministic contract gate sink.\"\"\"\n")
    print(json.dumps({"ok":True,"skill":args.skill,"kind":args.kind}))
    return 0

def audit_package(path, design=False):
    findings=[]; skill=path.name; md=path/"SKILL.md"
    if not md.exists(): return [{"skill":skill,"code":"missing-skill"}]
    lines=md.read_text(encoding="utf-8").splitlines()
    cap=80 if design or skill in {"my-designer","design-studio","visual-design-modernization"} else 180
    if len(lines)>cap: findings.append({"skill":skill,"code":"l0-lines","value":len(lines),"cap":cap})
    if md.stat().st_size>16*1024: findings.append({"skill":skill,"code":"l0-bytes"})
    text=md.read_text(encoding="utf-8")
    if PRIVATE.search(text): findings.append({"skill":skill,"code":"private-identifier"})
    for ref in (path/"references").glob("*") if (path/"references").is_dir() else []:
        if ref.suffix not in {".md",".json"}: continue
        if ref.suffix==".md" and len(ref.read_text(encoding="utf-8").splitlines())>160: findings.append({"skill":skill,"code":"l1-lines","file":str(ref)})
    return findings

def audit(args):
    packages=[]
    if args.skill:
        packages=[ROOT/"skills"/args.skill]
    else:
        packages=[p for root in package_roots() if root.is_dir() for p in root.iterdir() if p.is_dir() and (p/"SKILL.md").exists()]
    findings=[]; names={}
    for p in packages:
        names.setdefault(p.name,[]).append(str(p)); findings.extend(audit_package(p))
    findings.extend({"code":"duplicate-skill","name":n,"paths":ps} for n,ps in names.items() if len(ps)>1)
    result={"ok":not findings,"skills":len(packages),"findings":findings}
    print(json.dumps(result, indent=2))
    return 1 if findings and args.strict else 0

def parser():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="command",required=True)
    i=sub.add_parser("init"); i.add_argument("--skill",required=True); i.add_argument("--category",required=True); i.add_argument("--design",action="store_true")
    s=sub.add_parser("sink-rule"); s.add_argument("--skill",required=True); s.add_argument("--kind",choices=["regex-gate","case-card","mental-model"],required=True); s.add_argument("--id"); s.add_argument("--platforms",default="desktop,mobile,web"); s.add_argument("--dimensions",default=""); s.add_argument("--containers",default="")
    a=sub.add_parser("audit"); g=a.add_mutually_exclusive_group(required=True); g.add_argument("--all",action="store_true"); g.add_argument("--skill"); a.add_argument("--json",action="store_true"); a.add_argument("--strict",action="store_true")
    return p

def main():
    args=parser().parse_args(); return {"init":init_skill,"sink-rule":sink_rule,"audit":audit}[args.command](args)
if __name__ == "__main__": raise SystemExit(main())
