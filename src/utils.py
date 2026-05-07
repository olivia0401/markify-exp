import time
from pathlib import Path
from typing import Any, Union

import pandas as pd


def safe_to_csv(
    df: pd.DataFrame,
    path: Union[str, Path],
    *,
    mode: str,
    header: bool,
    max_retries: int = 6,
) -> None:
    delay = 0.5
    for attempt in range(max_retries):
        try:
            df.to_csv(path, mode=mode, header=header, index=False)
            return
        except PermissionError:
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)
            delay *= 2


def normalize_value(v: Any) -> str:
    if isinstance(v, (int, float)):
        return str(int(v))
    return str(v).strip()


def parse_databases(s: str) -> list[str]:
    return [d.strip() for d in s.split(",") if d.strip()]
