"""Validate and unpack selected synthetic sessions, then build a replay DB."""
from __future__ import annotations
import argparse,gzip,hashlib,json,shutil,sys
from datetime import date
from pathlib import Path


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',required=True,help='StockAutoTradingBot repository path')
    ap.add_argument('--data-root',required=True,help='root where downloaded packages were extracted')
    ap.add_argument('--start',required=True,help='first session YYYY-MM-DD inclusive')
    ap.add_argument('--end',required=True,help='last session YYYY-MM-DD inclusive')
    ap.add_argument('--out',required=True,help='new empty working directory')
    args=ap.parse_args();start=date.fromisoformat(args.start);end=date.fromisoformat(args.end)
    if start>end:ap.error('start must be <= end')
    root=Path(args.data_root).resolve();out=Path(args.out).resolve()
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    selected=[e for e in manifest['files'] if args.start<=e['session_date']<=args.end]
    if not selected:ap.error('no synthetic weekdays in the requested date range')
    missing=[e['path'] for e in selected if not (root/'data'/e['path']).is_file()]
    if missing:raise FileNotFoundError(f'Missing {len(missing)} trace files. Extract the corresponding ZIP parts into the same root. First: {missing[0]}')
    if out.exists() and any(out.iterdir()):raise FileExistsError(f'Output must be empty: {out}')
    sys.path.insert(0,str(Path(args.repo).resolve()))
    from Daily_bot.backtest.replay_db_builder import build_replay_db_from_logs
    out.mkdir(parents=True,exist_ok=True)
    traces=out/'traces';traces.mkdir()
    for entry in selected:
        source=root/'data'/entry['path'];digest=hashlib.sha256()
        with source.open('rb') as fp:
            for chunk in iter(lambda:fp.read(1024*1024),b''):digest.update(chunk)
        if digest.hexdigest()!=entry['sha256_gzip']:raise ValueError(f'Checksum mismatch: {source.name}')
        dest=traces/source.name.removesuffix('.gz')
        with gzip.open(source,'rb') as inp,dest.open('wb') as output:shutil.copyfileobj(inp,output,1024*1024)
        print(f'Unpacked {source.name}',flush=True)
    db=build_replay_db_from_logs(traces,out/'replay.sqlite3',market_pattern='fake_market_traces_*.csv')
    print(f'Replay DB: {db}')
    print('Use the standard runner with --market-source real --db <the above path>. This replays saved synthetic input without generating a new market.')


if __name__=='__main__':main()
