"""Export the machine-readable contracts (JSON Schema + OpenAPI) to ``schemas/``.

CI regenerates these and fails on drift, so the committed files are always authoritative for
editors (YAML schema hints), the web client (generated TS types) and external SDKs.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from catmod.config import PortfolioConfig


def _dump(obj: dict[str, Any], path: Path) -> Path:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    return path


def export_all(out_dir: Path) -> list[Path]:
    from catmod.api.app import create_app  # lazy: keeps config import light

    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_schema = PortfolioConfig.model_json_schema()
    cfg_schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    cfg_schema["$id"] = (
        "https://github.com/rohitranjan1812/cat-modelling/schemas/portfolio-config.schema.json"
    )
    with tempfile.TemporaryDirectory() as tmp:
        openapi = create_app(data_dir=Path(tmp)).openapi()
    from catmod.engine.runner import AnalysisConfig

    an_schema = AnalysisConfig.model_json_schema()
    an_schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return [
        _dump(cfg_schema, out_dir / "portfolio-config.schema.json"),
        _dump(an_schema, out_dir / "analysis-config.schema.json"),
        _dump(openapi, out_dir / "openapi.json"),
    ]
