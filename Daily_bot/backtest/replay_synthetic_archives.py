"""Bounded-memory daily adapter to the unchanged canonical refill runner.

Capital resets to a fixed basis each day, just as the canonical replay does.
No trading strategy or execution rule is reimplemented here.
"""
from __future__ import annotations
import argparse
import contextlib
import csv
import hashlib
import io
import json
import math
import sys
import time
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from zipfile import ZipFile
from Daily_bot.backtest import replay_market_traces as base
from Daily_bot.backtest import replay_refill_threshold as runner


def load_csv_bytes(data):
    rows=[]
    for r in csv.DictReader(io.StringIO(data.decode('utf-8-sig'))):
        prev=base._to_int(r.get('prev_close_price'));cur=base._to_int(r.get('current_price'))
        if prev>0 and cur>0:r['prev_day_change_percent']=(cur-prev)/prev*100
        ints=['selected','price','prev_close_price','current_price','expect_price','ask_depth_5_amount_krw']
        floats=['expect_revenue_percent','spread_percent','prev_day_change_percent']
        rows.append(base.TraceRow(session_date=r['session_date'],ticker=r['ticker'],phase=r['phase'],
            created_at=base._normalize_created_at(r['session_date'],r.get('scan_cycle_at') or r['created_at']),
            raw_json=r.get('raw_json') or '{}',**{k:base._to_int(r.get(k)) for k in ints},
            **{k:base._to_float(r.get(k)) for k in floats}))
    return sorted(rows,key=lambda r:(r.session_date,r.created_at,r.ticker))


def replay_day(rows,capital=1000000,fill_model='standard'):
    captured={}
    originals={name:getattr(base,name) for name in ['load_traces','resolve_replay_db_path','load_session_capital_bases','write_backtest_reports']}
    old_argv=sys.argv
    def capture(**kwargs):
        captured['result']=kwargs['trades']
        return {k:Path('isolated-memory-replay') for k in ['trades','daily_rev','daily_audit']}
    base.load_traces=lambda path:rows
    base.resolve_replay_db_path=lambda *args:Path('isolated-memory-replay.sqlite3')
    base.load_session_capital_bases=lambda path:{}
    base.write_backtest_reports=capture
    sys.argv=['replay_refill_threshold','--refill-min-expected-return','0.90','--starting-capital-krw',str(capital),
        '--ignore-selected-signals','--ignore-actual-fill-exits','--trend-filter-disabled','--fill-model',fill_model]
    if fill_model=='conservative':sys.argv+=['--max-tape-gap-seconds','15']
    try:
        with contextlib.redirect_stdout(io.StringIO()):runner.main()
        return captured['result']
    finally:
        sys.argv=old_argv
        for name,fn in originals.items():setattr(base,name,fn)


def summarize(days,trades,capital):
    net=sum(d['net_pnl_krw'] for d in days);equity=peak=float(capital);mdd=0
    for day in days:
        equity+=day['net_pnl_krw'];peak=max(peak,equity);mdd=min(mdd,(equity/peak-1)*100)
    return dict(sessions=len(days),trades=len(trades),net_profit_krw=net,
        net_return_on_initial_capital_percent=net/capital*100,
        daily_mean_krw=net/len(days),winning_days=sum(d['net_pnl_krw']>0 for d in days),
        losing_days=sum(d['net_pnl_krw']<0 for d in days),no_trade_days=sum(d['trades']==0 for d in days),
        net_winning_trades=sum(t['net_pnl_krw']>0 for t in trades),
        net_trade_win_rate_percent=100*sum(t['net_pnl_krw']>0 for t in trades)/len(trades) if trades else 0,
        daily_close_max_drawdown_percent=mdd,gross_pnl_krw=sum(d['gross_pnl_krw'] for d in days),
        fees_and_tax_krw=sum(d['cost_krw'] for d in days),
        exit_reasons=dict(Counter(t['exit_reason'] for t in trades)),
        unfilled_mark_positions=sum(t['exit_reason']=='unfilled_end_mark' for t in trades),
        marked_net_profit_krw=sum(d.get('marked_net_pnl_krw',d['net_pnl_krw']) for d in days),
        unresolved_stock_days=sum(len(d.get('unresolved_inventory',[])) for d in days))


