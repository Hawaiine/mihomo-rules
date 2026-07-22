import sys, os
sys.path.insert(0, 'scripts')
from commit_writer import STRATEGY_GROUP_MAP

for brand, sg in sorted(STRATEGY_GROUP_MAP.items()):
    if brand == sg:
        continue
    yaml = f'ruleset/{brand}/{brand}.yaml'
    readme = f'ruleset/{brand}/README.md'
    if not os.path.exists(yaml):
        continue
    
    # 修 YAML
    with open(yaml) as f:
        c = f.read()
    c = c.replace(f'Rule Name: {brand}', f'Rule Name: {sg}')
    with open(yaml, 'w') as f:
        f.write(c)
    
    # 修 README
    with open(readme) as f:
        c = f.read()
    c = c.replace(f'# 📦 {brand} 规则集', f'# 📦 {sg} 规则集')
    c = c.replace(f'策略组: {brand}', f'策略组: {sg}')
    with open(readme, 'w') as f:
        f.write(c)
    
    print(f'✅ {brand} → {sg}')

print('全部完成')