from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from bot.config import StrategyConfig
from bot.data.historical_provider import HistoricalKrxDataProvider
from bot.models import MarketSnapshot, OrderIntent
from bot.risk.position_sizing import EqualWeightPositionSizer
from bot.strategy.weekly_pullback import WeeklyPullbackStrategy
from bot.utils import discounted_limit_price
MIN_EXPECTED_KOSPI200_SIZE = 180
MAX_EXPECTED_KOSPI200_SIZE = 230


def _get_tick_size(price: int) -> int:
    if price < 1000:
        return 1
    if price < 5000:
        return 5
    if price < 10000:
        return 10
    if price < 50000:
        return 50
    if price < 100000:
        return 100
    if price < 500000:
        return 500
    return 1000


@dataclass(frozen=True)
class BacktestSettings:
    start: str
    end: str
    initial_cash: int
    data_source: str = "auto"
    signal_weekday: str = "friday"
    entry_offset_trading_days: int = 1
    liquidation_offset_trading_days: int = 0
    signal_price_basis: str = "previous_close"
    approximate_monday_10am: bool = False
    monday_approx_price_mode: str = "open"
    monday_approx_max_gap_pct: float = 2.0
    collision_take_profit_ratio: float = 0.75
    buy_slippage_bps: float = 0.0
    sell_slippage_bps: float = 0.0
    buy_fee_bps: float = 0.0
    sell_fee_bps: float = 0.0
    sell_tax_bps: float = 0.0
    entry_trigger_change_pct: float = -2.0
    use_entry_cost_aware_sizing: bool = False
    use_historical_universe: bool = True
    historical_universe_index_ticker: str = "1028"
    require_historical_universe: bool = False
    output_dir: str | Path = "logs/backtests"
    run_name: str | None = None


@dataclass(frozen=True)
class BacktestArtifacts:
    summary: pd.DataFrame
    trades: pd.DataFrame
    weekly: pd.DataFrame
    monthly: pd.DataFrame
    output_dir: Path


