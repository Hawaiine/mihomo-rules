#!/usr/bin/env python3
"""Pinned Oasisic brand-catalog projection; refresh writes candidates only."""
from __future__ import annotations
import argparse, ast, difflib, hashlib, json, re, subprocess, sys, tempfile, types
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
    docs={}
    for path,raw in blobs.items():
        try: docs[path]=json.loads(raw)
        except json.JSONDecodeError as exc: raise ValueError(f'{path}: malformed JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}') from exc
    if not isinstance(docs[FILES[0]],dict) or not isinstance(docs[FILES[0]].get('brands'),list): raise ValueError('brands.json must contain a brands array')
    if not isinstance(docs[FILES[1]],dict) or docs[FILES[1]].get('generated') is not True or not isinstance(docs[FILES[1]].get('brands'),list): raise ValueError('brand-relationships.json must be a generated relationship projection')
    brands=docs[FILES[0]]['brands']; ids=[]
    brands_doc=docs[FILES[0]]; categories=docs[FILES[2]].get('categories') if isinstance(docs[FILES[2]],dict) else None
    if not isinstance(categories,list): raise ValueError('categories.json must contain a categories array')
    validate_pinned_rows(repo,commit,brands_doc,docs[FILES[2]],tree)
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

def pinned_source(repo:Path, commit:str, rel:str)->str:
    return load_at(repo,commit,rel).decode('utf-8')

def pinned_literal(repo:Path, commit:str, rel:str, name:str):
    """Read a literal schema constant from pinned source AST (no executing validator imports)."""
    tree=ast.parse(pinned_source(repo,commit,rel),filename=rel)
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets):
            return ast.literal_eval(node.value)
        if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and node.target.id==name:
            return ast.literal_eval(node.value)
    raise ValueError(f'pinned schema constant {name} not found in {rel}')

