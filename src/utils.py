from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PUBLIC_DIR = ROOT / "public"
SETTINGS_PATH = ROOT / "config" / "settings.yaml"


def load_settings() -> dict[str, Any]:
    with SETTINGS_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def pick_column(df: pd.DataFrame, candidates: Iterable[str], contains: Iterable[str] | None = None) -> str | None:
    columns = [str(c) for c in df.columns]
    for candidate in candidates:
        if candidate in columns:
            return candidate
    if contains:
        for col in columns:
            if all(token.lower() in col.lower() for token in contains):
                return col
    return None


def month_key(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    # Prefer explicit year/month patterns such as 2024年03月份 or 2024-03-09.
    match = re.search(r"(19|20)\d{2}\D{0,3}(0?[1-9]|1[0-2])", text)
    if match:
        year = match.group(0)[:4]
        month_match = re.search(r"(0?[1-9]|1[0-2])", match.group(0)[4:])
        if month_match:
            return f"{year}{int(month_match.group(1)):02d}"
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 6:
        return digits[:6]
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y%m")


def date_key(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        text = "".join(ch for ch in str(value) if ch.isdigit())
        return text[:8] if len(text) >= 8 else None
    return parsed.strftime("%Y%m%d")


def safe_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def dataframe_to_records(df: pd.DataFrame, max_rows: int | None = None) -> list[dict[str, Any]]:
    if max_rows is not None and len(df) > max_rows:
        df = df.tail(max_rows)
    clean = df.replace({np.nan: None, pd.NA: None})
    records = clean.to_dict(orient="records")
    for record in records:
        for key, value in list(record.items()):
            if isinstance(value, (np.integer,)):
                record[key] = int(value)
            elif isinstance(value, (np.floating,)):
                record[key] = safe_float(value)
            elif isinstance(value, (pd.Timestamp, datetime)):
                record[key] = value.isoformat()
    return records


def write_csv_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(temp, index=False, encoding="utf-8-sig")
    temp.replace(path)


def read_csv_safe(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=columns or [])
    try:
        return pd.read_csv(path, dtype={"month": str, "trade_date": str, "founded_date": str, "fund_code": str})
    except Exception:
        return pd.DataFrame(columns=columns or [])


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
