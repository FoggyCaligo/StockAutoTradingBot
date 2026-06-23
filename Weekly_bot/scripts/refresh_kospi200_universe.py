from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bot.data.universe_provider import DEFAULT_KOSPI200_CACHE, DEFAULT_KOSPI200_CSV, get_kospi200_list


def main() -> None:
    universe = get_kospi200_list(
        csv_path=str(DEFAULT_KOSPI200_CSV),
        cache_path=str(DEFAULT_KOSPI200_CACHE),
        source="KOSPI200",
        refresh_daily=True,
    )
    output_path = ROOT / DEFAULT_KOSPI200_CSV
    output_path.parent.mkdir(parents=True, exist_ok=True)
    universe.to_csv(output_path, index=False, encoding="utf-8")

    codes = universe["Code"].astype(str).str.strip().str.replace(".KS", "", regex=False).str.replace(".KQ", "", regex=False).str.zfill(6)
    print(f"rows={len(universe)} unique_codes={codes.nunique()} output={output_path}")


if __name__ == "__main__":
    main()
