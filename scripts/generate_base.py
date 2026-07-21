"""
批量生成基础规则集（7 个）
从 Loyalsoldier 解析 → 写入 ruleset/
"""
import sys
from pathlib import Path
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from parse_loyalsoldier import parse_loyalsoldier_basic, LOYALSOLDIER_BASE_MAP
from commit_writer import write_ruleset, generate_yaml, generate_readme, determine_behavior

LOYAL_DIR = "upstream/loyalsoldier"

print("📥 生成基础规则集...")
print()

results = parse_loyalsoldier_basic(LOYAL_DIR)

print()
print("=" * 50)
print("📝 写入 ruleset/...")
print()

for ruleset_name, data in results.items():
    rules = data["rules"]
    behavior = data["behavior"]
    
    if not rules:
        print(f"  ⚠️ {ruleset_name}: 无规则，跳过")
        continue
    
    result = write_ruleset(ruleset_name, rules, strategy_group=ruleset_name)
    
    if result.success:
        type_counts = data["type_counts"]
        type_str = " + ".join([f"{t}:{c}" for t, c in sorted(type_counts.items())])
        print(f"  ✅ {ruleset_name}: {len(rules)} 条 [{type_str}]")
        if result.diff:
            changed = sum(1 for line in result.diff.split("\n") if line.startswith("+") and not line.startswith("+++"))
            removed = sum(1 for line in result.diff.split("\n") if line.startswith("-") and not line.startswith("---"))
            print(f"     diff: +{changed}/-{removed}")
    else:
        print(f"  ❌ {ruleset_name}: {result.error}")

print()
print("✅ 完成!")