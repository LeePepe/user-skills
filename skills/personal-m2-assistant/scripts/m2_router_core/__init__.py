"""Progressive-disclosure implementation loader for personal-m2-assistant.

The implementation is split into bounded source drawers but executed in the
facade namespace so legacy monkey-patching remains fully compatible.
"""
from pathlib import Path


def load_into(namespace: dict[str, object]) -> None:
    root = Path(__file__).parent
    for path in sorted(root.glob("part_*.py")):
        source = path.read_text(encoding="utf-8")
        code = compile(source, str(path), "exec")
        exec(code, namespace, namespace)
