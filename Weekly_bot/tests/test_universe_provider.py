from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bot.data import universe_provider


def test_get_kospi200_list_rejects_invalid_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "kospi200_latest.csv"
    pd.DataFrame({"Code": [str(idx).zfill(6) for idx in range(946)]}).to_csv(cache_path, index=False)

    fallback_path = tmp_path / "kospi200.csv"
    pd.DataFrame({"Code": [str(idx).zfill(6) for idx in range(200)]}).to_csv(fallback_path, index=False)

    monkeypatch.setattr(universe_provider, "_cache_is_today", lambda path: True)
    monkeypatch.setattr(universe_provider, "_load_fdr_listing", lambda source: (_ for _ in ()).throw(RuntimeError("blocked")))

    df = universe_provider.get_kospi200_list(
        csv_path=str(fallback_path),
        cache_path=str(cache_path),
        refresh_daily=True,
    )

    assert len(df) == 200
