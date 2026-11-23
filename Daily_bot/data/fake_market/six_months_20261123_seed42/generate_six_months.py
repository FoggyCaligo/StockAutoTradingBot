"""Generate six months of compact, one-second synthetic traces.

Uses the repository's unchanged OrderBook/advance_book implementation. Each
symbol has an independent documented RNG stream so independent symbols can
be generated in parallel while maintaining their own cross-day prices.
"""
from __future__ import annotations
import argparse,csv,gzip,hashlib,io,json,random,sys,time
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import date,timedelta
from pathlib import Path

INITIAL_PRICES=[1500,3500,7500,15000,25000,45000,75000,150000,250000,550000]


def expected_price(bids,asks,round_to_tick):
    if not bids or not asks:
        return None
    # Exact scalar equivalent of apply_orderbook_decay(0,0) + predictor;
    # no mutations or deepcopy of the HogaSnapshot are needed.
    bp=[p for p,q in bids];ap=[p for p,q in asks]
    bv=[round(q*(1-i/(len(bids)-1))) if len(bids)>1 else q for i,(p,q) in enumerate(bids)]
    av=[round(q*(1-i/(len(asks)-1))) if len(asks)>1 else q for i,(p,q) in enumerate(asks)]
    i=j=0
    while i<len(bv) and j<len(av):
        q=min(bv[i],av[j]);bv[i]-=q;av[j]-=q
        if bv[i]<=0:i+=1
        if av[j]<=0:j+=1
    return round_to_tick((bp[min(i,len(bp)-1)]+ap[min(j,len(ap)-1)])//2)


def symbol_seed(seed,ticker):
    return int.from_bytes(hashlib.sha256(f'dailybot-six-months-v1:{seed}:{ticker}'.encode()).digest()[:8],'big')


def worker(job):
    repo,out,ticker,initial,days,seed=job
    sys.path.insert(0,repo)
    from Daily_bot.backtest import fake_market as fm
    from Daily_bot.utils import round_to_tick
    cfg=fm.MarketConfig(seed=seed,days=len(days),ticks_per_day=22800,x=100,n=1,a=5,b=20,c=.1,d=1)
    rng=random.Random(symbol_seed(seed,ticker));previous=fm.floor_price(initial)
    times=[f'{9+s//3600:02d}:{s%3600//60:02d}:{s%60:02d}.000000' for s in range(22800)]
    entries=[]
    for day in days:
        started=time.monotonic();book=fm.OrderBook(previous);noise=0.;hour=-1;hourly=0.;fills_total=0;total_volume=0;total_turnover=0;first_price=None;high=0;low=10**20
        path=Path(out)/day[:7]/f'fake_market_traces_{day.replace("-","")}_{ticker}.csv.gz'
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('wb') as binary,gzip.GzipFile(filename='',mode='wb',fileobj=binary,compresslevel=1,mtime=0) as gz,io.TextIOWrapper(gz,encoding='utf-8-sig',newline='') as text:
            writer=csv.writer(text);writer.writerow(fm.FIELDS)
            for tick in range(22800):
                if tick//3600!=hour:
                    hour=tick//3600;hourly=fm.signed_uniform(rng,cfg.a,cfg.b)*cfg.n
                if tick:
                    noise+=hourly/3600+fm.signed_uniform(rng,cfg.c,cfg.d)*cfg.n
                ref=max(book.lower,min(book.upper,previous*(1+(cfg.x*tick/22800+noise)/10000)))
                fills=fm.advance_book(book,ref,rng,cfg)
                price=book.last_price;bids=book.snapshot('buy');asks=book.snapshot('sell')
                assert not bids or not asks or bids[0][0]<asks[0][0]
                assert book.lower<=price<=book.upper
                exp=expected_price(bids,asks,round_to_tick)
                if exp is None:exp=price
                target=int(fm.calc_target_sell_price(exp,1))
                volume=sum(f.quantity for f in fills);turnover=sum(f.quantity*f.price for f in fills)
                fills_total+=len(fills);total_volume+=volume;total_turnover+=turnover
                if first_price is None:first_price=price
                high=max(high,price);low=min(low,price)
                raw={'synthetic':True,'model_version':fm.MODEL_VERSION,'cur_prc':price,
                     'reference_price':round(ref,6),'tick_volume':volume,'tick_turnover':turnover}
                for prefix,levels in [('buy',bids),('sel',asks)]:
                    for idx,(p,q) in enumerate(levels,1):
                        key=f'{prefix}_fpr' if idx==1 else f'{prefix}_{idx}th_pre'
                        raw[key+'_bid']=p;raw[key+'_req']=q
                stamp=day+' '+times[tick];phase='scan_candidate' if tick%60==0 else 'watch'
                writer.writerow([day,phase,ticker,stamp if phase=='scan_candidate' else '',0,
                    'synthetic_continuous_auction',price,previous,price,
                    bids[0][0] if bids else 0,asks[0][0] if asks else 0,exp,
                    (target/price-1)*100,(asks[0][0]-bids[0][0])/price*100 if bids and asks else 999.,
                    sum(p*q for p,q in asks[:5]),(price/previous-1)*100,
                    json.dumps(raw,separators=(',',':')),stamp])
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        entry={'path':str(path.relative_to(out)),'session_date':day,'ticker':ticker,'rows':22800,
               'previous_close':previous,'first_price':first_price,'high':high,'low':low,'last_price':book.last_price,
               'tick_volume_total':total_volume,'tick_turnover_total':total_turnover,'executions':fills_total,
               'sha256_gzip':digest,'bytes':path.stat().st_size,'generation_seconds':round(time.monotonic()-started,3)}
        entries.append(entry);previous=book.last_price
        # Checkpoint each completed session, so a stopped run can be recovered.
        checkpoint=Path(out)/f'checkpoint_{ticker}.json'
        temporary=checkpoint.with_suffix('.json.tmp');temporary.write_text(json.dumps(entries),encoding='utf-8');temporary.replace(checkpoint)
        print(json.dumps({'ticker':ticker,'day':day,'seconds':entry['generation_seconds'],'bytes':entry['bytes']}),flush=True)
    return entries


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',required=True);ap.add_argument('--out',required=True)
    ap.add_argument('--workers',type=int,default=8);ap.add_argument('--benchmark',action='store_true')
    args=ap.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    seed=42;selection=random.Random(seed);day=date(2026,11,23);end=date(2027,5,23)
    schedule={};all_names=[f'FAKE{i:02d}' for i in range(1,11)]
    while day<end:
        if day.weekday()<5:schedule[str(day)]=selection.sample(all_names,selection.randint(5,10))
        day+=timedelta(days=1)
    if args.benchmark:
        entries=worker((str(Path(args.repo).resolve()),str(out),'FAKE01',1500,['2026-11-23'],seed))
        print(json.dumps(entries));return
    config={'dataset_version':'six-months-v1','source_commit':'709aa91e43902a91cdca4a4d304c7ab23a94118f',
      'period_start':'2026-11-23','period_end_exclusive':'2027-05-23','session_count':len(schedule),
      'seed':seed,'rng':'independent per symbol; SHA256 dailybot-six-months-v1:{seed}:{ticker} first 8 bytes big-endian',
      'selection_rng':'Python random.Random(42), randint(5,10), sample(FAKE01..FAKE10,count)',
      'ticks_per_stock_day':22800,'tick_seconds':1,'timezone':'Asia/Seoul','session':'09:00:00 <= t < 15:20:00',
      'x_bps_per_day':100,'n_bps':1,'a':5,'b':20,'c':.1,'d':1,'base_quantity':100,
      'cancellation_probability':.45,'market_order_scale':4,'scan_interval_seconds':60,
      'recording_profile':'All replay fields and visible depth retained; individual execution list omitted; tick volume/turnover retained',
      'limitations':['weekdays only; public holidays ignored','bullish reference +1% each session, not balanced regimes',
       'same engine; independent RNG streams differ from single shared RNG in standard CLI',
       'not calibrated to actual market; bot orders do not participate in matching','no HTML or replay DB included'],
      'schedule':schedule}
    (out/'dataset_config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    with (out/'universe.csv').open('w',encoding='utf-8-sig',newline='') as fp:
        w=csv.writer(fp);w.writerow(['session_date','ticker','ticks'])
        for d,names in schedule.items():
            for ticker in names:w.writerow([d,ticker,22800])
    jobs=[(str(Path(args.repo).resolve()),str(out),ticker,initial,[d for d,names in schedule.items() if ticker in names],seed)
          for ticker,initial in zip(all_names,INITIAL_PRICES)]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        entries=[]
        for future in as_completed([pool.submit(worker,j) for j in jobs]):entries.extend(future.result())
    entries.sort(key=lambda e:(e['session_date'],e['ticker']))
    manifest={**config,'files':entries,'total_rows':sum(e['rows'] for e in entries),
              'total_compressed_bytes':sum(e['bytes'] for e in entries)}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'complete':True,'rows':manifest['total_rows'],'bytes':manifest['total_compressed_bytes']}),flush=True)


if __name__=='__main__':main()
