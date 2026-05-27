from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from bot.config import StrategyConfig
from bot.data.historical_provider import HistoricalKrxDataProvider
from bot.models import MarketSnapshot
from bot.risk.position_sizing import EqualWeightPositionSizer
from bot.strategy.weekly_pullback import WeeklyPullbackStrategy


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
    approximate_monday_10am: bool = False
    monday_approx_price_mode: str = "open"
    monday_approx_max_gap_pct: float = 2.0
    collision_take_profit_ratio: float = 0.75
    buy_slippage_bps: float = 0.0
    sell_slippage_bps: float = 0.0
    buy_fee_bps: float = 0.0
    sell_fee_bps: float = 0.0
    sell_tax_bps: float = 0.0
    output_dir: str | Path = "logs/backtests"


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
        self.output_dir = Path(settings.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> BacktestArtifacts:
        provider = HistoricalKrxDataProvider(source=self.settings.data_source)
        market_data = provider.load(start=self.settings.start, end=self.settings.end)

        listing_by_code = self._listing_by_code(market_data.listing)
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
            entry_date = self._offset_trading_date(signal_date, trading_dates, self.settings.entry_offset_trading_days)
            if entry_date is None:
                continue
            week_end = self._week_end(entry_date, trading_dates)
            if week_end is None:
                continue

            snapshots, signal_modes = self._build_snapshots_for_date(
                signal_date,
                prepared,
                entry_date=entry_date if self.settings.approximate_monday_10am else None,
            )
            candidates = self.strategy.select_candidates(snapshots)
            orders = self.sizer.build_buy_orders(candidates, int(cash))
            if entry_date > week_end:
                weekly_rows.append(
                    {
                        "week_start": entry_date.date().isoformat(),
                        "week_end": week_end.date().isoformat(),
                        "signal_date": signal_date.date().isoformat(),
                        "entry_date": "",
                        "start_cash": round(cash, 2),
                        "end_cash": round(cash, 2),
                        "pnl_krw": 0.0,
                        "pnl_pct": 0.0,
                        "num_candidates": len(candidates),
                        "num_orders": 0,
                        "num_trades": 0,
                        "realized_pnl_krw": 0.0,
                    }
                )
                continue

            week_start_cash = cash
            week_realized = 0.0
            week_trades = 0

            for order in orders:
                history = prepared.get(order.code)
                if history is None or entry_date not in history.index:
                    continue

                entry_bar = history.loc[entry_date]
                entry_price = self._apply_buy_costs(float(entry_bar["Open"]))
                quantity = order.quantity
                gross_cost = entry_price * quantity
                buy_fee = gross_cost * self.settings.buy_fee_bps / 10_000.0
                total_cost = gross_cost + buy_fee
                if total_cost > cash:
                    continue

                cash -= total_cost
                exit_info = self._simulate_exit(
                    code=order.code,
                    name=order.name,
                    history=history,
                    entry_date=entry_date,
                    week_end=week_end,
                    entry_price=entry_price,
                    quantity=quantity,
                    collision_mode=signal_modes.get(order.code, "fallback"),
                )
                cash += exit_info["net_proceeds"]
                pnl_krw = exit_info["net_proceeds"] - total_cost
                pnl_pct = (pnl_krw / total_cost * 100.0) if total_cost > 0 else 0.0

                trade_rows.append(
                    {
                        "week_start": entry_date.date().isoformat(),
                        "signal_date": signal_date.date().isoformat(),
                        "entry_date": entry_date.date().isoformat(),
                        "exit_date": exit_info["exit_date"],
                        "code": order.code,
                        "name": order.name,
                        "signal_mode": signal_modes.get(order.code, "fallback"),
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
                    }
                )
                week_realized += pnl_krw
                week_trades += 1

            weekly_rows.append(
                {
                    "week_start": entry_date.date().isoformat(),
                    "week_end": week_end.date().isoformat(),
                    "signal_date": signal_date.date().isoformat(),
                    "entry_date": entry_date.date().isoformat(),
                    "start_cash": round(week_start_cash, 2),
                    "end_cash": round(cash, 2),
                    "pnl_krw": round(cash - week_start_cash, 2),
                    "pnl_pct": round(((cash / week_start_cash) - 1.0) * 100.0, 4) if week_start_cash > 0 else 0.0,
                    "num_candidates": len(candidates),
                    "num_orders": len(orders),
                    "num_trades": week_trades,
                    "realized_pnl_krw": round(week_realized, 2),
                }
            )

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
                    is_kospi200=True,
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
        exit_reason = "friday_liquidation"

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
        df["market_cap_krw"] = int(listing_row.get("Marcap") or listing_row.get("MarketCap") or listing_row.get("market_cap") or 0)
        df["name"] = str(listing_row.get("Name") or listing_row.get("name") or code)
        return df

    @staticmethod
    def _trading_dates(histories: dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
        all_dates: set[pd.Timestamp] = set()
        for history in histories.values():
            all_dates.update(pd.to_datetime(history.index))
        return sorted(all_dates)

    @staticmethod
    def _week_end(monday: pd.Timestamp, trading_dates: list[pd.Timestamp]) -> pd.Timestamp | None:
        candidates = [dt for dt in trading_dates if monday <= dt <= monday + pd.Timedelta(days=4)]
        return candidates[-1] if candidates else None

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
        if not self.settings.approximate_monday_10am:
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