def reprice_standard_report(report, sell_tax_rate=.002):
    """Exact cost update: standard replay selection/gross cash does not read tax."""
    if report['fill_model']!='standard' or report['summaries']['all']['unfilled_mark_positions']:
        raise ValueError('repricing requires standard replay with no unfilled end marks')
    if not math.isfinite(sell_tax_rate) or not 0<=sell_tax_rate<1:
        raise ValueError('invalid sell tax rate')
    report=deepcopy(report)
    for trade in report['trades']:
        cost=(trade['buy_amount_krw']+trade['sell_amount_krw'])*report['fee_rate_per_side']+trade['sell_amount_krw']*sell_tax_rate
        trade['cost_krw']=cost;trade['net_pnl_krw']=trade['gross_pnl_krw']-cost
    for day in report['days']:
        parts=[t for t in report['trades'] if t['session_date']==day['date']]
        day['cost_krw']=sum(t['cost_krw'] for t in parts)
        day['net_pnl_krw']=sum(t['net_pnl_krw'] for t in parts)
        day['marked_net_pnl_krw']=day['net_pnl_krw']
    report['legacy_sell_tax_rate']=report['sell_tax_rate'];report['sell_tax_rate']=sell_tax_rate
    report['cost_method']='Exact ledger repricing; standard trade selection and gross cash are tax-independent'
    for label,days in [('all',report['days']),('weak_bull',[d for d in report['days'] if d['date']<'0001-04-01']),('strong_bear',[d for d in report['days'] if d['date']>='0001-04-01'])]:
        if not days:continue
        dates={d['date'] for d in days}
        report['summaries'][label]=summarize(days,[t for t in report['trades'] if t['session_date'] in dates],report['capital_basis_krw'])
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--wait',action='store_true');ap.add_argument('--resume',action='store_true')
    ap.add_argument('--capital',type=int,default=1000000)
    ap.add_argument('--sell-tax-rate',type=float,default=.002,help='2026 ordinary-share total sell tax; explicit historical overrides allowed')
    ap.add_argument('--fill-model',choices=['standard','conservative'],default='standard');args=ap.parse_args()
    if not math.isfinite(args.sell_tax_rate) or not 0<=args.sell_tax_rate<1:raise ValueError('invalid sell tax rate')
    base.DEFAULT_SELL_TAX_RATE=args.sell_tax_rate
    days=[];trades=[];sources=[]
    args.output.mkdir(parents=True,exist_ok=True)
    identity=dict(input=str(args.input.resolve()),capital=args.capital,fill_model=args.fill_model,sell_tax_rate=args.sell_tax_rate)
    if args.resume and (args.output/'progress.json').exists():
        saved=json.loads((args.output/'progress.json').read_text())
        if saved.get('identity')!=identity:raise ValueError('resume requires identical input/capital/fill/tax settings')
        days=saved['days'];trades=saved['trades']
    completed={d['date'] for d in days}
    for month in range(1,7):
        marker=args.input/f'dailybot_korean_mixed_0001_{month+1:02d}.zip' if month<6 else args.input/'manifest.json'
        if args.wait:
            while not marker.exists():time.sleep(2)
        path=args.input/f'dailybot_korean_mixed_0001_{month:02d}.zip'
        sources.append(dict(path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        with ZipFile(path) as z:
            for name in sorted(z.namelist()):
                if not name.endswith('.csv'):continue
                suffix=name.removesuffix('.csv')[-8:];day_key=suffix[:4]+'-'+suffix[4:6]+'-'+suffix[6:]
                if day_key in completed:continue
                rows=load_csv_bytes(z.read(name));day=rows[0].session_date
                result=replay_day(rows,args.capital,args.fill_model)
                daily=[]
                for t in result:
                    item=asdict(t);gross=t.sell_amount_krw-t.buy_amount_krw
                    cost=(t.buy_amount_krw+t.sell_amount_krw)*base.DEFAULT_FEE_RATE+t.sell_amount_krw*base.DEFAULT_SELL_TAX_RATE
                    item.update(gross_pnl_krw=gross,cost_krw=cost,net_pnl_krw=gross-cost)
                    daily.append(item)
                trades.extend(daily)
                entry=dict(date=day,rows=len(rows),trades=len(daily),
                    gross_pnl_krw=sum(t['gross_pnl_krw'] for t in daily),cost_krw=sum(t['cost_krw'] for t in daily),
                    net_pnl_krw=sum(t['net_pnl_krw'] for t in daily),unresolved_inventory=getattr(result,'unresolved_inventory',[]))
                entry['marked_net_pnl_krw']=(sum(x['marked_net_pnl'] for x in result.execution_sessions) if hasattr(result,'execution_sessions') else entry['net_pnl_krw'])
                days.append(entry)
                (args.output/'progress.json').write_text(json.dumps(dict(identity=identity,days=days,trades=trades),indent=2))
                print(f'{day}: trades={len(daily)}, net={entry["net_pnl_krw"]:.2f}',flush=True)
    assert len(days)==130
    summaries={}
    for label,subset in [('all',days),('weak_bull',[d for d in days if d['date']<'0001-04-01']),('strong_bear',[d for d in days if d['date']>='0001-04-01'])]:
        dates={d['date'] for d in subset};summaries[label]=summarize(subset,[t for t in trades if t['session_date'] in dates],args.capital)
    report=dict(capital_basis_krw=args.capital,capital_policy='fixed each session; no compounding',
        fee_rate_per_side=base.DEFAULT_FEE_RATE,sell_tax_rate=base.DEFAULT_SELL_TAX_RATE,
        fill_model=args.fill_model,sources=sources,summaries=summaries,days=days,trades=trades)
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    for name,values in [('days',days),('trades',trades)]:
        with (args.output/f'{name}.csv').open('w',encoding='utf-8-sig',newline='') as fp:
            if values:
                writer=csv.DictWriter(fp,fieldnames=list(values[0]));writer.writeheader();writer.writerows(values)
    print(json.dumps(summaries,indent=2),flush=True)


if __name__=='__main__':main()
