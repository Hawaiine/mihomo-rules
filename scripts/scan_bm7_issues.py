"""
排查 blackmatrix7 数据质量问题
"""
import os

BM7_DIR = "upstream/blackmatrix7/rule/Clash"

def is_full_domain(v):
    return "." in v

issues = {
    "bare_suffix": [],
    "bare_domain": [],
    "domain_as_keyword": [],
}

for brand in sorted(os.listdir(BM7_DIR)):
    brand_dir = os.path.join(BM7_DIR, brand)
    if not os.path.isdir(brand_dir):
        continue
    yaml_path = os.path.join(brand_dir, f"{brand}.yaml")
    if not os.path.isfile(yaml_path):
        continue
    try:
        with open(yaml_path) as f:
            content = f.read()
    except:
        continue
    in_payload = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped == "payload:":
            in_payload = True
            continue
        if not in_payload:
            continue
        if not stripped.startswith("- "):
            continue
        raw = stripped[2:].strip()
        if (raw.startswith("'") and raw.endswith("'")) or \
           (raw.startswith('"') and raw.endswith('"')):
            raw = raw[1:-1]
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) < 2:
            continue
        rule_type = parts[0].upper()
        value = parts[1]
        if rule_type == "DOMAIN-SUFFIX" and not is_full_domain(value):
            issues["bare_suffix"].append((brand, value))
        elif rule_type == "DOMAIN" and not is_full_domain(value):
            issues["bare_domain"].append((brand, value))
        elif rule_type == "DOMAIN-KEYWORD" and is_full_domain(value):
            issues["domain_as_keyword"].append((brand, value))

print("=" * 60)
print("  blackmatrix7 数据质量问题排查")
print("=" * 60)

print(f"\n  1. DOMAIN-SUFFIX 裸词（无 .）- {len(issues['bare_suffix'])} 个")
for brand, val in sorted(issues["bare_suffix"]):
    print(f"    {brand}: {val}")

print(f"\n  2. DOMAIN 裸词（无 .）- {len(issues['bare_domain'])} 个")
for brand, val in sorted(issues["bare_domain"]):
    print(f"    {brand}: {val}")

print(f"\n  3. 完整域名当 KEYWORD - {len(issues['domain_as_keyword'])} 个")
for brand, val in sorted(issues["domain_as_keyword"]):
    print(f"    {brand}: {val}")

print(f"\n  总计: {len(issues['bare_suffix']) + len(issues['bare_domain']) + len(issues['domain_as_keyword'])} 个问题")