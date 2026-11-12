"""Export the backend schema without requiring a running API server."""

import json
from pathlib import Path

from autobench.api.main import app

destination = Path(__file__).resolve().parents[1] / "frontend" / "types" / "openapi.json"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(app.openapi()), encoding="utf-8")
print(f"Exported {destination.name}")
