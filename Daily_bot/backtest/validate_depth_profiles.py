"""Reproduce small predeclared depth-profile smoke replays, without profit fitting."""
from __future__ import annotations
import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from Daily_bot.backtest.fake_market import ordinary_bull_config, generate_market
from Daily_bot.backtest.diagnose_no_trades import diagnose
from Daily_bot.backtest.replay_db_builder import build_replay_db_from_logs


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--calibration',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--seeds',type=int,nargs='+',default=[901,902])
    ap.add_argument('--ticks-per-day',type=int,default=2280)
    args=ap.parse_args()
    calibrated=json.loads(args.calibration.read_text())
    root=Path(__file__).resolve().parents[2]
    env=dict(os.environ,PYTHONPATH=str(root)+os.pathsep+os.environ.get('PYTHONPATH',''))
    results=[]
    for seed in args.seeds:
        observed_prices=tuple(calibrated.get('initial_price_samples',[]))
        for label,stocks,sigma,prices in [('baseline_10',10,0.,()),
                                  ('depth_only_10',10,calibrated['depth_imbalance_sigma'],()),
                                  ('calibrated_10',10,calibrated['depth_imbalance_sigma'],observed_prices),
                                  ('calibrated_20',20,calibrated['depth_imbalance_sigma'],observed_prices)]:
            cfg=ordinary_bull_config(seed=seed,stocks=stocks,start_date='0001-01-01',
                ticks_per_day=args.ticks_per_day,depth_imbalance_sigma=sigma,
                depth_imbalance_persistence=calibrated['depth_imbalance_persistence'],
                initial_price_samples=prices)
            with tempfile.TemporaryDirectory(prefix='depth-validation-')as tmp:
                market=Path(tmp)/'market'
                generate_market(market,cfg,viewer=False)
                sources=sorted(market.glob('fake_market_traces_*.csv'))
                coverage,_=diagnose(sources)
                db=build_replay_db_from_logs(market,Path(tmp)/'replay.sqlite3',market_pattern='fake_market_traces_*.csv')
                out=Path(tmp)/'trades.csv'
                cmd=[sys.executable,'-m','Daily_bot.backtest.replay_refill_threshold',
                     '--db',str(db),'--refill-min-expected-return','0.90','--fill-model','standard',
                     '--starting-capital-krw','1000000','--ignore-selected-signals',
                     '--ignore-actual-fill-exits','--trend-filter-disabled','--out',str(out)]
                run=subprocess.run(cmd,cwd=root,env=env,text=True,capture_output=True)
                if run.returncode:raise RuntimeError(run.stdout+run.stderr)
                with out.open(encoding='utf-8-sig',newline='')as fp:trades=list(csv.DictReader(fp))
                net=sum(float(t['sell_amount_krw'])*(1-.00015-.0018)-float(t['buy_amount_krw'])*(1+.00015)for t in trades)
                result=dict(seed=seed,case=label,stocks=stocks,ticks_per_day=args.ticks_per_day,
                    depth_sigma=sigma,initial_price_sample_count=len(prices),
                    coverage=coverage['counts'],quantiles=coverage['quantiles'],
                    trades=len(trades),net_profit_krw=net,fill_model='standard',capital_krw=1000000,
                    exit_reasons={reason:sum(t['exit_reason']==reason for t in trades)for reason in {t['exit_reason']for t in trades}})
                results.append(result)
                print(json.dumps(result,ensure_ascii=False),flush=True)
    output=dict(seeds=args.seeds,results=results,calibration=calibrated,
        limitations=['One-session synthetic smoke cases, not a new six-month performance test',
                     'Fixed cases were declared before replay; no seed or parameter chosen on profits',
                     'Depth dispersion comes from a single observed candidate cross-section',
                     'Order-arrival dispersion does not guarantee the same stationary quote distribution',
                     'This comparison explicitly uses ordinary-bull; its baseline cases have no calibration'])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()

