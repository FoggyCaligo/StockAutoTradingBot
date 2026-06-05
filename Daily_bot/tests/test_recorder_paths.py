from pathlib import Path

from Daily_bot.storage.db import Recorder


def test_recorder_migrates_legacy_logs_into_default_log_dir(tmp_path):
    legacy_dir = tmp_path / "Daily_bot" / "logs"
    legacy_dir.mkdir(parents=True)
    legacy_csv = legacy_dir / "market_traces_20260605.csv"
    legacy_csv.write_text("col_a,col_b\n1,2\n", encoding="utf-8-sig")

    recorder = Recorder(tmp_path / "bot.sqlite3")

    migrated_csv = tmp_path / "logs" / "market_traces_20260605.csv"
    assert migrated_csv.exists()
    assert "1,2" in migrated_csv.read_text(encoding="utf-8-sig")
    assert not legacy_csv.exists()
    recorder.conn.close()


def test_recorder_merges_legacy_csv_rows_when_target_already_exists(tmp_path):
    target_dir = tmp_path / "logs"
    target_dir.mkdir(parents=True)
    target_csv = target_dir / "orders_20260605.csv"
    target_csv.write_text("ticker,price\nAAA,1000\n", encoding="utf-8-sig")

    legacy_dir = tmp_path / "Daily_bot" / "logs"
    legacy_dir.mkdir(parents=True)
    legacy_csv = legacy_dir / "orders_20260605.csv"
    legacy_csv.write_text("ticker,price\nBBB,2000\n", encoding="utf-8-sig")

    recorder = Recorder(tmp_path / "bot.sqlite3")

    merged_text = target_csv.read_text(encoding="utf-8-sig")
    assert "AAA,1000" in merged_text
    assert "BBB,2000" in merged_text
    assert merged_text.count("ticker,price") == 1
    assert not legacy_csv.exists()
    recorder.conn.close()
