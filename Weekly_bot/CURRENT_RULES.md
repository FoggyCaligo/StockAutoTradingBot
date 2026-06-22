# Weekly Bot Current Rules

This file reflects the current operating intent of `Weekly_bot` and takes precedence over older brainstorming notes.

## Schedule

- Monday 10:00: first buy
- Monday to Friday during market hours: monitor take-profit / stop-loss
- Friday 14:50: forced liquidation

## Entry Rules

- Universe: KOSPI200
- Market cap: at least KRW 300 billion
- Daily change: `-7.0%` to `-2.0%`
- Turnover: at least KRW 1 billion
- Envelope: current price must be below the configured lower envelope
- Trend: `MA30 up` or `MA50 up` or (`MA120 up` and `price > MA120`)
- Spread filter: currently disabled unless explicitly configured

## Position Sizing

- Use 90% of deposit cash
- Maximum 10 names
- `min_positions=5` is a soft target, not a hard requirement
- The actual number of bought names is computed dynamically from deposit cash and candidate prices
- If deposit cash cannot fill 5 names, buy as many affordable names as possible
- Allocation is equal-weighted across the finally selected names

## Exit Rules

- Take profit: `+5.0%`
- Stop loss: `-5.0%`
- Monitoring compares current price against the held average price (`avg_price`)
- Any remaining position is force-liquidated on Friday at 14:50