def validate_pinned_rows(repo:Path, commit:str, brands_doc:dict, cats_doc:dict, tree:set[str])->None:
    """Apply exact pinned Oasisic schema and physical-path validator rules."""
    brands=brands_doc['brands']; categories=cats_doc['categories']
    entity_types=pinned_literal(repo,commit,'scripts/ci-validate-icons.py','ENTITY_TYPES')
    id_re_src=None
    ast_validate=ast.parse(pinned_source(repo,commit,'scripts/validate-brand.py'))
    for n in ast_validate.body:
        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ID_RE' for t in n.targets):
            call=n.value
            if isinstance(call,ast.Call) and call.args and isinstance(call.args[0],ast.Constant) and isinstance(call.args[0].value,str):
                id_re_src=call.args[0].value
    if not id_re_src: raise ValueError('cannot read pinned ID_RE pattern')
    id_re=re.compile(id_re_src)
    cat_rows={c['id']:c for c in categories}
    resolver_source=pinned_source(repo,commit,'scripts/brand_relationships.py')
    ns={'__name__':'pinned_oasisic_brand_relationships','__file__':'scripts/brand_relationships.py'}
    exec(compile(resolver_source,ns['__file__'],'exec'),ns)
    expected_icon_path=ns['expected_icon_path']
    expected_paths={row['id']:expected_icon_path(row['id'],{b['id']:b for b in brands}) for row in brands}
    ids=[]
    required=('id','display_name','category','entity_type')
    for i,row in enumerate(brands):
        if not isinstance(row,dict): raise ValueError(f'brands[{i}] must be an object')
        for field in required:
            if field not in row: raise ValueError(f'brands[{i}] missing required field {field}')
            value=row[field]
            if not isinstance(value,str) or not value.strip(): raise ValueError(f'brands[{i}].{field} must be a non-empty string')
        bid=row['id']
        if not id_re.fullmatch(bid) or bid in ('.','..') or '/' in bid or '\\\\' in bid:
            raise ValueError(f'invalid brand id {bid!r} (pinned Oasisic ID_RE)')
        ids.append(bid)
        if row['entity_type'] not in entity_types:
            raise ValueError(f'{bid}: invalid entity_type {row["entity_type"]!r}; allowed {sorted(entity_types)}')
        if row['category'] not in cat_rows:
            raise ValueError(f'{bid}: category {row["category"]!r} is not defined in categories.json')
        parent=row.get('parent_brand')
        if parent is not None and (not isinstance(parent,str) or not parent.strip()):
            raise ValueError(f'{bid}: parent_brand must be null or a non-empty string')
        status=row.get('icon_status')
        if status is not None and (not isinstance(status,str) or not status.strip()):
            raise ValueError(f'{bid}: icon_status must be a non-empty string when present')
        icon=row.get('icon_path')
        if icon is not None and (not isinstance(icon,str) or not icon.strip()):
            raise ValueError(f'{bid}: icon_path must be null or a non-empty string')
        pending=(row['entity_type']=='ecosystem' and status=='pending' and not icon)
        if row['entity_type']=='ecosystem' and status=='generated_temporary' and not icon:
            raise ValueError(f'{bid}: generated_temporary ecosystem must have icon_path')
        if not icon and not pending:
            raise ValueError(f'{bid}: missing icon_path (only pending ecosystem may omit it)')
        if pending:
            if row.get('canonical') is not True: raise ValueError(f'{bid}: pending ecosystem must set canonical=true')
            if row['category']!=bid: raise ValueError(f'{bid}: pending ecosystem category must equal its ID')
            continue
        if parent is not None and not isinstance(parent,str):
            raise ValueError(f'{bid}: parent_brand must be null or a string')
        expected=expected_paths[bid]
        if icon != expected:
            raise ValueError(f'{bid}: icon_path mismatch: got {icon!r}, expected {expected!r}')
        if icon not in tree: raise ValueError(f'{bid}: icon resource absent from pinned git tree: {icon}')
    if len(ids)!=len(set(ids)): raise ValueError('duplicate canonical brand ID')
    # Use exact pinned resolver and physical-path validator; supply pinned icon files for path existence.
    source=pinned_source(repo,commit,'scripts/brand_relationships.py')
    ns={'__name__':'pinned_oasisic_brand_relationships','__file__':'scripts/brand_relationships.py'}
    exec(compile(source,ns['__file__'],'exec'),ns)
    rel_errors=ns['validate_relationships'](brands_doc,categories)
    if rel_errors: raise ValueError('pinned relationship validator: '+'; '.join(rel_errors[:12]))
    with tempfile.TemporaryDirectory(prefix='oasisic-icon-paths-') as td:
        physical_root=Path(td)
        for icon in tree:
            if icon.endswith('.png'):
                path=physical_root/icon
                path.parent.mkdir(parents=True,exist_ok=True)
                path.touch()
        errors=ns['validate_physical_paths'](brands_doc,categories,str(physical_root))
        if errors: raise ValueError('pinned physical-path validator: '+'; '.join(errors[:12]))
    # Re-run the exact pinned exporter against the validated SSOT and require its committed
    # derived relationship file to match byte-for-byte; use the already loaded pinned engine.
    pinned_engine=types.ModuleType('brand_relationships')
    pinned_engine.__dict__.update(ns)
    json_io_module=types.ModuleType('json_io')
    json_io_module.read_json_or_exit=lambda *args,**kwargs: None
    previous_engine=sys.modules.get('brand_relationships'); previous_json_io=sys.modules.get('json_io')
    try:
        sys.modules['brand_relationships']=pinned_engine
        sys.modules['json_io']=json_io_module
        exporter_source=pinned_source(repo,commit,'scripts/export-brand-relationships.py')
        exporter_ns={'__name__':'pinned_oasisic_relationship_exporter','__file__':'scripts/export-brand-relationships.py'}
        exec(compile(exporter_source,exporter_ns['__file__'],'exec'),exporter_ns)
        replay=exporter_ns['build'](brands_doc,categories)
        if replay!=json.loads(load_at(repo,commit,'config/brand-relationships.json')):
            raise ValueError('brand-relationships.json differs from exact pinned exporter output')
    finally:
        if previous_engine is None: sys.modules.pop('brand_relationships',None)
        else: sys.modules['brand_relationships']=previous_engine
        if previous_json_io is None: sys.modules.pop('json_io',None)
        else: sys.modules['json_io']=previous_json_io

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
    except Exception as exc:
        print(f'FAIL: unexpected catalog validation error ({type(exc).__name__}): {exc}',file=sys.stderr); return 1
if __name__=='__main__': raise SystemExit(main())
