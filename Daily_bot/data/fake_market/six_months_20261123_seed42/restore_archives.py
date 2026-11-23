"""Verify tracked fragments and reconstruct the original six-month ZIP archives."""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def file_hash(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open('rb') as fp:
        for block in iter(lambda:fp.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def restore_archive(archive: dict, parts: Path, output: Path | None = None) -> None:
    destination=output/archive['name'] if output is not None else None
    if destination is not None and destination.exists():
        if file_hash(destination)!=archive['sha256']:
            raise FileExistsError(f'Existing ZIP differs from the original: {destination}')
        print(f'Already restored: {destination.name}');return
    temporary=destination.with_name(destination.name+'.partial') if destination is not None else None
    if output is not None:output.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256();total=0
    try:
        target=temporary.open('wb') if temporary is not None else None
        try:
            for fragment in archive['parts']:
                path=parts/fragment['name']
                if path.stat().st_size!=fragment['bytes']:raise ValueError(f'Incorrect size: {path.name}')
                chunk_hash=hashlib.sha256()
                with path.open('rb') as inp:
                    for block in iter(lambda:inp.read(1024*1024),b''):
                        chunk_hash.update(block);digest.update(block);total+=len(block)
                        if target is not None:target.write(block)
                if chunk_hash.hexdigest()!=fragment['sha256']:raise ValueError(f'Checksum mismatch: {path.name}')
        finally:
            if target is not None:target.close()
        if total!=archive['bytes'] or digest.hexdigest()!=archive['sha256']:
            raise ValueError(f'Original ZIP checksum mismatch: {archive["name"]}')
        if temporary is not None:temporary.replace(destination)
    except Exception:
        if temporary is not None:temporary.unlink(missing_ok=True)
        raise
    print(('Verified: ' if output is None else 'Restored: ')+archive['name'])


def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--month',help='Optional YYYY-MM; omit for all months')
    ap.add_argument('--parts-dir',type=Path,default=ROOT/'parts')
    ap.add_argument('--out',type=Path,default=ROOT/'reconstructed')
    ap.add_argument('--verify-only',action='store_true')
    args=ap.parse_args()
    manifest=json.loads((ROOT/'parts_manifest.json').read_text(encoding='utf-8'))
    archives=[a for a in manifest['archives'] if not args.month or a['name'].startswith('dailybot_fake_'+args.month+'_')]
    if not archives:ap.error('No matching month in this dataset')
    try:
        for archive in archives:restore_archive(archive,args.parts_dir,None if args.verify_only else args.out)
    except (OSError,ValueError) as error:
        ap.exit(1,str(error)+'\n')


if __name__=='__main__':main()
