#!/usr/bin/env python3
"""Pinned Oasisic brand-catalog projection; refresh writes candidates only."""
from __future__ import annotations
import argparse, difflib, hashlib, json, re, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/'config/brand-catalog.lock.json'
PROJECTION=ROOT/'config/brand-catalog.projection.json'
FILES=('config/brands.json','config/brand-relationships.json','config/categories.json')
SHA_RE=re.compile(r'^[0-9a-f]{40}$')

def digest(data: bytes)->str: return hashlib.sha256(data).hexdigest()
def git(repo:Path,*args:str)->bytes:
    p=subprocess.run(['git','-C',str(repo),*args],capture_output=True)
    if p.returncode: raise ValueError(p.stderr.decode('utf-8','replace').strip() or f'git failed: {args}')
    return p.stdout
def load_at(repo:Path, commit:str, path:str)->bytes: return git(repo,'show',f'{commit}:{path}')
def build(repo:Path,commit:str)->tuple[dict,dict]:
    if not SHA_RE.fullmatch(commit): raise ValueError('commit must be a full lowercase 40-hex SHA; floating refs are forbidden')
    actual=git(repo,'rev-parse',f'{commit}^{{commit}}').decode().strip()
    if actual != commit: raise ValueError('commit object does not resolve to the requested full SHA')
    reachable=subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',commit,'HEAD'],capture_output=True)
    if reachable.returncode: raise ValueError('requested commit is not reachable from the pinned checkout HEAD')
    blobs={f:load_at(repo,commit,f) for f in FILES}
    tree=set(git(repo,'ls-tree','-r','--name-only',commit,'--','icons/').decode().splitlines())
    docs={f:json.loads(b) for f,b in blobs.items()}
    if not isinstance(docs[FILES[0]],dict) or not isinstance(docs[FILES[0]].get('brands'),list): raise ValueError('brands.json must contain a brands array')
    if not isinstance(docs[FILES[1]],dict) or docs[FILES[1]].get('generated') is not True or not isinstance(docs[FILES[1]].get('brands'),list): raise ValueError('brand-relationships.json must be a generated relationship projection')
    brands=docs[FILES[0]]['brands']; ids=[]
    for row in brands:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id'].strip(): raise ValueError('brand entry has missing/invalid id')
        ids.append(row['id'])
    if len(ids)!=len(set(ids)): raise ValueError('duplicate canonical brand id')
    known=set(ids); missing=sorted({r.get('parent_brand') for r in brands if r.get('parent_brand') and r['parent_brand'] not in known})
    for row in brands:
        for field in ('display_name','category','entity_type'):
            if field in row and (not isinstance(row[field],str) or not row[field].strip()): raise ValueError(f'invalid {field} for {row["id"]}')
        parent=row.get('parent_brand')
        if parent is not None and (not isinstance(parent,str) or not parent.strip()): raise ValueError(f'invalid parent_brand for {row["id"]}')
    if not isinstance(docs[FILES[1]],dict) or docs[FILES[1]].get('generated') is not True or not isinstance(docs[FILES[1]].get('brands'),list): raise ValueError('brand-relationships.json must be a generated relationship projection')
    relation_rows=docs[FILES[1]]['brands']; relation_ids=[r.get('child') for r in relation_rows if isinstance(r,dict)]
    if len(relation_ids)!=len(brands) or len(set(relation_ids))!=len(relation_ids) or set(relation_ids)!=set(ids): raise ValueError('relationship projection IDs do not exactly match brands SSOT')
    relation_map={r['child']:r for r in relation_rows}
    for row in brands:
        if relation_map[row['id']].get('parent') != row.get('parent_brand'): raise ValueError(f'relationship projection parent differs for {row["id"]}')
    if not isinstance(docs[FILES[2]],dict) or not isinstance(docs[FILES[2]].get('categories'),list): raise ValueError('categories.json must contain a categories array')
    category_rows=docs[FILES[2]]['categories']; category_ids=[c.get('id') for c in category_rows if isinstance(c,dict)]
    if len(category_ids)!=len(category_rows) or any(not isinstance(x,str) or not x for x in category_ids) or len(set(category_ids))!=len(category_ids): raise ValueError('categories must have unique non-empty string IDs')
    # Reuse resolver + exporter from the exact pinned commit in an isolated temp source tree.
    try:
        import tempfile
        with tempfile.TemporaryDirectory(prefix='oasisic-pinned-catalog-') as tmp:
            pinned_root=Path(tmp)
            (pinned_root/'scripts').mkdir()
            for rel in ('scripts/brand_relationships.py','scripts/json_io.py','scripts/export-brand-relationships.py'):
                target=pinned_root/rel
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(load_at(repo,commit,rel))
            resolver_ns={'__name__':'oasisic_pinned_brand_relationships','__file__':str(pinned_root/'scripts/brand_relationships.py')}
            exec(compile((pinned_root/'scripts/brand_relationships.py').read_text(encoding='utf-8'),resolver_ns['__file__'],'exec'),resolver_ns)
            errors=resolver_ns['validate_relationships'](docs[FILES[0]],docs[FILES[2]]['categories'])
            if errors: raise ValueError('pinned upstream relationship validator: '+'; '.join(errors[:20]))
            exporter_ns={'__name__':'oasisic_pinned_relationship_exporter','__file__':str(pinned_root/'scripts/export-brand-relationships.py')}
            exec(compile((pinned_root/'scripts/export-brand-relationships.py').read_text(encoding='utf-8'),exporter_ns['__file__'],'exec'),exporter_ns)
            replay=exporter_ns['build'](docs[FILES[0]],docs[FILES[2]]['categories'])
            if replay != docs[FILES[1]]: raise ValueError('pinned brand-relationships.json differs from replayed pinned exporter output')
    except (OSError,ImportError,AttributeError,UnicodeError) as exc: raise ValueError(f'cannot execute pinned Oasisic relationship resolver/exporter: {exc}') from exc
    for row in brands:
        icon=row.get('icon_path')
        if icon is not None and (not isinstance(icon,str) or not icon.startswith('icons/')): raise ValueError(f'invalid icon_path for {row["id"]}')
        if icon and icon not in tree: raise ValueError(f'icon resource missing from pinned tree: {row["id"]} -> {icon}')
    rows=[]
    for row in sorted(brands,key=lambda x:x['id']):
        rows.append({k:row[k] for k in ('id','display_name','entity_type','category','parent_brand','icon_path','icon_status','canonical') if k in row})
    # Validate existing human-approved icon mappings and strategy-group keys against real catalog/ruleset IDs.
    try:
        sys.path.insert(0,str(ROOT/'scripts'))
        from lib.icon_mapping import TECHNICAL_TO_CANONICAL
        from commit_writer import STRATEGY_GROUP_MAP
        ruleset_ids={p.name for p in (ROOT/'ruleset').iterdir() if p.is_dir()}
        unknown_targets=sorted({v for v in TECHNICAL_TO_CANONICAL.values() if v not in known})
        unknown_mapping_ids=sorted(set(TECHNICAL_TO_CANONICAL)-ruleset_ids)
        unknown_strategy_ids=sorted(set(STRATEGY_GROUP_MAP)-ruleset_ids)
        if unknown_targets or unknown_mapping_ids or unknown_strategy_ids:
            raise ValueError(f'ruleset mapping mismatch: targets={unknown_targets}, icon IDs={unknown_mapping_ids}, strategy IDs={unknown_strategy_ids}')
    except (ImportError,OSError) as exc: raise ValueError(f'cannot validate mihomo mapping inputs: {exc}') from exc
    mapping_inputs={}
    for name in ('scripts/lib/icon_mapping.py','scripts/commit_writer.py','scripts/lib/ownership_map.py'):
        mapping_inputs[name]=digest((ROOT/name).read_bytes())
    strategy_map_digest=digest(json.dumps({'icon_mapping':TECHNICAL_TO_CANONICAL,'strategy_groups':STRATEGY_GROUP_MAP,'sub_parent':__import__('lib.ownership_map',fromlist=['SUB_PARENT']).SUB_PARENT,'ruleset_ids':sorted(ruleset_ids)},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
    projection={'schema_version':1,'source_repository':'Hawaiine/Oasisic-Icons','source_commit':commit,'external_parent_ids':missing,'mapping_inputs':mapping_inputs,'ruleset_mapping_sha256':strategy_map_digest,'brands':rows,'categories':sorted(docs[FILES[2]]['categories'],key=lambda x:x.get('id',''))}
    existing=json.loads(LOCK.read_text()) if LOCK.exists() else {}
    previous=existing.get('previous_commit') if existing.get('commit')==commit else existing.get('commit')
    lock={'schema_version':1,'repository':'Hawaiine/Oasisic-Icons','commit':commit,'files':{f:{'sha256':digest(blobs[f])} for f in FILES},'previous_commit':previous,'update_policy':'explicit reviewed PR only; refresh never writes production lock/projection'}
    return lock,projection
def canonical(obj:dict)->bytes: return (json.dumps(obj,ensure_ascii=False,indent=2)+'\n').encode()
def main()->int:
    ap=argparse.ArgumentParser(); mode=ap.add_mutually_exclusive_group(required=True); mode.add_argument('--check',action='store_true'); mode.add_argument('--refresh',action='store_true')
    ap.add_argument('--repo',required=True); ap.add_argument('--commit'); ap.add_argument('--candidate-dir',default='artifacts/brand-catalog-candidate')
    a=ap.parse_args(); repo=Path(a.repo).resolve()
    try:
        lock=json.loads(LOCK.read_text()) if LOCK.exists() else None
        if lock is None:
            manifest=json.loads((ROOT/'scripts/config_contract/oasisic_revision.json').read_text())
            lock={'commit':manifest.get('revision')}
        commit=a.commit or (lock or {}).get('commit')
        if not isinstance(commit,str) or not SHA_RE.fullmatch(commit): raise ValueError('a full 40-hex commit SHA is required')
        new_lock,new_projection=build(repo,commit)
        if a.check:
            manifest=json.loads((ROOT/'scripts/config_contract/oasisic_revision.json').read_text())
            if manifest.get('repository')!='Hawaiine/Oasisic-Icons' or manifest.get('revision')!=commit: raise ValueError('catalog lock commit differs from the independently approved Oasisic revision manifest')
            if lock is None or canonical(lock)!=canonical(new_lock): raise ValueError('catalog lock pin/digest mismatch; use reviewed candidate refresh')
            if not PROJECTION.exists() or PROJECTION.read_bytes()!=canonical(new_projection): raise ValueError('catalog projection drift')
            print(f'PASS: pinned catalog {commit}, {len(new_projection["brands"])} brands')
            return 0
        if not a.commit: raise ValueError('--refresh requires explicit --commit')
        dest=(ROOT/a.candidate_dir).resolve(); dest.mkdir(parents=True,exist_ok=True)
        (dest/'brand-catalog.lock.candidate.json').write_bytes(canonical(new_lock))
        (dest/'brand-catalog.projection.candidate.json').write_bytes(canonical(new_projection))
        old_text=PROJECTION.read_text(encoding='utf-8') if PROJECTION.exists() else ''
        new_text=canonical(new_projection).decode('utf-8')
        diff=''.join(difflib.unified_diff(old_text.splitlines(keepends=True),new_text.splitlines(keepends=True),fromfile='current projection',tofile=f'candidate {commit}'))
        (dest/'brand-catalog-diff.md').write_text(f'# Catalog candidate diff\n\nSource: `{commit}`\nBrand count: {len(new_projection["brands"])}\nExternal parent IDs: {len(new_projection["external_parent_ids"])}\n\nCandidate only; checked-in lock/projection unchanged.\n\n```diff\n{diff}```\n',encoding='utf-8')
        print(f'Candidate written under {dest}; production lock/projection unchanged')
        return 0
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        print(f'FAIL: {exc}',file=sys.stderr); return 1
if __name__=='__main__': raise SystemExit(main())
