"""Check every generated row, session checksum, price grid and close continuity."""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile
import numpy as np
from Daily_bot.backtest.fake_market import floor_price


def validate(root):
    manifest=json.loads((root/'manifest.json').read_text())
    previous_closes=None
    initial=None
    rows=0
    counts=defaultdict(int)
    returns=defaultdict(list)
    march=None
    for file in manifest['files']:
        with ZipFile(root/file['archive']) as archive:
            data=archive.read(file['path'])
        assert hashlib.sha256(data).hexdigest()==file['sha256'], file['path']
        per_ticker=defaultdict(list)
        last={}
        n=0
        for row in csv.DictReader(io.StringIO(data.decode('utf-8-sig'))):
            n+=1
            ticker=row['ticker']; price=int(row['current_price']);prev=int(row['prev_close_price'])
            raw=json.loads(row['raw_json'])
            assert raw['synthetic'] and raw['model_version']==4
            assert floor_price(price)==price and .7*prev<=price<=1.3*prev
            assert prev==file['previous_close_by_ticker'][ticker]
            if previous_closes is not None:
                assert prev==previous_closes[ticker]
            assert float(row['price'])==price==raw['cur_prc']
            for side in ('buy','sel'):
                quotes=[]
                for i in range(1,11):
                    key=side+'_fpr' if i==1 else f'{side}_{i}th_pre'
                    if key+'_bid' in raw:
                        p=int(raw[key+'_bid']);q=int(raw[key+'_req'])
                        assert floor_price(p)==p and .7*prev<=p<=1.3*prev and q>0
                        quotes.append(p)
                assert quotes==sorted(set(quotes),reverse=side=='buy')
            if int(row['best_bid']) and int(row['best_ask']):
                assert int(row['best_bid'])<int(row['best_ask'])
            fills=raw['executions']
            if fills:
                assert fills[-1]['price']==price
                assert all(f['quantity']>0 and floor_price(f['price'])==f['price'] for f in fills)
            else:
                assert last.get(ticker,prev)==price
            assert raw['tick_volume']==sum(f['quantity'] for f in fills)
            last[ticker]=price
            series=per_ticker[ticker]
            seconds=len(series)*10
            hh=9+seconds//3600;mm=(seconds%3600)//60;ss=seconds%60
            assert row['created_at']==f"{row['session_date']} {hh:02d}:{mm:02d}:{ss:02d}.000000"
            series.append(price)
        assert n==file['rows']==23410
        assert len(per_ticker)==10 and all(len(v)==2341 for v in per_ticker.values())
        assert last==file['closing_price_by_ticker']
        previous_closes=last
        if initial is None:
            initial=file['previous_close_by_ticker']
        if '00010330' in file['path']:
            march=last.copy()
        # Filename-independent date extraction from the manifest's YYYYMMDD suffix.
        month=int(file['path'].removesuffix('.csv')[-4:-2])
        regime='weak_bull' if month<=3 else 'strong_bear'
        counts[regime]+=1;rows+=n
        for horizon in (60,300,900):
            k=horizon//10
            for prices in per_ticker.values():
                prices=np.asarray(prices,dtype=float)[::k]
                returns[(regime,horizon)].extend((np.diff(np.log(prices))*10000).tolist())
    volatility=[]
    for (regime,horizon),values in sorted(returns.items()):
        lo,hi=np.percentile(values,[1,99])
        volatility.append(dict(regime=regime,horizon_seconds=horizon,observations=len(values),
            winsorized_sd_bps=float(np.std(np.clip(values,lo,hi))),
            flat_percent=100*sum(v==0 for v in values)/len(values)))
    assert len(manifest['files'])==130 and rows==3043300
    assert counts=={'weak_bull':65,'strong_bear':65}
    return dict(sessions=130,rows=rows,regime_sessions=dict(counts),
        integrity='all rows passed checksums, date cadence, grid, limits, quotes, fills and close continuity',
        weak_bull_actual_equal_weight_return_percent=100*(np.mean([march[t]/initial[t] for t in initial])-1),
        strong_bear_actual_equal_weight_return_percent=100*(np.mean([last[t]/march[t] for t in initial])-1),
        volatility=volatility)


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    result=validate(args.input)
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