class WeeklyBacktester:
    def __init__(self, config: StrategyConfig, settings: BacktestSettings):
        self.config = config
        self.settings = settings
        self.strategy = WeeklyPullbackStrategy(config)
        self.sizer = EqualWeightPositionSizer(config)
        self.base_output_dir = Path(settings.output_dir)
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir = self.base_output_dir / self._build_run_name()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._historical_universe_cache: dict[str, set[str]] = {}
        self._historical_universe_sources: dict[str, str] = {}
        self._historical_universe_counts: dict[str, int] = {"local": 0, "remote": 0, "fallback": 0}
        self._fallback_universe_codes: set[str] = set()

    def run(self) -> BacktestArtifacts:
        provider = HistoricalKrxDataProvider(
            source=self.settings.data_source,
            listing_market="KOSPI" if self.settings.use_historical_universe else "KOSPI200",
        )
        market_data = provider.load(start=self.settings.start, end=self.settings.end)

        listing_by_code = self._listing_by_code(market_data.listing)
        self._fallback_universe_codes = self._load_fallback_universe_codes(listing_by_code)
        prepared = {
            code: self._prepare_history(code, history, listing_by_code.get(code, {}))
            for code, history in market_data.histories.items()
        }
        trading_dates = self._trading_dates(prepared)

        cash = float(self.settings.initial_cash)
        trade_rows: list[dict[str, object]] = []
        weekly_rows: list[dict[str, object]] = []

        start_ts = pd.Timestamp(self.settings.start)
        end_ts = pd.Timestamp(self.settings.end)
        signal_dates = [dt for dt in trading_dates if dt.weekday() == self._signal_weekday_index() and start_ts <= dt <= end_ts]
        for signal_date in signal_dates:
            week_result = self._run_week(signal_date, trading_dates, prepared, cash)
            if week_result is None:
                continue
            cash = week_result["ending_cash"]
            trade_rows.extend(week_result["trade_rows"])
            weekly_rows.append(week_result["weekly_row"])

        trades_df = pd.DataFrame(trade_rows)
        weekly_df = pd.DataFrame(weekly_rows)
        monthly_df = self._build_monthly_summary(weekly_df)
        summary_df = self._build_summary(trades_df, weekly_df, cash)
        self._write_outputs(summary_df, trades_df, weekly_df, monthly_df)
        return BacktestArtifacts(summary=summary_df, trades=trades_df, weekly=weekly_df, monthly=monthly_df, output_dir=self.output_dir)

    def _build_snapshots_for_date(
        self,
        date_key: pd.Timestamp,
        histories: dict[str, pd.DataFrame],
        universe_codes: set[str],
        entry_date: pd.Timestamp | None = None,
    ) -> tuple[list[MarketSnapshot], dict[str, str]]:
        snapshots: list[MarketSnapshot] = []
        signal_modes: dict[str, str] = {}
        for code, history in histories.items():
            if date_key not in history.index:
                continue
            row = history.loc[date_key]
            if any(pd.isna(row.get(column)) for column in ("ma20", "ma30", "ma30_prev", "ma50", "ma50_prev", "ma120", "ma120_prev")):
                continue
            current_price = int(row["Close"])
            change_pct = float(row["change_pct"])
            signal_mode = "fallback"
            if entry_date is not None and entry_date in history.index:
                entry_row = history.loc[entry_date]
                approx_price = self._approximate_signal_price(float(row["Close"]), float(entry_row["Open"]))
                prev_close = float(row["Close"])
                approx_change_pct = ((approx_price / prev_close) - 1.0) * 100.0 if prev_close > 0 else 0.0
                if self._is_monday_approx_reliable(row, approx_change_pct):
                    current_price = approx_price
                    change_pct = approx_change_pct
                    signal_mode = "approx"
            tick_size = _get_tick_size(current_price)
            snapshots.append(
                MarketSnapshot(
                    code=code,
                    name=str(row["name"]),
                    is_kospi200=code in universe_codes,
                    market_cap_krw=int(row["market_cap_krw"]),
                    current_price=current_price,
                    change_pct=change_pct,
                    turnover_krw=int(row["turnover_krw"]),
                    volume=int(row["Volume"]),
                    ma20=float(row["ma20"]),
                    ma30=float(row["ma30"]),
                    ma30_prev=float(row["ma30_prev"]),
                    ma50=float(row["ma50"]),
                    ma50_prev=float(row["ma50_prev"]),
                    ma120=float(row["ma120"]),
                    ma120_prev=float(row["ma120_prev"]),
                    bid_price_1=current_price - tick_size,
                    ask_price_1=current_price + tick_size,
                    tick_size=tick_size,
                )
            )
            signal_modes[code] = signal_mode
        return snapshots, signal_modes

    def _simulate_exit(
        self,
        code: str,
        name: str,
        history: pd.DataFrame,
        entry_date: pd.Timestamp,
        week_end: pd.Timestamp,
        entry_price: float,
        quantity: int,
        collision_mode: str = "fallback",
    ) -> dict[str, object]:
        tp_price = entry_price * (1.0 + self.config.take_profit_pct / 100.0)
        sl_price = entry_price * (1.0 + self.config.stop_loss_pct / 100.0)

        window = history.loc[(history.index >= entry_date) & (history.index <= week_end)]
        if window.empty:
            window = history.loc[[entry_date]]

        exit_price = float(window.iloc[-1]["Close"])
        exit_date = window.index[-1]
        exit_reason = "friday_liquidation" if exit_date.weekday() == 4 else "extended_liquidation"

        for current_date, bar in window.iterrows():
            open_price = float(bar["Open"])
            low = float(bar["Low"])
            high = float(bar["High"])
            if open_price <= sl_price:
                exit_price = open_price
                exit_date = current_date
                exit_reason = "stop_loss_gap_open"
                break
            if open_price >= tp_price:
                exit_price = open_price
                exit_date = current_date
                exit_reason = "take_profit_gap_open"
                break
            if low <= sl_price and high >= tp_price:
                if collision_mode == "approx":
                    close_price = float(bar["Close"])
                    if close_price >= entry_price:
                        exit_price = tp_price
                        exit_reason = "take_profit_same_day_collision_approx"
                    else:
                        exit_price = sl_price
                        exit_reason = "stop_loss_same_day_collision_approx"
                else:
                    if self._collision_prefers_take_profit(code, entry_date, current_date):
                        exit_price = tp_price
                        exit_reason = "take_profit_same_day_collision_fallback"
                    else:
                        exit_price = sl_price
                        exit_reason = "stop_loss_same_day_collision_fallback"
                exit_date = current_date
                break
            if low <= sl_price:
                exit_price = sl_price
                exit_date = current_date
                exit_reason = "stop_loss"
                break
            if high >= tp_price:
                exit_price = tp_price
                exit_date = current_date
                exit_reason = "take_profit"
                break

        exit_price = self._apply_sell_costs(exit_price)
        gross_proceeds = exit_price * quantity
        sell_fee = gross_proceeds * self.settings.sell_fee_bps / 10_000.0
        sell_tax = gross_proceeds * self.settings.sell_tax_bps / 10_000.0
        net_proceeds = gross_proceeds - sell_fee - sell_tax

        return {
            "code": code,
            "name": name,
            "exit_price": exit_price,
            "exit_date": exit_date.date().isoformat(),
            "exit_reason": exit_reason,
            "holding_days": self._trading_day_distance(entry_date, exit_date),
            "sell_fee": sell_fee,
            "sell_tax": sell_tax,
            "net_proceeds": net_proceeds,
        }

    def _build_monthly_summary(self, weekly_df: pd.DataFrame) -> pd.DataFrame:
        if weekly_df.empty:
            return pd.DataFrame(columns=["month", "start_cash", "end_cash", "pnl_krw", "pnl_pct", "weeks"])

        df = weekly_df.copy()
        df["month"] = pd.to_datetime(df["week_start"]).dt.to_period("M").astype(str)
        monthly = (
            df.groupby("month", as_index=False)
            .agg(
                start_cash=("start_cash", "first"),
                end_cash=("end_cash", "last"),
                pnl_krw=("pnl_krw", "sum"),
                weeks=("week_start", "count"),
            )
            .sort_values("month")
        )
        monthly["pnl_pct"] = ((monthly["end_cash"] / monthly["start_cash"]) - 1.0) * 100.0
        monthly["pnl_pct"] = monthly["pnl_pct"].round(4)
        return monthly

    def _build_summary(self, trades_df: pd.DataFrame, weekly_df: pd.DataFrame, ending_cash: float) -> pd.DataFrame:
        initial_cash = float(self.settings.initial_cash)
        total_return_pct = ((ending_cash / initial_cash) - 1.0) * 100.0 if initial_cash > 0 else 0.0
        win_rate = float((trades_df["pnl_krw"] > 0).mean() * 100.0) if not trades_df.empty else 0.0
        avg_trade_pct = float(trades_df["pnl_pct"].mean()) if not trades_df.empty else 0.0
        avg_monthly_pct = float(weekly_df["pnl_pct"].mean()) if not weekly_df.empty else 0.0
        max_drawdown_pct = self._max_drawdown_pct(weekly_df)
        historical_universe_dates = sum(self._historical_universe_counts.values())
        historical_universe_fallback_pct = (
            self._historical_universe_counts["fallback"] / historical_universe_dates * 100.0
            if historical_universe_dates > 0
            else 0.0
        )

        return pd.DataFrame(
            [
                {
                    "start": self.settings.start,
                    "end": self.settings.end,
                    "initial_cash": round(initial_cash, 2),
                    "ending_cash": round(ending_cash, 2),
                    "total_return_pct": round(total_return_pct, 4),
                    "weeks": int(len(weekly_df)),
                    "trades": int(len(trades_df)),
                    "win_rate_pct": round(win_rate, 4),
                    "avg_trade_pct": round(avg_trade_pct, 4),
                    "avg_weekly_pct": round(avg_monthly_pct, 4),
                    "max_drawdown_pct": round(max_drawdown_pct, 4),
                    "buy_slippage_bps": self.settings.buy_slippage_bps,
                    "sell_slippage_bps": self.settings.sell_slippage_bps,
                    "buy_fee_bps": self.settings.buy_fee_bps,
                    "sell_fee_bps": self.settings.sell_fee_bps,
                    "sell_tax_bps": self.settings.sell_tax_bps,
                    "strategy_name": self.config.strategy_name,
                    "universe": self.config.universe,
                    "min_market_cap_krw": self.config.min_market_cap_krw,
                    "min_change_pct": self.config.min_change_pct,
                    "max_change_pct": self.config.max_change_pct,
                    "min_turnover_krw": self.config.min_turnover_krw,
                    "min_volume": self.config.min_volume,
                    "envelope_ma_days": self.config.envelope_ma_days,
                    "envelope_lower_pct": self.config.envelope_lower_pct,
                    "max_spread_ticks": self.config.max_spread_ticks,
                    "ma_short_days": self.config.ma_short_days,
                    "ma_mid_days": self.config.ma_mid_days,
                    "ma_long_days": self.config.ma_long_days,
                    "slope_lookback_days": self.config.slope_lookback_days,
                    "deploy_cash_ratio": self.config.deploy_cash_ratio,
                    "max_positions": self.config.max_positions,
                    "min_positions": self.config.min_positions,
                    "take_profit_pct": self.config.take_profit_pct,
                    "stop_loss_pct": self.config.stop_loss_pct,
                    "monitor_poll_seconds": self.config.monitor_poll_seconds,
                    "monitor_end_time": self.config.monitor_end_time,
                    "friday_liquidation_time": self.config.friday_liquidation_time,
                    "signal_weekday": self.settings.signal_weekday,
                    "entry_offset_trading_days": self.settings.entry_offset_trading_days,
                    "liquidation_offset_trading_days": self.settings.liquidation_offset_trading_days,
                    "signal_price_basis": self._signal_price_basis(),
                    "approximate_monday_10am": self.settings.approximate_monday_10am,
                    "monday_approx_price_mode": self.settings.monday_approx_price_mode,
                    "monday_approx_max_gap_pct": self.settings.monday_approx_max_gap_pct,
                    "collision_take_profit_ratio": self.settings.collision_take_profit_ratio,
                    "entry_trigger_change_pct": self.settings.entry_trigger_change_pct,
                    "entry_cost_aware_sizing": self.settings.use_entry_cost_aware_sizing,
                    "historical_universe_local_hits": self._historical_universe_counts["local"],
                    "historical_universe_remote_hits": self._historical_universe_counts["remote"],
                    "historical_universe_fallbacks": self._historical_universe_counts["fallback"],
                    "historical_universe_dates": historical_universe_dates,
                    "historical_universe_fallback_pct": round(historical_universe_fallback_pct, 4),
                    "data_source": self.settings.data_source,
                    "run_name": self.output_dir.name,
                }
            ]
        )

    @staticmethod
    def _listing_by_code(listing: pd.DataFrame) -> dict[str, dict[str, object]]:
        rows: dict[str, dict[str, object]] = {}
        for row in listing.to_dict(orient="records"):
            code = str(row.get("Code") or row.get("Symbol") or row.get("code") or "").zfill(6)
            if not code:
                continue
            rows[code] = row
        return rows

    @staticmethod
    def _prepare_history(code: str, history: pd.DataFrame, listing_row: dict[str, object]) -> pd.DataFrame:
        df = history.copy()
        close = pd.to_numeric(df["Close"], errors="coerce")
        df["ma20"] = close.rolling(20).mean()
        df["ma30"] = close.rolling(30).mean()
        df["ma30_prev"] = df["ma30"].shift(1)
        df["ma50"] = close.rolling(50).mean()
        df["ma50_prev"] = df["ma50"].shift(1)
        df["ma120"] = close.rolling(120).mean()
        df["ma120_prev"] = df["ma120"].shift(1)
        df["change_pct"] = pd.to_numeric(df["Change"], errors="coerce").fillna(0.0) * 100.0
        df["turnover_krw"] = (close * pd.to_numeric(df["Volume"], errors="coerce").fillna(0)).fillna(0)
        shares = int(
            listing_row.get("Stocks")
            or listing_row.get("ListedShares")
            or listing_row.get("listed_shares")
            or 0
        )
        static_market_cap = int(listing_row.get("Marcap") or listing_row.get("MarketCap") or listing_row.get("market_cap") or 0)
        if shares > 0:
            df["market_cap_krw"] = (close * shares).fillna(0)
        else:
            df["market_cap_krw"] = static_market_cap
        df["name"] = str(listing_row.get("Name") or listing_row.get("name") or code)
        return df

    @staticmethod
    def _trading_dates(histories: dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
        all_dates: set[pd.Timestamp] = set()
        for history in histories.values():
            all_dates.update(pd.to_datetime(history.index))
        return sorted(all_dates)

    @staticmethod
    def _liquidation_date(
        monday: pd.Timestamp,
        trading_dates: list[pd.Timestamp],
        liquidation_offset: int,
    ) -> pd.Timestamp | None:
        candidates = [dt for dt in trading_dates if monday <= dt <= monday + pd.Timedelta(days=4)]
        if not candidates:
            return None
        base_week_end = candidates[-1]
        if liquidation_offset <= 0:
            return base_week_end
        return WeeklyBacktester._offset_trading_date(base_week_end, trading_dates, liquidation_offset)

    @staticmethod
    def _next_trading_date(current_date: pd.Timestamp, trading_dates: list[pd.Timestamp]) -> pd.Timestamp | None:
        for date_key in trading_dates:
            if date_key > current_date:
                return date_key
        return None

    @staticmethod
    def _offset_trading_date(
        current_date: pd.Timestamp,
        trading_dates: list[pd.Timestamp],
        offset: int,
    ) -> pd.Timestamp | None:
        later_dates = [date_key for date_key in trading_dates if date_key > current_date]
        if offset <= 0:
            return current_date if current_date in trading_dates else None
        if len(later_dates) < offset:
            return None
        return later_dates[offset - 1]

    def _signal_weekday_index(self) -> int:
        weekday = self.settings.signal_weekday.strip().lower()
        mapping = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
        }
        if weekday not in mapping:
            raise ValueError(f"Unsupported signal weekday: {self.settings.signal_weekday}")
        return mapping[weekday]

    def _collision_prefers_take_profit(self, code: str, entry_date: pd.Timestamp, exit_date: pd.Timestamp) -> bool:
        ratio = min(max(self.settings.collision_take_profit_ratio, 0.0), 1.0)
        bucket_size = 100
        threshold = int(round(ratio * bucket_size))
        seed = f"{code}-{entry_date.date().isoformat()}-{exit_date.date().isoformat()}"
        bucket = sum(ord(char) for char in seed) % bucket_size
        return bucket < threshold

    def _is_monday_approx_reliable(self, signal_row: pd.Series, approx_change_pct: float) -> bool:
        if self._signal_price_basis() != "entry_open_proxy":
            return False
        if pd.isna(approx_change_pct):
            return False
        if abs(approx_change_pct) > self.settings.monday_approx_max_gap_pct:
            return False
        min_change = self.config.min_change_pct
        max_change = self.config.max_change_pct
        return min_change <= approx_change_pct <= max_change

    def _approximate_signal_price(self, friday_close: float, monday_open: float) -> int:
        mode = self.settings.monday_approx_price_mode.strip().lower()
        if mode == "open":
            return int(monday_open)
        if mode == "mid":
            return int(round((friday_close + monday_open) / 2.0))
        if mode == "weighted":
            return int(round(friday_close * 0.6 + monday_open * 0.4))
        raise ValueError(f"Unsupported monday approximation price mode: {self.settings.monday_approx_price_mode}")

    def _signal_price_basis(self) -> str:
        basis = self.settings.signal_price_basis.strip().lower()
        if basis in {"previous_close", "entry_open_proxy"}:
            return basis
        if self.settings.approximate_monday_10am:
            return "entry_open_proxy"
        return "previous_close"

    @staticmethod
    def _trading_day_distance(start_date: pd.Timestamp, end_date: pd.Timestamp) -> int:
        return max(int(len(pd.bdate_range(start_date, end_date)) - 1), 0)

    def _apply_buy_costs(self, price: float) -> float:
        return price * (1.0 + self.settings.buy_slippage_bps / 10_000.0)

    def _apply_sell_costs(self, price: float) -> float:
        return price * (1.0 - self.settings.sell_slippage_bps / 10_000.0)

    @staticmethod
    def _max_drawdown_pct(weekly_df: pd.DataFrame) -> float:
        if weekly_df.empty:
            return 0.0
        equity = weekly_df["end_cash"].astype(float)
        running_max = equity.cummax()
        drawdown = (equity / running_max - 1.0) * 100.0
        return float(drawdown.min())

    def _write_outputs(
        self,
        summary_df: pd.DataFrame,
        trades_df: pd.DataFrame,
        weekly_df: pd.DataFrame,
        monthly_df: pd.DataFrame,
    ) -> None:
        summary_df.to_csv(self.output_dir / "summary.csv", index=False, encoding="utf-8")
        trades_df.to_csv(self.output_dir / "trades.csv", index=False, encoding="utf-8")
        weekly_df.to_csv(self.output_dir / "weekly.csv", index=False, encoding="utf-8")
        monthly_df.to_csv(self.output_dir / "monthly.csv", index=False, encoding="utf-8")
        self._build_universe_coverage_df().to_csv(self.output_dir / "universe_coverage.csv", index=False, encoding="utf-8")
        self._write_run_metadata()

    def _build_run_name(self) -> str:
        if self.settings.run_name:
            return self.settings.run_name
        return datetime.now().strftime("run_%Y%m%d_%H%M%S")

    def _write_run_metadata(self) -> None:
        manifest = {
            "run_name": self.output_dir.name,
            "output_dir": str(self.output_dir),
            "config": asdict(self.config),
            "settings": {
                key: str(value) if isinstance(value, Path) else value
                for key, value in asdict(self.settings).items()
            },
        }
        with (self.output_dir / "run_manifest.json").open("w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        with (self.output_dir / "config_snapshot.yaml").open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                {
                    "strategy_config": manifest["config"],
                    "backtest_settings": manifest["settings"],
                },
                f,
                allow_unicode=True,
                sort_keys=False,
            )

    def _run_week(
        self,
        signal_date: pd.Timestamp,
        trading_dates: list[pd.Timestamp],
        prepared: dict[str, pd.DataFrame],
        starting_cash: float,
    ) -> dict[str, Any] | None:
        entry_date = self._offset_trading_date(signal_date, trading_dates, self.settings.entry_offset_trading_days)
        if entry_date is None:
            return None
        week_end = self._liquidation_date(entry_date, trading_dates, self.settings.liquidation_offset_trading_days)
        if week_end is None:
            return None

        primary_universe_codes = self._universe_codes_for_date(signal_date)
        primary_snapshots, primary_signal_modes = self._build_snapshots_for_date(
            signal_date,
            prepared,
            primary_universe_codes,
            entry_date=entry_date if self._signal_price_basis() == "entry_open_proxy" else None,
        )
        primary_candidates = self.strategy.select_candidates(primary_snapshots)
        primary_orders = self._build_backtest_buy_orders(
            candidates=primary_candidates,
            prepared=prepared,
            signal_date=signal_date,
            entry_date=entry_date,
            available_cash=int(starting_cash),
        )

        cash_end_week = starting_cash
        total_orders = 0
        total_candidates = len(primary_candidates)
        weekly_trade_rows: list[dict[str, object]] = []
        week_realized = 0.0

        for order in primary_orders:
            trade_row, pnl_krw = self._simulate_order_trade(
                week_start=entry_date,
                signal_date=signal_date,
                entry_date=entry_date,
                week_end=week_end,
                order=order,
                history=prepared.get(order.code),
                signal_mode=primary_signal_modes.get(order.code, "fallback"),
            )
            if trade_row is None:
                continue
            total_orders += 1
            weekly_trade_rows.append(trade_row)
            week_realized += pnl_krw
            cash_end_week += pnl_krw

        if entry_date > week_end:
            return {
                "ending_cash": round(starting_cash, 2),
                "trade_rows": [],
                "weekly_row": {
                    "week_start": entry_date.date().isoformat(),
                    "week_end": week_end.date().isoformat(),
                    "signal_date": signal_date.date().isoformat(),
                    "entry_date": "",
                    "start_cash": round(starting_cash, 2),
                    "end_cash": round(starting_cash, 2),
                    "pnl_krw": 0.0,
                    "pnl_pct": 0.0,
                    "num_candidates": total_candidates,
                    "num_orders": 0,
                    "num_trades": 0,
                    "realized_pnl_krw": 0.0,
                },
            }

        return {
            "ending_cash": round(cash_end_week, 2),
            "trade_rows": weekly_trade_rows,
            "weekly_row": {
                "week_start": entry_date.date().isoformat(),
                "week_end": week_end.date().isoformat(),
                "signal_date": signal_date.date().isoformat(),
                "entry_date": entry_date.date().isoformat(),
                "start_cash": round(starting_cash, 2),
                "end_cash": round(cash_end_week, 2),
                "pnl_krw": round(cash_end_week - starting_cash, 2),
                "pnl_pct": round(((cash_end_week / starting_cash) - 1.0) * 100.0, 4) if starting_cash > 0 else 0.0,
                "num_candidates": total_candidates,
                "num_orders": total_orders,
                "num_trades": len(weekly_trade_rows),
                "realized_pnl_krw": round(week_realized, 2),
            },
        }

    def _simulate_order_trade(
        self,
        week_start: pd.Timestamp,
        signal_date: pd.Timestamp,
        entry_date: pd.Timestamp,
        week_end: pd.Timestamp,
        order: Any,
        history: pd.DataFrame | None,
        signal_mode: str,
    ) -> tuple[dict[str, object] | None, float]:
        if history is None or entry_date not in history.index:
            return None, 0.0

        entry_price = self._resolve_entry_price(order, history, signal_date, entry_date)
        if entry_price <= 0:
            return None, 0.0
        quantity = order.quantity
        gross_cost = entry_price * quantity
        buy_fee = gross_cost * self.settings.buy_fee_bps / 10_000.0
        total_cost = gross_cost + buy_fee

        exit_info = self._simulate_exit(
            code=order.code,
            name=order.name,
            history=history,
            entry_date=entry_date,
            week_end=week_end,
            entry_price=entry_price,
            quantity=quantity,
            collision_mode=signal_mode,
        )
        pnl_krw = exit_info["net_proceeds"] - total_cost
        pnl_pct = (pnl_krw / total_cost * 100.0) if total_cost > 0 else 0.0

        return (
            {
                "week_start": week_start.date().isoformat(),
                "signal_date": signal_date.date().isoformat(),
                "entry_date": entry_date.date().isoformat(),
                "exit_date": exit_info["exit_date"],
                "code": order.code,
                "name": order.name,
                "signal_mode": signal_mode,
                "quantity": quantity,
                "entry_price": round(entry_price, 4),
                "exit_price": round(exit_info["exit_price"], 4),
                "exit_reason": exit_info["exit_reason"],
                "holding_days": exit_info["holding_days"],
                "gross_cost": round(gross_cost, 2),
                "buy_fee": round(buy_fee, 2),
                "sell_fee": round(exit_info["sell_fee"], 2),
                "sell_tax": round(exit_info["sell_tax"], 2),
                "net_proceeds": round(exit_info["net_proceeds"], 2),
                "pnl_krw": round(pnl_krw, 2),
                "pnl_pct": round(pnl_pct, 4),
            },
            pnl_krw,
        )

    def _resolve_entry_price(
        self,
        order: Any,
        history: pd.DataFrame,
        signal_date: pd.Timestamp,
        entry_date: pd.Timestamp,
    ) -> float:
        target_price = float(order.reference_price)
        if target_price <= 0:
            signal_close = float(history.loc[signal_date, "Close"]) if signal_date in history.index else 0.0
            target_price = float(self._target_entry_price(int(round(signal_close))))
        entry_bar = history.loc[entry_date]
        entry_high = float(entry_bar["High"])
        entry_low = float(entry_bar["Low"])
        if target_price <= 0 or not (entry_low <= target_price <= entry_high):
            return 0.0
        return self._apply_buy_costs(target_price)

    def _build_backtest_buy_orders(
        self,
        candidates: list[Any],
        prepared: dict[str, pd.DataFrame],
        signal_date: pd.Timestamp,
        entry_date: pd.Timestamp,
        available_cash: int,
    ) -> list[OrderIntent]:
        deploy_cash = int(available_cash * self.config.deploy_cash_ratio)
        if deploy_cash <= 0:
            return []

        max_target_count = len(candidates)
        if self.config.max_positions > 0:
            max_target_count = min(max_target_count, self.config.max_positions)
        if max_target_count <= 0:
            return []

        selected = self._select_affordable_backtest_candidates(
            candidates,
            prepared,
            signal_date,
            entry_date,
            deploy_cash,
            max_target_count,
        )
        if not selected:
            return []

        orders: list[OrderIntent] = []
        remaining_cash = deploy_cash
        remaining_slots = len(selected)
        for candidate in selected:
            unit_cost = self._entry_unit_cost(candidate.snapshot.code, prepared, signal_date, entry_date)
            if unit_cost <= 0 or remaining_slots <= 0:
                remaining_slots -= 1
                continue
            per_stock_cash = remaining_cash // remaining_slots
            quantity = int(per_stock_cash // unit_cost)
            if quantity <= 0:
                remaining_slots -= 1
                continue
            estimated_cost = quantity * unit_cost
            orders.append(
                OrderIntent(
                    code=candidate.snapshot.code,
                    name=candidate.snapshot.name,
                    side="BUY",
                    quantity=quantity,
                    order_type="LIMIT",
                    reason="weekly_pullback_entry_prev_close_minus_2pct",
                    reference_price=self._signal_date_target_price(prepared.get(candidate.snapshot.code), signal_date),
                    trigger_price=int(candidate.snapshot.current_price),
                )
            )
            remaining_cash -= int(estimated_cost)
            remaining_slots -= 1
        return orders

    def _select_affordable_backtest_candidates(
        self,
        candidates: list[Any],
        prepared: dict[str, pd.DataFrame],
        signal_date: pd.Timestamp,
        entry_date: pd.Timestamp,
        deploy_cash: int,
        max_target_count: int,
    ) -> list[Any]:
        if deploy_cash <= 0 or max_target_count <= 0:
            return []

        min_target_count = min(max(int(self.config.min_positions), 1), min(max_target_count, len(candidates)))
        desired_max = min(max_target_count, len(candidates))
        for target_count in range(desired_max, min_target_count - 1, -1):
            selected = self._try_select_backtest_candidates(
                candidates,
                prepared,
                signal_date,
                entry_date,
                deploy_cash,
                target_count,
            )
            if len(selected) == target_count:
                return selected

        for target_count in range(min_target_count - 1, 0, -1):
            selected = self._try_select_backtest_candidates(
                candidates,
                prepared,
                signal_date,
                entry_date,
                deploy_cash,
                target_count,
            )
            if len(selected) == target_count:
                return selected
        return []

    def _try_select_backtest_candidates(
        self,
        candidates: list[Any],
        prepared: dict[str, pd.DataFrame],
        signal_date: pd.Timestamp,
        entry_date: pd.Timestamp,
        deploy_cash: int,
        target_count: int,
    ) -> list[Any]:
        remaining_cash = deploy_cash
        selected: list[Any] = []
        for candidate in candidates:
            unit_cost = self._entry_unit_cost(candidate.snapshot.code, prepared, signal_date, entry_date)
            if unit_cost <= 0:
                continue
            remaining_slots = target_count - len(selected)
            if remaining_slots <= 0:
                break
            per_stock_cash = remaining_cash // remaining_slots
            quantity = int(per_stock_cash // unit_cost)
            estimated_cost = quantity * unit_cost
            if quantity <= 0 or estimated_cost <= 0 or estimated_cost > remaining_cash:
                continue
            selected.append(candidate)
            remaining_cash -= int(estimated_cost)
        return selected

    def _entry_unit_cost(
        self,
        code: str,
        prepared: dict[str, pd.DataFrame],
        signal_date: pd.Timestamp,
        entry_date: pd.Timestamp,
    ) -> float:
        history = prepared.get(code)
        if history is None or entry_date not in history.index:
            return 0.0
        target_price = self._signal_date_target_price(history, signal_date)
        if target_price <= 0:
            return 0.0
        entry_bar = history.loc[entry_date]
        entry_high = float(entry_bar["High"])
        entry_low = float(entry_bar["Low"])
        if not (entry_low <= target_price <= entry_high):
            return 0.0
        entry_price = self._apply_buy_costs(target_price)
        return entry_price * (1.0 + self.settings.buy_fee_bps / 10_000.0)

    def _signal_date_target_price(self, history: pd.DataFrame | None, signal_date: pd.Timestamp) -> int:
        if history is None or signal_date not in history.index:
            return 0
        signal_close = int(round(float(history.loc[signal_date, "Close"])))
        return self._target_entry_price(signal_close)

    def _target_entry_price(self, signal_price: int) -> int:
        if signal_price <= 0:
            return 0
        return discounted_limit_price(signal_price, abs(self.settings.entry_trigger_change_pct))

    def _build_universe_coverage_df(self) -> pd.DataFrame:
        rows = [
            {
                "date": date_key,
                "source": source,
                "count": len(self._historical_universe_cache.get(date_key, set())),
            }
            for date_key, source in sorted(self._historical_universe_sources.items())
        ]
        return pd.DataFrame(rows, columns=["date", "source", "count"])

    def _universe_codes_for_date(self, date_key: pd.Timestamp) -> set[str]:
        cache_key = date_key.date().isoformat()
        if cache_key in self._historical_universe_cache:
            return self._historical_universe_cache[cache_key]

        resolved, source = self._load_historical_universe_codes(date_key)
        if not resolved:
            if self.settings.require_historical_universe:
                raise RuntimeError(f"Historical KOSPI200 universe unavailable for {cache_key}")
            resolved = set(self._fallback_universe_codes)
            source = "fallback"
        self._historical_universe_cache[cache_key] = resolved
        self._historical_universe_sources[cache_key] = source
        self._historical_universe_counts[source] = self._historical_universe_counts.get(source, 0) + 1
        return resolved

    def _load_historical_universe_codes(self, date_key: pd.Timestamp) -> tuple[set[str], str]:
        if not self.settings.use_historical_universe:
            return set(self._fallback_universe_codes), "fallback"

        local_codes = self._load_local_historical_universe_codes(date_key)
        if local_codes:
            return local_codes, "local"
        try:
            from pykrx import stock  # type: ignore[import]

            codes = stock.get_index_portfolio_deposit_file(
                self.settings.historical_universe_index_ticker,
                date_key.strftime("%Y%m%d"),
            )
        except Exception:
            return set(), "remote"

        normalized: set[str] = set()
        if isinstance(codes, pd.DataFrame):
            values = []
            for column in ("종목코드", "티커", "ticker", "code"):
                if column in codes.columns:
                    values = codes[column].tolist()
                    break
        else:
            values = list(codes)
        for value in values:
            code = str(value or "").strip().replace(".KS", "").replace(".KQ", "")
            if code:
                normalized.add(code.zfill(6))
        return normalized, "remote"

    @staticmethod
    def _historical_universe_data_dir() -> Path:
        return Path(__file__).resolve().parents[2] / "data" / "historical_kospi200"

    def _load_local_historical_universe_codes(self, date_key: pd.Timestamp) -> set[str]:
        data_dir = self._historical_universe_data_dir()
        if not data_dir.exists():
            return set()

        iso_key = date_key.strftime("%Y-%m-%d")
        compact_key = date_key.strftime("%Y%m%d")
        for candidate_path in (
            data_dir / f"{iso_key}.csv",
            data_dir / f"{compact_key}.csv",
        ):
            codes = self._read_historical_universe_file(candidate_path)
            if codes:
                return codes

        membership_path = data_dir / "membership.csv"
        if membership_path.exists():
            try:
                membership_df = pd.read_csv(membership_path, dtype=str)
            except Exception:
                return set()
            date_column = next((column for column in membership_df.columns if column.lower() in {"date", "dt", "as_of_date"}), None)
            code_column = next((column for column in membership_df.columns if column.lower() in {"code", "ticker", "symbol"}), None)
            if date_column and code_column:
                matched = membership_df.loc[membership_df[date_column].astype(str).str.strip() == iso_key, code_column]
                codes = self._normalize_universe_codes(matched.tolist())
                if codes:
                    return codes
            if code_column:
                start_column = next(
                    (
                        column
                        for column in membership_df.columns
                        if column.lower() in {"start_date", "from_date", "effective_from", "start", "from"}
                    ),
                    None,
                )
                end_column = next(
                    (
                        column
                        for column in membership_df.columns
                        if column.lower() in {"end_date", "to_date", "effective_to", "end", "to"}
                    ),
                    None,
                )
                if start_column:
                    date_value = pd.Timestamp(date_key).normalize()
                    start_dates = pd.to_datetime(membership_df[start_column], errors="coerce").dt.normalize()
                    if end_column:
                        end_dates = pd.to_datetime(membership_df[end_column], errors="coerce").dt.normalize()
                    else:
                        end_dates = pd.Series(pd.NaT, index=membership_df.index)
                    matched = membership_df.loc[
                        start_dates.notna()
                        & (start_dates <= date_value)
                        & (end_dates.isna() | (date_value <= end_dates)),
                        code_column,
                    ]
                    codes = self._normalize_universe_codes(matched.tolist())
                    if codes:
                        return codes
        return set()

    @staticmethod
    def _read_historical_universe_file(csv_path: Path) -> set[str]:
        if not csv_path.exists():
            return set()
        try:
            df = pd.read_csv(csv_path, dtype=str)
        except Exception:
            return set()
        for column in ("Code", "code", "ticker", "Ticker", "Symbol", "symbol"):
            if column in df.columns:
                return WeeklyBacktester._normalize_universe_codes(df[column].tolist())
        return set()

    @staticmethod
    def _normalize_universe_codes(values: list[object]) -> set[str]:
        return {
            str(value).strip().replace(".KS", "").replace(".KQ", "").zfill(6)
            for value in values
            if str(value).strip()
        }

    @staticmethod
    def _is_expected_kospi200_codes(codes: set[str]) -> bool:
        return MIN_EXPECTED_KOSPI200_SIZE <= len(codes) <= MAX_EXPECTED_KOSPI200_SIZE

    @staticmethod
    def _load_fallback_universe_codes(listing_by_code: dict[str, dict[str, object]]) -> set[str]:
        csv_path = Path(__file__).resolve().parents[2] / "data" / "kospi200_latest.csv"
        if csv_path.exists():
            try:
                df = pd.read_csv(csv_path, dtype=str)
                codes = WeeklyBacktester._normalize_universe_codes(df.get("Code", []).tolist())
                if WeeklyBacktester._is_expected_kospi200_codes(codes):
                    return codes
            except Exception:
                pass
        fallback_codes = set(listing_by_code)
        if WeeklyBacktester._is_expected_kospi200_codes(fallback_codes):
            return fallback_codes
        return set()
