from __future__ import annotations

import argparse
import sys
from pathlib import Path

from Daily_bot.backtest import replay_market_traces as base


def run_backtest_dynamic_expect_debounce(
    db_path: Path,
    min_expected_return_percent: float,
    max_spread_percent: float,
    top_n_per_day: int,
    stop_loss_percent: float,
    dynamic_expect_stop_threshold_percent: float = 0.0,
    dynamic_expect_stop_consecutive: int = 3,
    min_prev_day_change_percent: float = 0.0,
    max_prev_day_change_percent: float = 0.0,
    stop_loss_tick_count: int = 0,
    stop_loss_tick_multiplier: float = 2.0,
    use_selected_signals: bool = True,
    take_profit_percent: float = 0.25,
    top_ratio: float = 1.0,
    sell_tick_offset: int = 1,
    session_capital_by_day: dict[str, int] | None = None,
    default_starting_capital_krw: int = 0,
    min_slot_count: int = 1,
    max_slot_count: int = 0,
    slot_budget_unit_krw: int = 0,
    max_budget_per_stock_krw: int = 0,
    max_position_count: int = 0,
    max_buy_count: int = 0,
    target_budget_ratio_per_stock: float = 0.0,
    start_buy_time: str = "09:30",
    stop_buy_time: str = "13:00",
    force_sell_time: str = "15:00",
    max_hold_seconds_before_exit: int = 0,
    spread_expected_return_multiplier: float = 0.0,
    max_intraday_jump_from_prev_scan_percent: float = 0.0,
    fallback_min_expected_return_percents: list[float] | tuple[float, ...] | None = None,
    max_orderbook_ask_depth_ratio: float = 0.0,
    missing_ask_depth_policy: str = "ignore",
    allow_refill_empty_slots: bool = False,
    refill_min_empty_fraction: float = 0.0,
    block_stop_loss_reentry_same_day: bool = False,
    trend_filter_enabled: bool = False,
    trend_ok_tickers_by_day: dict[str, set[str]] | None = None,
    trend_filter_days: set[str] | None = None,
    orderbook_levels_per_side: int = 10,
    orderbook_bid_linear_decay_min_weight: float = 1.0,
    orderbook_ask_linear_decay_min_weight: float = 1.0,
    selected_signals_override: list[base.SelectedSignal] | None = None,
    actual_exit_overrides_by_ticker: dict[tuple[str, str], list[base.ActualExitOverride]] | None = None,
) -> list[base.BacktestTrade]:
    """Replay the normal strategy with a debounced forward expected-return stop.

    A held position increments its counter when the refreshed expected sell price
    implies an expected return versus the current price at or below the configured
    threshold. Any refresh above the threshold resets the counter. The position is
    exited at the current trace price once the counter reaches the configured
    consecutive count.

    This is backtest-only; live trading behavior is untouched.
    """

    consecutive_required = max(1, int(dynamic_expect_stop_consecutive or 1))
    traces = base.load_traces(db_path)
    traces = base.apply_orderbook_level_limit(
        traces,
        levels_per_side=orderbook_levels_per_side,
        sell_tick_offset=sell_tick_offset,
        bid_linear_decay_min_weight=orderbook_bid_linear_decay_min_weight,
        ask_linear_decay_min_weight=orderbook_ask_linear_decay_min_weight,
    )
    selected_signals = (
        list(selected_signals_override)
        if selected_signals_override is not None
        else (base.load_selected_signals(db_path) if use_selected_signals else [])
    )
    selected_tickers_by_timestamp = base._selected_tickers_by_timestamp(selected_signals)
    grouped_by_session = base.group_by_session(traces)
    trades: list[base.BacktestTrade] = []

    for session_date, day_rows in grouped_by_session.items():
        session_plan = base.resolve_session_capital_plan(
            session_date=session_date,
            session_capital_by_day=session_capital_by_day,
            default_starting_capital_krw=default_starting_capital_krw,
            min_slot_count=min_slot_count,
            max_slot_count=max_slot_count,
            slot_budget_unit_krw=slot_budget_unit_krw,
            max_budget_per_stock_krw=max_budget_per_stock_krw,
            max_position_count=max_position_count,
            target_budget_ratio_per_stock=target_budget_ratio_per_stock,
        )
        if session_plan.position_limit <= 0 or session_plan.slot_budget_per_stock <= 0 or session_plan.session_capital_basis <= 0:
            continue

        rows_by_time: dict[str, list[base.TraceRow]] = {}
        last_row_by_ticker: dict[str, base.TraceRow] = {}
        for row in day_rows:
            rows_by_time.setdefault(row.created_at, []).append(row)
            last_row_by_ticker[row.ticker] = row

        actual_exit_queues: dict[tuple[str, str], list[base.ActualExitOverride]] = {}
        session_override_times: set[str] = set()
        if actual_exit_overrides_by_ticker:
            for key, overrides in actual_exit_overrides_by_ticker.items():
                if key[0] != session_date or not overrides:
                    continue
                actual_exit_queues[key] = list(overrides)
                session_override_times.update(item.final_exit_time for item in overrides)

        open_positions: dict[str, base.ReplayPosition] = {}
        dynamic_stop_counts: dict[str, int] = {}
        blocked_reentry_tickers: set[str] = set()
        previous_scan_prices: dict[str, int] = {}
        trend_allowed_tickers = None
        if trend_filter_enabled and trend_filter_days and session_date in trend_filter_days:
            trend_allowed_tickers = (trend_ok_tickers_by_day or {}).get(session_date, set())
        available_cash = session_plan.session_capital_basis

        for created_at in sorted(set(rows_by_time) | session_override_times):
            rows_at_time = rows_by_time.get(created_at, [])
            rows_by_ticker = {row.ticker: row for row in rows_at_time}
            exited_tickers_at_time: set[str] = set()

            for ticker, position in list(open_positions.items()):
                actual_exit_override = position.actual_exit_override
                if actual_exit_override is not None and actual_exit_override.final_exit_time <= created_at:
                    exit_price = actual_exit_override.weighted_exit_price
                    trades.append(
                        base.BacktestTrade(
                            session_date=session_date,
                            ticker=ticker,
                            entry_time=position.entry.created_at,
                            exit_time=actual_exit_override.final_exit_time,
                            quantity=position.quantity,
                            entry_price=position.entry_price,
                            exit_price=exit_price,
                            buy_amount_krw=position.invested_amount,
                            sell_amount_krw=position.quantity * exit_price,
                            exit_reason="actual_fill_exit",
                            pnl_percent=base._realized_pnl_percent(position.entry_price, exit_price),
                        )
                    )
                    available_cash += position.quantity * exit_price
                    del open_positions[ticker]
                    dynamic_stop_counts.pop(ticker, None)
                    exited_tickers_at_time.add(ticker)
                    continue
                if actual_exit_override is not None:
                    continue

                current_row = rows_by_ticker.get(ticker)
                if current_row is None:
                    continue
                current_price = current_row.current_price or current_row.price
                if current_price <= 0:
                    continue

                if base._is_after_time(current_row.created_at, force_sell_time):
                    trades.append(
                        base.BacktestTrade(
                            session_date=session_date,
                            ticker=ticker,
                            entry_time=position.entry.created_at,
                            exit_time=current_row.created_at,
                            quantity=position.quantity,
                            entry_price=position.entry_price,
                            exit_price=current_price,
                            buy_amount_krw=position.invested_amount,
                            sell_amount_krw=position.quantity * current_price,
                            exit_reason="force_exit_time",
                            pnl_percent=base._realized_pnl_percent(position.entry_price, current_price),
                        )
                    )
                    available_cash += position.quantity * current_price
                    del open_positions[ticker]
                    dynamic_stop_counts.pop(ticker, None)
                    exited_tickers_at_time.add(ticker)
                    continue

                if max_hold_seconds_before_exit > 0:
                    held_seconds = (
                        base._parse_timestamp(current_row.created_at)
                        - base._parse_timestamp(position.entry.created_at)
                    ).total_seconds()
                    if held_seconds > max_hold_seconds_before_exit:
                        trades.append(
                            base.BacktestTrade(
                                session_date=session_date,
                                ticker=ticker,
                                entry_time=position.entry.created_at,
                                exit_time=current_row.created_at,
                                quantity=position.quantity,
                                entry_price=position.entry_price,
                                exit_price=current_price,
                                buy_amount_krw=position.invested_amount,
                                sell_amount_krw=position.quantity * current_price,
                                exit_reason="time_stop_loss",
                                pnl_percent=base._realized_pnl_percent(position.entry_price, current_price),
                            )
                        )
                        available_cash += position.quantity * current_price
                        del open_positions[ticker]
                        dynamic_stop_counts.pop(ticker, None)
                        exited_tickers_at_time.add(ticker)
                        continue

                if current_price <= position.stop_loss_price:
                    trades.append(
                        base.BacktestTrade(
                            session_date=session_date,
                            ticker=ticker,
                            entry_time=position.entry.created_at,
                            exit_time=current_row.created_at,
                            quantity=position.quantity,
                            entry_price=position.entry_price,
                            exit_price=current_price,
                            buy_amount_krw=position.invested_amount,
                            sell_amount_krw=position.quantity * current_price,
                            exit_reason="stop_loss",
                            pnl_percent=base._realized_pnl_percent(position.entry_price, current_price),
                        )
                    )
                    available_cash += position.quantity * current_price
                    if block_stop_loss_reentry_same_day:
                        blocked_reentry_tickers.add(ticker)
                    del open_positions[ticker]
                    dynamic_stop_counts.pop(ticker, None)
                    exited_tickers_at_time.add(ticker)
                    continue

                if current_price >= position.target_price:
                    exit_price = position.target_price
                    trades.append(
                        base.BacktestTrade(
                            session_date=session_date,
                            ticker=ticker,
                            entry_time=position.entry.created_at,
                            exit_time=current_row.created_at,
                            quantity=position.quantity,
                            entry_price=position.entry_price,
                            exit_price=exit_price,
                            buy_amount_krw=position.invested_amount,
                            sell_amount_krw=position.quantity * exit_price,
                            exit_reason="take_profit",
                            pnl_percent=base._realized_pnl_percent(position.entry_price, exit_price),
                        )
                    )
                    available_cash += position.quantity * exit_price
                    del open_positions[ticker]
                    dynamic_stop_counts.pop(ticker, None)
                    exited_tickers_at_time.add(ticker)
                    continue

                refreshed_expected_sell_price = (
                    base.calc_target_sell_price(current_row.expect_price, sell_tick_offset)
                    if current_row.expect_price > 0
                    else 0
                )
                refreshed_expected_return = (
                    (refreshed_expected_sell_price - current_price) / current_price * 100
                    if refreshed_expected_sell_price > 0 and current_price > 0
                    else None
                )
                if refreshed_expected_return is not None and refreshed_expected_return <= dynamic_expect_stop_threshold_percent:
                    dynamic_stop_counts[ticker] = dynamic_stop_counts.get(ticker, 0) + 1
                else:
                    dynamic_stop_counts[ticker] = 0

                if dynamic_stop_counts.get(ticker, 0) >= consecutive_required:
                    trades.append(
                        base.BacktestTrade(
                            session_date=session_date,
                            ticker=ticker,
                            entry_time=position.entry.created_at,
                            exit_time=current_row.created_at,
                            quantity=position.quantity,
                            entry_price=position.entry_price,
                            exit_price=current_price,
                            buy_amount_krw=position.invested_amount,
                            sell_amount_krw=position.quantity * current_price,
                            exit_reason="dynamic_expect_stop",
                            pnl_percent=base._realized_pnl_percent(position.entry_price, current_price),
                        )
                    )
                    available_cash += position.quantity * current_price
                    if block_stop_loss_reentry_same_day:
                        blocked_reentry_tickers.add(ticker)
                    del open_positions[ticker]
                    dynamic_stop_counts.pop(ticker, None)
                    exited_tickers_at_time.add(ticker)
                    continue

            if not base._is_within_buy_window(created_at, start_buy_time, stop_buy_time):
                scan_rows = [row for row in rows_at_time if row.phase == "scan_candidate"]
                if scan_rows:
                    previous_scan_prices = {
                        base._ticker_key(row.ticker): int((row.current_price or row.price) or 0)
                        for row in scan_rows
                        if int((row.current_price or row.price) or 0) > 0
                    }
                continue

            scan_rows = [row for row in rows_at_time if row.phase == "scan_candidate"]
            if not scan_rows:
                continue

            allowed_tickers = None
            if use_selected_signals:
                allowed_tickers = selected_tickers_by_timestamp.get((session_date, created_at))
                if allowed_tickers is None:
                    previous_scan_prices = {
                        base._ticker_key(row.ticker): int((row.current_price or row.price) or 0)
                        for row in scan_rows
                        if int((row.current_price or row.price) or 0) > 0
                    }
                    continue

            effective_trend_allowed_tickers = trend_allowed_tickers
            if effective_trend_allowed_tickers is not None and allowed_tickers is not None:
                effective_trend_allowed_tickers = effective_trend_allowed_tickers & allowed_tickers

            effective_position_limit = (
                min(top_n_per_day, session_plan.position_limit)
                if top_n_per_day > 0
                else session_plan.position_limit
            )
            if not base._refill_is_allowed(
                active_position_count=len(open_positions),
                position_limit=effective_position_limit,
                allow_refill_empty_slots=allow_refill_empty_slots,
                refill_min_empty_fraction=refill_min_empty_fraction,
            ):
                previous_scan_prices = {
                    base._ticker_key(row.ticker): int((row.current_price or row.price) or 0)
                    for row in scan_rows
                    if int((row.current_price or row.price) or 0) > 0
                }
                continue

            available_slots = max(0, effective_position_limit - len(open_positions))
            if available_slots <= 0:
                continue
            affordable_slots = (
                available_cash // session_plan.slot_budget_per_stock
                if session_plan.slot_budget_per_stock > 0
                else 0
            )
            planned_buy_count = min(available_slots, affordable_slots)
            if max_buy_count > 0:
                planned_buy_count = min(planned_buy_count, max_buy_count)
            if planned_buy_count <= 0:
                previous_scan_prices = {
                    base._ticker_key(row.ticker): int((row.current_price or row.price) or 0)
                    for row in scan_rows
                    if int((row.current_price or row.price) or 0) > 0
                }
                continue

            candidates, used_threshold = base._pick_candidates_for_entry_with_fallback(
                scan_rows,
                min_expected_return_percent=min_expected_return_percent,
                fallback_min_expected_return_percents=fallback_min_expected_return_percents,
                max_spread_percent=max_spread_percent,
                top_ratio=top_ratio,
                spread_expected_return_multiplier=spread_expected_return_multiplier,
                min_prev_day_change_percent=min_prev_day_change_percent,
                max_prev_day_change_percent=max_prev_day_change_percent,
                active_tickers=set(open_positions) | exited_tickers_at_time,
                blocked_tickers=blocked_reentry_tickers,
                allowed_tickers=allowed_tickers,
                trend_allowed_tickers=effective_trend_allowed_tickers,
                previous_scan_prices=previous_scan_prices,
                max_intraday_jump_from_prev_scan_percent=max_intraday_jump_from_prev_scan_percent,
                allow_refill_empty_slots=allow_refill_empty_slots,
            )
            if candidates and used_threshold != min_expected_return_percent:
                print(
                    f"Replay fallback threshold used for {session_date} {created_at}: "
                    f"{used_threshold:.2f} instead of {min_expected_return_percent:.2f}; "
                    f"candidates={len(candidates)}"
                )

            previous_scan_prices = {
                base._ticker_key(row.ticker): int((row.current_price or row.price) or 0)
                for row in scan_rows
                if int((row.current_price or row.price) or 0) > 0
            }

            candidate_models: list[base.Candidate] = []
            candidate_rows_by_ticker: dict[str, base.TraceRow] = {}
            for candidate in candidates:
                entry_price = candidate.current_price or candidate.price
                if entry_price <= 0:
                    continue
                if (
                    max_orderbook_ask_depth_ratio > 0
                    and candidate.ask_depth_5_amount_krw <= 0
                    and missing_ask_depth_policy == "skip"
                ):
                    continue
                candidate_models.append(
                    base.Candidate(
                        ticker=candidate.ticker,
                        price=entry_price,
                        expect_price=candidate.expect_price,
                        expect_revenue_percent=candidate.expect_revenue_percent,
                        spread_percent=candidate.spread_percent,
                        ask_depth_5_amount_krw=candidate.ask_depth_5_amount_krw,
                    )
                )
                candidate_rows_by_ticker[candidate.ticker] = candidate

            selected_targets = base.select_affordable_targets(
                candidate_models,
                max_buy_count=planned_buy_count,
                available_cash_krw=available_cash,
                budget_per_stock_krw=session_plan.slot_budget_per_stock,
                sell_tick_offset=sell_tick_offset,
                max_orderbook_ask_depth_ratio=max_orderbook_ask_depth_ratio,
            )

            for candidate_model in selected_targets:
                candidate = candidate_rows_by_ticker.get(candidate_model.ticker)
                if candidate is None:
                    continue
                entry_price = candidate.current_price or candidate.price
                quantity = base.calc_order_quantity(candidate_model, session_plan.slot_budget_per_stock)
                estimated_cost = quantity * entry_price
                if quantity <= 0 or estimated_cost <= 0 or estimated_cost > available_cash:
                    setattr(candidate_model, "planned_budget_krw", 0)
                    continue
                if max_orderbook_ask_depth_ratio > 0 and candidate.ask_depth_5_amount_krw > 0:
                    if not base.passes_orderbook_ask_depth_ratio(
                        candidate_model,
                        estimated_cost_krw=estimated_cost,
                        max_orderbook_ask_depth_ratio=max_orderbook_ask_depth_ratio,
                    ):
                        setattr(candidate_model, "planned_budget_krw", 0)
                        continue

                target_price = base._resolve_target_price(candidate.expect_price, sell_tick_offset, entry_price)
                if target_price <= 0:
                    target_price = int(entry_price * (1 + take_profit_percent / 100))
                open_positions[candidate.ticker] = base.ReplayPosition(
                    entry=candidate,
                    quantity=quantity,
                    invested_amount=estimated_cost,
                    entry_price=entry_price,
                    target_price=target_price,
                    stop_loss_price=base._resolve_stop_loss_price(
                        entry_price=entry_price,
                        expect_price=candidate.expect_price,
                        stop_loss_percent=stop_loss_percent,
                        stop_loss_tick_count=stop_loss_tick_count,
                        stop_loss_tick_multiplier=stop_loss_tick_multiplier,
                    ),
                    actual_exit_override=(
                        actual_exit_queues.get((session_date, candidate.ticker), []).pop(0)
                        if actual_exit_queues.get((session_date, candidate.ticker))
                        else None
                    ),
                )
                dynamic_stop_counts[candidate.ticker] = 0
                available_cash -= estimated_cost
                setattr(candidate_model, "planned_budget_krw", 0)

        for ticker, position in open_positions.items():
            exit_row = last_row_by_ticker.get(ticker, position.entry)
            exit_price = exit_row.current_price or exit_row.price or position.entry_price
            trades.append(
                base.BacktestTrade(
                    session_date=session_date,
                    ticker=ticker,
                    entry_time=position.entry.created_at,
                    exit_time=exit_row.created_at,
                    quantity=position.quantity,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    buy_amount_krw=position.invested_amount,
                    sell_amount_krw=position.quantity * exit_price,
                    exit_reason="force_exit_last_trace",
                    pnl_percent=base._realized_pnl_percent(position.entry_price, exit_price),
                )
            )

    return trades


