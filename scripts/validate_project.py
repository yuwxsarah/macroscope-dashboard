from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.ensure_data_files import SCHEMAS


def main() -> None:
    errors: list[str] = []
    warnings: list[str] = []
    summary: dict[str, dict] = {}

    # Validate every declared CSV. Missing/invalid datasets are reported,
    # but they do not block deployment because public sources can fail
    # temporarily and the dashboard is designed to retain cached data.
    for filename, required_columns in SCHEMAS.items():
        path = ROOT / "data" / filename

        if not path.exists():
            warnings.append(f"missing optional dataset: data/{filename}")
            summary[filename] = {"rows": 0, "columns": [], "status": "missing"}
            continue

        try:
            dataframe = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            warnings.append(f"empty dataset: data/{filename}")
            summary[filename] = {"rows": 0, "columns": [], "status": "empty"}
            continue
        except Exception as exc:
            warnings.append(f"unreadable dataset data/{filename}: {exc}")
            summary[filename] = {"rows": 0, "columns": [], "status": "unreadable"}
            continue

        missing_columns = [
            column for column in required_columns if column not in dataframe.columns
        ]
        if missing_columns:
            warnings.append(
                f"data/{filename} missing columns: {missing_columns}"
            )

        row_count = len(dataframe)
        status = "ok"
        if row_count == 0:
            status = "waiting_for_update"
            if filename == "a_share_universe.csv":
                warnings.append(
                    "data/a_share_universe.csv has 0 rows; this is normal "
                    "before the crowding-history backfill runs."
                )
            else:
                warnings.append(
                    f"data/{filename} has 0 rows and is waiting for an online update."
                )

        summary[filename] = {
            "rows": row_count,
            "columns": list(dataframe.columns),
            "status": status,
        }

    # Only files required to render and deploy the already-built website
    # are treated as blocking errors.
    required_runtime_files = [
        "scripts/update_data.py",
        "scripts/build_site.py",
        "src/pipeline.py",
        "src/providers.py",
        "src/extended_providers.py",
        "public/index.html",
    ]
    for relative_path in required_runtime_files:
        if not (ROOT / relative_path).exists():
            errors.append(f"missing required runtime file: {relative_path}")

    # Workflow files are useful, but absence of the optional backfill workflow
    # must not block publication of the main dashboard.
    optional_files = [
        ".github/workflows/update-and-deploy.yml",
        ".github/workflows/backfill-crowding.yml",
    ]
    for relative_path in optional_files:
        if not (ROOT / relative_path).exists():
            warnings.append(f"missing optional workflow file: {relative_path}")

    messages_path = ROOT / "data" / "messages.json"
    if not messages_path.exists():
        warnings.append("missing optional messages feed: data/messages.json")
    else:
        try:
            messages = json.loads(messages_path.read_text(encoding="utf-8"))
            if not isinstance(messages.get("items"), list):
                warnings.append("data/messages.json has no items list")
            if not isinstance(messages.get("sources"), list):
                warnings.append("data/messages.json has no sources list")
        except Exception as exc:
            errors.append(f"unreadable messages feed data/messages.json: {exc}")

    tracking_path = ROOT / "data" / "market_tracking.json"
    if not tracking_path.exists():
        warnings.append("missing optional market tracking model: data/market_tracking.json")
    else:
        try:
            tracking = json.loads(tracking_path.read_text(encoding="utf-8"))
            if not isinstance(tracking.get("tracking"), dict):
                warnings.append("data/market_tracking.json has no tracking object")
            if not isinstance(tracking.get("framework_sections"), list):
                warnings.append("data/market_tracking.json has no framework_sections list")
        except Exception as exc:
            errors.append(f"unreadable market tracking model data/market_tracking.json: {exc}")

    result = {
        "ok": not errors,
        "deployment_allowed": not errors,
        "errors": errors,
        "warnings": warnings,
        "data": summary,
    }

    manifest_path = ROOT / "data" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
