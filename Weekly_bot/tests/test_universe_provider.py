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


def test_get_kospi200_list_can_rebuild_from_kospi_listing(tmp_path, monkeypatch):
    cache_path = tmp_path / "kospi200_latest.csv"
    pd.DataFrame({"Code": [str(idx).zfill(6) for idx in range(946)]}).to_csv(cache_path, index=False)

    fallback_path = tmp_path / "kospi200.csv"
    pd.DataFrame({"Code": [str(idx).zfill(6) for idx in range(200)]}).to_csv(fallback_path, index=False)

    broad_listing = pd.DataFrame(
        {
            "Code": [str(idx).zfill(6) for idx in range(300)],
            "Name": [f"Name-{idx}" for idx in range(300)],
        }
    )

    monkeypatch.setattr(universe_provider, "_cache_is_today", lambda path: False)

    def _fake_load(source: str):
        if source == "KOSPI200":
            raise RuntimeError("unsupported")
        if source == "KOSPI":
            return broad_listing
        raise AssertionError(source)

    monkeypatch.setattr(universe_provider, "_load_fdr_listing", _fake_load)

    df = universe_provider.get_kospi200_list(
        csv_path=str(fallback_path),
        cache_path=str(cache_path),
        refresh_daily=True,
    )

    assert len(df) == 200
    assert set(df["Code"]) == {str(idx).zfill(6) for idx in range(200)}