def main() -> None:
    custom_parser = argparse.ArgumentParser(add_help=False)
    custom_parser.add_argument("--dynamic-expect-stop-threshold", type=float, default=0.0)
    custom_parser.add_argument("--dynamic-expect-stop-consecutive", type=int, default=3)
    custom_args, remaining = custom_parser.parse_known_args()

    original_argv = sys.argv
    try:
        sys.argv = [original_argv[0], *remaining]
        args = base.parse_args()
    finally:
        sys.argv = original_argv

    if args.out == "Daily_bot/backtest/results/backtest_replay.csv":
        args.out = "Daily_bot/backtest/results/backtest_replay_dynamic_expect_debounce.csv"

    logs_dir = Path(args.logs_dir) if args.logs_dir else None
    resolved_db_path = base.resolve_replay_db_path(Path(args.db), logs_dir)
    if resolved_db_path != Path(args.db):
        print(f"Rebuilt replay DB from logs: {resolved_db_path}")

    session_capital_by_day = base.load_session_capital_bases(resolved_db_path)
    trend_ok_tickers_by_day, trend_filter_days = (
        base.load_trend_ok_tickers_by_day(logs_dir)
        if args.trend_filter_enabled and logs_dir
        else ({}, set())
    )

    selected_signals_override = None
    actual_exit_overrides_by_ticker = (
        base.load_actual_exit_overrides_from_fills(logs_dir)
        if args.use_actual_fill_exits
        else None
    )
    if args.use_selected_signals:
        loaded_selected_signals = base.load_selected_signals(resolved_db_path)
        if loaded_selected_signals:
            selected_signals_override = loaded_selected_signals
        else:
            inferred_selected_signals = base.infer_selected_signals_from_fill_audit(
                db_path=resolved_db_path,
                logs_dir=logs_dir,
            )
            if inferred_selected_signals:
                selected_signals_override = inferred_selected_signals
                print(
                    "Inferred selected signals from fill logs because signals table was empty: "
                    f"{len(inferred_selected_signals)} rows"
                )

    result = run_backtest_dynamic_expect_debounce(
        db_path=resolved_db_path,
        min_expected_return_percent=args.min_expected_return,
        max_spread_percent=args.max_spread,
        min_prev_day_change_percent=args.min_prev_day_change,
        top_n_per_day=args.top_n,
        max_prev_day_change_percent=args.max_prev_day_change,
        stop_loss_percent=args.stop_loss,
        dynamic_expect_stop_threshold_percent=custom_args.dynamic_expect_stop_threshold,
        dynamic_expect_stop_consecutive=custom_args.dynamic_expect_stop_consecutive,
        stop_loss_tick_count=args.stop_loss_tick_count,
        stop_loss_tick_multiplier=args.stop_loss_tick_multiplier,
        use_selected_signals=args.use_selected_signals,
        take_profit_percent=args.take_profit,
        top_ratio=args.top_ratio,
        sell_tick_offset=args.sell_tick_offset,
        session_capital_by_day=session_capital_by_day,
        default_starting_capital_krw=args.starting_capital_krw,
        min_slot_count=args.min_slot_count,
        max_slot_count=args.max_slot_count,
        slot_budget_unit_krw=args.slot_budget_unit_krw,
        max_budget_per_stock_krw=args.max_budget_per_stock_krw,
        max_position_count=args.max_position_count,
        max_buy_count=args.max_buy_count,
        target_budget_ratio_per_stock=args.target_budget_ratio_per_stock,
        start_buy_time=args.start_buy_time,
        stop_buy_time=args.stop_buy_time,
        force_sell_time=args.force_sell_time,
        max_hold_seconds_before_exit=args.max_hold_seconds_before_exit,
        spread_expected_return_multiplier=args.spread_expected_return_multiplier,
        max_intraday_jump_from_prev_scan_percent=args.max_intraday_jump_from_prev_scan,
        fallback_min_expected_return_percents=args.fallback_min_expected_returns,
        max_orderbook_ask_depth_ratio=args.max_orderbook_ask_depth_ratio,
        missing_ask_depth_policy=args.missing_ask_depth_policy,
        allow_refill_empty_slots=args.allow_refill_empty_slots,
        refill_min_empty_fraction=args.refill_min_empty_fraction,
        block_stop_loss_reentry_same_day=args.block_stop_loss_reentry_same_day,
        trend_filter_enabled=args.trend_filter_enabled,
        trend_ok_tickers_by_day=trend_ok_tickers_by_day,
        trend_filter_days=trend_filter_days,
        orderbook_levels_per_side=args.orderbook_levels_per_side,
        orderbook_bid_linear_decay_min_weight=args.orderbook_bid_linear_decay_min_weight,
        orderbook_ask_linear_decay_min_weight=args.orderbook_ask_linear_decay_min_weight,
        selected_signals_override=selected_signals_override,
        actual_exit_overrides_by_ticker=actual_exit_overrides_by_ticker,
    )

    report_paths = base.write_backtest_reports(
        out_path=Path(args.out),
        trades=result,
        session_capital_by_day=session_capital_by_day,
        default_starting_capital_krw=args.starting_capital_krw,
        min_slot_count=args.min_slot_count,
        max_slot_count=args.max_slot_count,
        slot_budget_unit_krw=args.slot_budget_unit_krw,
        max_budget_per_stock_krw=args.max_budget_per_stock_krw,
        max_position_count=args.max_position_count,
        target_budget_ratio_per_stock=args.target_budget_ratio_per_stock,
    )
    base.print_summary(result)
    dynamic_stop_count = sum(trade.exit_reason == "dynamic_expect_stop" for trade in result)
    print(
        "dynamic_expect_stop="
        f"threshold={custom_args.dynamic_expect_stop_threshold:.4f}% "
        f"consecutive={max(1, custom_args.dynamic_expect_stop_consecutive)} "
        f"exits={dynamic_stop_count}"
    )
    print(f"wrote {report_paths['trades']}")
    print(f"wrote {report_paths['daily_rev']}")
    print(f"wrote {report_paths['daily_audit']}")


if __name__ == "__main__":
    main()
