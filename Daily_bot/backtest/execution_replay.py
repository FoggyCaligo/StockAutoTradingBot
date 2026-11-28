"""Causal, quantity-limited execution for recorded snapshot replay.

This is a conservative approximation, not reconstruction of exchange FIFO.
Queue progress requires reported executions or optional aggregate trade-through
evidence. Quote cancellation alone never advances the queue. No price-touch fills.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class ExecutionConfig:
    latency_seconds: float = 1.0
    buy_timeout_seconds: float = 30.0
    depth_participation: float = 0.10
    queue_multiplier: float = 1.0
    max_tape_gap_seconds: float = 5.0
    aggregate_trade_through: bool = False
    allow_signal_snapshot_buy: bool = False
    buy_fill_poll_seconds: float = 2.0
    replay_recorded_buy_orders: bool = False

    def __post_init__(self):
        values = [self.latency_seconds, self.buy_timeout_seconds, self.depth_participation,
                  self.queue_multiplier, self.max_tape_gap_seconds, self.buy_fill_poll_seconds]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('execution parameters must be finite')
        if self.latency_seconds < 0 or self.buy_timeout_seconds <= self.latency_seconds:
            raise ValueError('0 <= latency < buy timeout is required')
        if self.buy_fill_poll_seconds <= 0:
            raise ValueError('buy fill poll interval must be positive')
        if self.replay_recorded_buy_orders and self.allow_signal_snapshot_buy:
            raise ValueError('recorded buy orders require post-submission quotes')
        if not 0 < self.depth_participation <= 1 or self.queue_multiplier < 0 or self.max_tape_gap_seconds <= 0:
            raise ValueError('participation in (0,1], queue multiplier >= 0, tape gap > 0 required')


@dataclass
class ReplayOrder:
    order_id: int
    ticker: str
    side: str
    quantity: int
    limit_price: int | None
    submitted_at: datetime
    active_at: datetime
    reason: str
    remaining: int = 0
    joined: bool = False
    queue_ahead: float = 0

    def __post_init__(self):
        self.remaining = self.quantity


class SnapshotMatcher:
    """One frame per symbol/time; do not replenish consumed unchanged quotes."""
    def __init__(self, config):
        self.config = config
        self.frames = {}
        self.events = []
        self.sequence = 0

    def submit(self, ticker, side, quantity, price, now, reason, *, broker_order_id=None):
        self.sequence += 1
        order = ReplayOrder(self.sequence, ticker, side, quantity, price, now,
                            now+timedelta(seconds=self.config.latency_seconds), reason)
        self.events.append(dict(event='submit', time=now.isoformat(), ticker=ticker,
            order_id=order.order_id, side=side, quantity=quantity, price=price,
            reason=reason, active_at=order.active_at.isoformat(), broker_order_id=broker_order_id))
        return order

    def frame(self, row):
        from Daily_bot.backtest import replay_market_traces as base
        now = base._parse_timestamp(row.created_at)
        old = self.frames.get(row.ticker)
        if old and old['now'] == now:
            return old
        try:
            raw = json.loads(row.raw_json or '{}')
        except (ValueError, TypeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        levels = {side: [(x.price, x.volume) for x in base._extract_hoga_levels_from_raw(raw, side, 10)]
                  for side in ['bid', 'ask']}
        pools = {}
        for side, quotes in levels.items():
            for price, quantity in quotes:
                key = (side, price)
                previous = old['pools'].get(key) if old else None
                allowance = int(quantity*self.config.depth_participation)
                if previous:
                    # Only observed positive replenishment restores consumed depth.
                    restored = int(max(0, quantity-previous[0])*self.config.depth_participation)
                    allowance = min(allowance, previous[1]+restored)
                pools[key] = [quantity, allowance]
        good_interval = bool(old and 0 < (now-old['now']).total_seconds() <= self.config.max_tape_gap_seconds)
        tape = []
        if good_interval:
            for item in raw.get('executions', []) or []:
                if not isinstance(item, dict):
                    continue
                price, qty = int(item.get('price', 0)), int(item.get('quantity', 0))
                aggressor = item.get('aggressor_side')
                if price > 0 and qty > 0 and aggressor in {'buy', 'sell'}:
                    tape.append([price, qty, aggressor, 'execution'])
            # Aggregate flow does not reveal price-by-price or aggressor sequence.
            # Opt-in stress proxy only; strict trade-through, never mere touch.
            if not tape and self.config.aggregate_trade_through:
                qty = max(0, int(raw.get('tick_volume', 0) or 0))
                price = row.current_price or row.price
                if qty and price > 0:
                    tape.append([price, qty, None, 'aggregate_trade_through'])
        result = dict(now=now, levels=levels, pools=pools, tape=tape)
        self.frames[row.ticker] = result
        return result

    def match(self, order, frame):
        now = frame['now']
        signal_buy = (
            self.config.allow_signal_snapshot_buy
            and order.side == 'buy'
            and now == order.submitted_at
        )
        if order.remaining <= 0 or (
            not signal_buy and (now <= order.submitted_at or now < order.active_at)
        ):
            return []
        fills = []
        if not signal_buy and not order.joined:
            # The interval's tape precedes this observed arrival: never reuse it.
            own = frame['levels']['bid' if order.side == 'buy' else 'ask']
            order.queue_ahead = self.config.queue_multiplier*sum(q for p,q in own if p == order.limit_price)
            order.joined = True
        elif not signal_buy and order.limit_price is not None:
            for event in frame['tape']:
                price, qty, aggressor, evidence = event
                qualifies = ((order.side == 'buy' and price <= order.limit_price and aggressor == 'sell')
                    or (order.side == 'sell' and price >= order.limit_price and aggressor == 'buy'))
                if evidence == 'aggregate_trade_through':
                    qualifies = price < order.limit_price if order.side == 'buy' else price > order.limit_price
                if not qualifies or qty <= 0:
                    continue
                ahead = min(order.queue_ahead, qty)
                order.queue_ahead -= ahead
                event[1] -= int(ahead)
                quantity = min(order.remaining, int(event[1]*self.config.depth_participation))
                if quantity:
                    event[1] -= quantity
                    order.remaining -= quantity
                    fills.append((quantity, order.limit_price, evidence))
        opposite = 'ask' if order.side == 'buy' else 'bid'
        for price, _ in frame['levels'][opposite]:
            if not order.remaining:
                break
            if order.limit_price is not None and ((order.side == 'buy' and price > order.limit_price)
                    or (order.side == 'sell' and price < order.limit_price)):
                break
            pool = frame['pools'][(opposite, price)]
            quantity = min(order.remaining, pool[1])
            if quantity:
                pool[1] -= quantity
                order.remaining -= quantity
                evidence = 'signal_snapshot_depth' if signal_buy else 'displayed_depth'
                fills.append((quantity, price, evidence))
        for quantity, price, evidence in fills:
            self.events.append(dict(event='fill', time=now.isoformat(), ticker=order.ticker,
                order_id=order.order_id, side=order.side, quantity=quantity, price=price,
                reason=order.reason, evidence=evidence, remaining=order.remaining,
                queue_ahead=order.queue_ahead))
        return fills

    def cancel(self, order, now, reason):
        self.events.append(dict(event='cancel', time=now.isoformat(), ticker=order.ticker,
            order_id=order.order_id, side=order.side, quantity=order.remaining,
            price=order.limit_price, reason=reason, observed_active_quote=order.joined))
        order.remaining = 0


class ExecutionResult(list):
    def __init__(self, config):
        super().__init__()
        self.execution_config = asdict(config)
        self.execution_events = []
        self.execution_sessions = []
        self.unresolved_inventory = []


@dataclass
class Position:
    entry: object
    buy: ReplayOrder
    target: int
    stop: float
    bought: int = 0
    buy_amount: int = 0
    sold: int = 0
    sell_amount: int = 0
    first_fill: str = ''
    buy_confirm_at: datetime | None = None
    last_sell: str = ''
    sell: ReplayOrder | None = None
    dynamic_count: int = 0
    anchor_count: int = 0
    exit_reason: str = ''


def recorded_buy_submissions(rows):
    submissions = {}
    for row in rows:
        if row.phase != 'buy_order_quote':
            continue
        metadata = json.loads(row.raw_json).get('buy_order', {})
        order_id = metadata.get('order_id')
        if order_id and order_id not in submissions:
            stamp = datetime.fromisoformat(metadata['submitted_at']).isoformat(
                sep=' ', timespec='microseconds',
            )
            if stamp[:10] != row.session_date or stamp >= row.created_at:
                raise ValueError(f'Invalid submission time for recorded buy {order_id}')
            submissions[order_id] = (stamp, row, metadata)
    return submissions


def run_execution_replay(arguments, config):
    from Daily_bot.backtest import replay_market_traces as base
    a = dict(arguments)
    a.pop('execution_config', None)
    if a.get('actual_exit_overrides_by_ticker'):
        raise ValueError('conservative execution cannot use actual-fill exit overrides; use --ignore-actual-fill-exits')
    if config.replay_recorded_buy_orders and a['use_selected_signals']:
        raise ValueError('recorded buy orders require --ignore-selected-signals')
    traces = base.apply_orderbook_level_limit(base.load_traces(a['db_path']),
        levels_per_side=a['orderbook_levels_per_side'], sell_tick_offset=a['sell_tick_offset'],
        bid_linear_decay_min_weight=a['orderbook_bid_linear_decay_min_weight'],
        ask_linear_decay_min_weight=a['orderbook_ask_linear_decay_min_weight'])
    signals = a.get('selected_signals_override')
    if signals is None:
        signals = base.load_selected_signals(a['db_path']) if a['use_selected_signals'] else []
    selected = base._selected_tickers_by_timestamp(signals)
    result = ExecutionResult(config)
    if config.replay_recorded_buy_orders and not any(row.phase == 'buy_order_quote' for row in traces):
        raise ValueError('no post-submission buy quotes in replay data')
    fee, tax = base.DEFAULT_FEE_RATE, base.DEFAULT_SELL_TAX_RATE
    for day, rows in base.group_by_session(traces).items():
        plan = base.resolve_session_capital_plan(**{k:a[k] for k in ['session_capital_by_day','default_starting_capital_krw',
                'min_slot_count','max_slot_count','slot_budget_unit_krw','max_budget_per_stock_krw',
                'max_position_count','target_budget_ratio_per_stock']}, session_date=day)
        if min(plan.position_limit, plan.slot_budget_per_stock, plan.session_capital_basis) <= 0:
            continue
        cash = float(plan.session_capital_basis)
        matcher = SnapshotMatcher(config)
        positions = {}
        blocked = set()
        previous_scan = {}
        by_time = {}
        last_rows = {}
        recorded_entries = {}
        if config.replay_recorded_buy_orders:
            for stamp, row, order in recorded_buy_submissions(rows).values():
                recorded_entries.setdefault(stamp, []).append((row, order))
                by_time[stamp] = []
        for row in rows:
            if config.replay_recorded_buy_orders and row.phase == 'scan_candidate':
                continue
            by_time.setdefault(row.created_at, []).append(row)
            last_rows[row.ticker] = row
        allowed_trend = None
        if a['trend_filter_enabled'] and day in (a.get('trend_filter_days') or set()):
            allowed_trend = (a.get('trend_ok_tickers_by_day') or {}).get(day, set())

        def reserved_cash():
            return sum(p.buy.remaining*p.buy.limit_price*(1+fee) for p in positions.values())

        def apply_buy_fills(position, frame, stamp):
            nonlocal cash
            for quantity, price, _ in matcher.match(position.buy, frame):
                position.bought += quantity
                position.buy_amount += quantity * price
                cash -= quantity * price * (1 + fee)
                if not position.first_fill:
                    position.first_fill = stamp
            if position.buy.remaining == 0 and position.buy_confirm_at is None:
                elapsed = (frame['now'] - position.buy.submitted_at).total_seconds()
                poll_interval = config.buy_fill_poll_seconds
                confirm_seconds = min(
                    math.ceil(elapsed / poll_interval) * poll_interval,
                    config.buy_timeout_seconds,
                )
                position.buy_confirm_at = position.buy.submitted_at + timedelta(seconds=confirm_seconds)

        def submit_confirmed_exit(position, now):
            if position.buy_confirm_at is None or now < position.buy_confirm_at:
                return
            matcher.events.append(dict(
                event='confirm', time=position.buy_confirm_at.isoformat(),
                ticker=position.buy.ticker, order_id=position.buy.order_id, side='buy',
                quantity=position.bought, reason='buy_fill_confirmed',
            ))
            position.sell = matcher.submit(
                position.buy.ticker, 'sell', position.bought, position.target,
                position.buy_confirm_at, 'take_profit',
            )

        def record_trade(position, reason):
            if not position.sold:
                return
            # One report trade per completed position; slot hooks fire once.
            cost = position.buy_amount*position.sold/position.bought
            ep = cost/position.sold
            xp = position.sell_amount/position.sold
            result.append(base.BacktestTrade(day, position.entry.ticker, position.first_fill,
                position.last_sell, position.sold, ep, xp, cost, position.sell_amount,
                reason, base._realized_pnl_percent(ep, xp)))

        def submit_buy(row, quantity, price, expect_price, now, broker_order_id=None):
            target = base._resolve_target_price(expect_price, a['sell_tick_offset'], price)
            if target <= 0:
                target = int(price * (1 + a['take_profit_percent'] / 100))
            stop = base._resolve_stop_loss_price(
                price, expect_price, a['stop_loss_percent'],
                a['stop_loss_tick_count'], a['stop_loss_tick_multiplier'],
            )
            order = matcher.submit(
                row.ticker, 'buy', quantity, price, now, 'entry', broker_order_id=broker_order_id,
            )
            position = Position(row, order, target, stop)
            positions[row.ticker] = position
            return position

        for stamp, frame_rows in sorted(by_time.items()):
            now = base._parse_timestamp(stamp)
            frames = {r.ticker:matcher.frame(r) for r in frame_rows}
            row_map = {r.ticker:r for r in frame_rows}
            exited = set()
            for ticker, p in list(positions.items()):
                row = row_map.get(ticker)
                deadline = p.buy.submitted_at + timedelta(seconds=config.buy_timeout_seconds)
                if p.buy.remaining and now >= deadline:
                    matcher.cancel(p.buy, deadline, 'buy_timeout')
                    p.buy_confirm_at = deadline
                if p.buy.remaining and row is not None:
                    apply_buy_fills(p, frames[ticker], stamp)
                if not p.buy.remaining and not p.bought:
                    del positions[ticker]; exited.add(ticker)
                    continue
                if p.sell is None:
                    submit_confirmed_exit(p, now)
                if row is None or p.sell is None:
                    continue
                for qty, price, _ in matcher.match(p.sell, frames[ticker]):
                    p.sold += qty; p.sell_amount += qty*price; p.last_sell = stamp
                    cash += qty*price*(1-fee-tax)
                if p.sold == p.bought:
                    record_trade(p, p.exit_reason or 'take_profit')
                    del positions[ticker]; exited.add(ticker)
                    continue
                if p.exit_reason:
                    continue
                price = row.current_price or row.price
                expected_sell = base.calc_target_sell_price(row.expect_price, a['sell_tick_offset'])
                expected_return = (expected_sell/price-1)*100 if price>0 else float('inf')
                threshold = a.get('dynamic_expect_stop_threshold_percent')
                p.dynamic_count = p.dynamic_count+1 if threshold is not None and expected_return<=threshold else 0
                anchor = a.get('entry_anchor_stop_threshold_percent')
                avg = p.buy_amount/p.bought
                p.anchor_count = p.anchor_count+1 if anchor is not None and (expected_sell/avg-1)*100<=anchor else 0
                reason = ''
                if base._is_after_time(stamp, a['force_sell_time']): reason = 'force_exit_time'
                elif a['max_hold_seconds_before_exit']>0 and (now-base._parse_timestamp(p.first_fill)).total_seconds()>a['max_hold_seconds_before_exit']: reason = 'time_stop_loss'
                elif price<=p.stop: reason = 'stop_loss'
                elif p.dynamic_count>=max(1,a.get('dynamic_expect_stop_consecutive',3)): reason = 'dynamic_expect_stop'
                elif p.anchor_count>=max(1,a.get('entry_anchor_stop_consecutive',3)): reason = 'entry_anchor_expect_stop'
                if reason:
                    matcher.cancel(p.sell, now, 'replace_exit')
                    p.exit_reason = reason
                    p.sell = matcher.submit(ticker, 'sell', p.bought-p.sold, None, now, reason)
                    if a['block_stop_loss_reentry_same_day'] and reason in {'stop_loss','dynamic_expect_stop','entry_anchor_expect_stop'}:
                        blocked.add(ticker)

            if config.replay_recorded_buy_orders:
                for row, order in recorded_entries.get(stamp, []):
                    price, quantity = int(order['limit_price']), int(order['quantity'])
                    if price <= 0 or quantity <= 0:
                        raise ValueError(f"Invalid recorded buy order {order['order_id']}")
                    if row.ticker in positions or quantity * price * (1 + fee) > cash - reserved_cash():
                        matcher.events.append(dict(
                            event='skip', time=now.isoformat(), ticker=row.ticker, side='buy',
                            broker_order_id=order['order_id'], price=price, quantity=quantity,
                            reason='simulated_position' if row.ticker in positions else 'insufficient_cash',
                        ))
                        continue
                    submit_buy(row, quantity, price, int(order['expect_price']), now, order['order_id'])
                continue

            scans = [r for r in frame_rows if r.phase == 'scan_candidate']
            if not scans:
                continue
            prior_scan = previous_scan
            previous_scan = {base._ticker_key(r.ticker):r.current_price or r.price for r in scans}
            if not base._is_within_buy_window(stamp, a['start_buy_time'], a['stop_buy_time']):
                continue
            allowed = selected.get((day,stamp)) if a['use_selected_signals'] else None
            if a['use_selected_signals'] and allowed is None:
                continue
            limit = min(a['top_n_per_day'],plan.position_limit) if a['top_n_per_day']>0 else plan.position_limit
            if not base._refill_is_allowed(len(positions),limit,a['allow_refill_empty_slots'],a['refill_min_empty_fraction']):
                continue
            free_cash = max(0, cash-reserved_cash())
            count = min(limit-len(positions),int(free_cash//plan.slot_budget_per_stock))
            if a['max_buy_count']>0: count=min(count,a['max_buy_count'])
            if count<=0: continue
            candidates, _ = base._pick_candidates_for_entry_with_fallback(scans,
                **{k:a[k] for k in ['min_expected_return_percent','fallback_min_expected_return_percents',
                    'max_spread_percent','top_ratio','spread_expected_return_multiplier',
                    'min_prev_day_change_percent','max_prev_day_change_percent',
                    'max_intraday_jump_from_prev_scan_percent','allow_refill_empty_slots']},
                active_tickers=set(positions)|exited,blocked_tickers=blocked,allowed_tickers=allowed,
                trend_allowed_tickers=allowed_trend,previous_scan_prices=prior_scan)
            models=[]; candidates_by_ticker={}
            for r in candidates:
                if a['max_orderbook_ask_depth_ratio']>0 and r.ask_depth_5_amount_krw<=0 and a['missing_ask_depth_policy']=='skip':
                    continue
                models.append(base.Candidate(ticker=r.ticker,price=r.current_price or r.price,
                    expect_price=r.expect_price,expect_revenue_percent=r.expect_revenue_percent,
                    spread_percent=r.spread_percent,ask_depth_5_amount_krw=r.ask_depth_5_amount_krw))
                candidates_by_ticker[r.ticker]=r
            targets=base.select_affordable_targets(models,max_buy_count=count,available_cash_krw=int(free_cash),
                budget_per_stock_krw=plan.slot_budget_per_stock,sell_tick_offset=a['sell_tick_offset'],
                max_orderbook_ask_depth_ratio=a['max_orderbook_ask_depth_ratio'])
            for model in targets:
                r=candidates_by_ticker[model.ticker]; price=r.current_price or r.price
                qty=min(base.calc_order_quantity(model,plan.slot_budget_per_stock),
                        int(min(plan.slot_budget_per_stock,max(0,cash-reserved_cash()))/(price*(1+fee))))
                if qty<=0: continue
                position = submit_buy(r, qty, price, r.expect_price, now)
                if config.allow_signal_snapshot_buy:
                    apply_buy_fills(position, frames[r.ticker], stamp)
                    submit_confirmed_exit(position, now)
                setattr(model,'planned_budget_krw',0)

        inventory_value=0.0
        for ticker,p in positions.items():
            row=last_rows[ticker]
            now=base._parse_timestamp(row.created_at)
            if p.buy.remaining: matcher.cancel(p.buy,now,'end_of_data')
            if p.sell and p.sell.remaining: matcher.cancel(p.sell,now,'end_of_data')
            record_trade(p,p.exit_reason or 'partial_exit_end_of_data')
            quantity=p.bought-p.sold
            if quantity:
                frame=matcher.frames.get(ticker,{})
                bids=frame.get('levels',{}).get('bid',[])
                mark=bids[0][0] if bids else 0
                inventory_value+=quantity*mark*(1-fee-tax)
                result.unresolved_inventory.append(dict(session_date=day,ticker=ticker,quantity=quantity,
                    average_buy_price=p.buy_amount/p.bought,mark_bid=mark,mark_only=True))
        result.execution_sessions.append(dict(session_date=day,starting_capital=plan.session_capital_basis,
            ending_cash=cash,unresolved_inventory_mark=inventory_value,
            marked_net_pnl=cash+inventory_value-plan.session_capital_basis,
            unresolved_positions=sum(x['session_date']==day for x in result.unresolved_inventory)))
        result.execution_events.extend(sorted(matcher.events, key=lambda event: event['time']))
    return result


def write_execution_report(path: Path, result):
    if not isinstance(result,ExecutionResult): return
    payload=dict(config=result.execution_config,sessions=result.execution_sessions,
        unresolved_inventory=result.unresolved_inventory,
        limitations=['Snapshot queue estimate, not exchange FIFO reconstruction.',
          'No market response to bot orders; participation is a stress assumption.',
          'Cancellation never treated as traded volume; missing tape gives no passive queue progress.',
          'Unresolved inventory is marked at final best bid, never fabricated as a realized fill.',
          'Trade CSV covers sold quantities; execution sessions include fees and marked inventory.'])
    path.with_name(path.stem+'_execution.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    if result.execution_events:
        fields=sorted({key for row in result.execution_events for key in row})
        with path.with_name(path.stem+'_execution_events.csv').open('w',newline='',encoding='utf-8-sig') as fp:
            w=csv.DictWriter(fp,fieldnames=fields);w.writeheader();w.writerows(result.execution_events)


def config_from_args(args):
    if args.fill_model in {'standard', 'legacy'}:
        if args.replay_recorded_buy_orders:
            raise ValueError('recorded buy orders require --fill-model conservative')
        return None
    return ExecutionConfig(args.order_latency_seconds,args.buy_order_timeout_seconds,
        args.depth_participation,args.queue_multiplier,args.max_tape_gap_seconds,args.aggregate_trade_through,
        args.allow_signal_snapshot_buy, args.buy_fill_poll_seconds, args.replay_recorded_buy_orders)
