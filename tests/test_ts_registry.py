"""The generated TypeScript registry data stays fresh.

``ts/src/registry.data.ts`` is derived from ``registry.json`` (plus the
aim-note template) by ``scripts/gen_ts_registry.py``; hand edits or a
registry change without regeneration must fail here.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_ts_registry_data_is_fresh():
    spec = importlib.util.spec_from_file_location(
        "gen_ts_registry", ROOT / "scripts" / "gen_ts_registry.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_ts_registry"] = module
    spec.loader.exec_module(module)
    committed = (ROOT / "ts" / "src" / "registry.data.ts").read_text("utf-8")
    assert module.render() == committed, (
        "ts/src/registry.data.ts is stale — run scripts/gen_ts_registry.py"
    )
