from pathlib import Path

from Daily_bot.backtest.sweep_replay_configs import run_sweep
from Daily_bot.tests.test_replay_backtest import _create_db


def test_run_sweep_returns_ranked_rows(tmp_path):
    db_path = tmp_path / "bot.sqlite3"
    _create_db(db_path)

    rows = run_sweep(
        db_path=Path(db_path),
        min_expected_returns=[0.25, 0.5],
        max_spreads=[0.7],
        top_ns=[1],
        take_profit_percent=0.25,
        stop_loss_percents=[6.0],
        use_selected_signals=True,
    )

    assert len(rows) == 2
    assert rows[0]["total_pnl_percent"] >= rows[1]["total_pnl_percent"]
    assert "min_expected_return_percent" in rows[0]
    assert "trades" in rows[0]
