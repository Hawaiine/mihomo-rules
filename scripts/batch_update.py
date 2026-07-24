#!/usr/bin/env python3
"""
batch_update.py — 上游拉取 → 自动生成 → 校验 → 提交 → Discord 通知

每日定时同步（cron 6:00）：
  1. git pull 同步远程
  2. 备份当前状态
  3. 执行 8 步（fetch→parse→merge→write→ownership→config）
  4. 校验（品牌数/空壳/YAML/随机50*3轮）
  5. 通过 → 提交 + 推送；失败 → 回滚 + 通知
"""
import json
import os
import random
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = Path('/tmp/mihomo-batch-update.lock')
LOG_FILE = Path('/var/log/mihomo-sync.log')
DISCORD_WEBHOOK = os.environ.get('MIHOMO_DISCORD_WEBHOOK', '')
GITHUB_RUN_URL = os.environ.get('GITHUB_RUN_URL', '')
GITHUB_REPOSITORY = os.environ.get('GITHUB_REPOSITORY', 'Hawaiine/mihomo-rules')
GITHUB_SHA = os.environ.get('GITHUB_SHA', '')

# 8 步流程
STEPS = [
    ("fetch_upstream", "拉取上游数据", 300),
    ("batch_write", "批量写入规则集", 600),
    ("resolve_ownership", "所有权清理（--apply）", 60),
    ("generate_config", "生成 config", 60),
]

BACKUP_DIR = None
START_TIME = time.time()
LOG = []


# ── 工具函数 ──────────────────────────────────────────


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}')
    LOG.append(msg)


def send_discord(title, color, fields, add_run_link=True):
    """发送 Discord 卡片通知"""
    if not DISCORD_WEBHOOK:
        return
    payload = {
        "embeds": [{
            "title": title,
            "color": color,
            "fields": fields,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {
                "text": "mihomo-rules · 每日自动同步",
                "icon_url": "https://github.com/Hawaiine.png"
            },
            "thumbnail": {
                "url": "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/Mihomo/Mihomo.png"
            }
        }]
    }
    # 添加 Actions 运行链接
    if add_run_link and GITHUB_RUN_URL:
        payload["embeds"][0]["fields"].append({
            "name": "🔗 Actions",
            "value": f"[查看运行日志]({GITHUB_RUN_URL})",
            "inline": False
        })
    try:
        import urllib.request
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(DISCORD_WEBHOOK, data=data,
                                     headers={'Content-Type': 'application/json',
                                              'User-Agent': 'mihomo-bot/1.0'})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log(f'⚠️ Discord 通知失败: {e}')


def send_success(stats, elapsed, updated_brands=None):
    """✅ 成功通知"""
    commit_short = GITHUB_SHA[:7] if GITHUB_SHA else '?'
    fields = [
        {"name": "📊 品牌统计", "value": f"99 品牌 | {stats.get('rules_total', '?')} 条规则", "inline": True},
        {"name": "🔄 变更数", "value": str(stats.get('brands_updated', 0)), "inline": True},
        {"name": "➕ 新增规则", "value": f"+{stats.get('rules_added', 0)} 条", "inline": True},
        {"name": "➖ 移除规则", "value": f"-{stats.get('rules_removed', 0)} 条", "inline": True},
        {"name": "⚙️ Config 更新", "value": f"{stats.get('configs_updated', 0)} 个", "inline": True},
        {"name": "⏱️ 耗时", "value": f"{elapsed:.0f} 秒", "inline": True},
        {"name": "🆔 提交", "value": f"`{commit_short}`", "inline": True},
    ]
    # 变更品牌 top10
    if updated_brands:
        top = updated_brands[:10]
        if len(updated_brands) > 10:
            top.append(f"等 {len(updated_brands) - 10} 个")
        fields.insert(1, {
            "name": "📝 变更品牌",
            "value": " · ".join(top),
            "inline": False,
        })
    send_discord(
        "✅ 规则集同步成功",
        5763719,
        fields,
    )


def send_failure(step, error_msg):
    """❌ 失败通知"""
    send_discord(
        "❌ 同步失败，已自动回滚",
        15548997,
        [
            {"name": "💥 失败步骤", "value": f"`{step}`", "inline": True},
            {"name": "📝 错误信息", "value": f"```{error_msg[:300]}```"},
            {"name": "🔄 回滚状态", "value": "✅ 已恢复至同步前版本，无需手动操作", "inline": True},
            {"name": "⏱️ 耗时", "value": f"{time.time() - START_TIME:.0f} 秒", "inline": True},
        ],
        add_run_link=True
    )


def send_nochange(elapsed):
    """⏸️ 无变化通知"""
    send_discord(
        "⏸️ 上游无变化，跳过提交",
        15844367,
        [
            {"name": "📊 品牌", "value": "99 个品牌均无变化", "inline": True},
            {"name": "⏱️ 耗时", "value": f"{elapsed:.0f} 秒", "inline": True},
        ]
    )


# ── 锁 ──────────────────────────────────────────────────


def acquire_lock():
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < 1800:
            log('⚠️ 上次同步未完成（锁存在 < 30 分钟），跳过')
            sys.exit(0)
        else:
            log('⚠️ 过期锁，清理')
            LOCK_FILE.unlink()
    LOCK_FILE.write_text(str(os.getpid()))


def release_lock():
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()


# ── 备份 ──────────────────────────────────────────────────


def backup():
    global BACKUP_DIR
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    BACKUP_DIR = Path(f'/tmp/mihomo-backup-{ts}')
    BACKUP_DIR.mkdir(parents=True)

    # 备份关键目录
    for d in ['ruleset', 'configs', 'scripts', 'data']:
        src = ROOT / d
        if src.exists():
            shutil.copytree(src, BACKUP_DIR / d, symlinks=True)

    # 记录 commit hash
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = 'unknown'

    manifest = {
        'time': datetime.now().isoformat(),
        'commit': commit,
        'brands': len([d for d in (ROOT / 'ruleset').iterdir() if d.is_dir()]),
        'rules_total': 0,
    }
    (BACKUP_DIR / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    log(f'📦 备份到 {BACKUP_DIR}')


def rollback(step, error_msg):
    log(f'❌ 回滚: {step} - {error_msg}')
    if BACKUP_DIR and BACKUP_DIR.exists():
        # 恢复目录
        for d in ['ruleset', 'configs', 'scripts', 'data']:
            src = BACKUP_DIR / d
            dst = ROOT / d
            if src.exists():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst, symlinks=True)
        # git reset 到备份时的 commit
        manifest = json.loads((BACKUP_DIR / 'manifest.json').read_text())
        if manifest['commit'] != 'unknown':
            subprocess.run(['git', 'reset', '--hard', manifest['commit']], cwd=ROOT,
                           capture_output=True)
        log('✅ 已回滚')
    send_failure(step, error_msg)
    sys.exit(1)


# ── 校验 ──────────────────────────────────────────────────


def validate():
    """校验品牌规则集一致性"""
    from commit_writer import STRATEGY_GROUP_MAP

    BASE_BRANDS = {'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications', 'LanCIDR'}
    brands = sorted([d.name for d in (ROOT / 'ruleset').iterdir()
                     if d.is_dir() and d.name not in BASE_BRANDS])

    # 1. 品牌数量
    if len(brands) != 99:
        rollback('校验', f'品牌数异常: {len(brands)}（应为 99）')

    # 2. 空壳检查
    empty = []
    for b in brands:
        yaml = ROOT / 'ruleset' / b / f'{b}.yaml'
        if not yaml.exists():
            empty.append(f'{b}: 文件缺失')
            continue
        rules = [l for l in yaml.read_text().split('\n')
                 if l.strip() and not l.startswith('#') and not l.startswith('payload')]
        if len(rules) == 0:
            empty.append(f'{b}: 空壳')
    if empty:
        rollback('校验', f'空壳检查失败: {empty[:3]}')

    # 3. YAML 语法检查
    import yaml as yamllib
    for b in brands:
        yaml = ROOT / 'ruleset' / b / f'{b}.yaml'
        try:
            yamllib.safe_load(yaml.read_text())
        except Exception as e:
            rollback('校验', f'{b}: YAML 语法错误: {e}')

    # 4. 随机 50 品牌验证（3 轮）
    for round_num in range(3):
        sample = random.sample(brands, 50)
        errors = {}
        for b in sample:
            sg = STRATEGY_GROUP_MAP.get(b, b)
            berrors = []
            yaml = ROOT / 'ruleset' / b / f'{b}.yaml'
            readme = ROOT / 'ruleset' / b / 'README.md'
            # Rule Name
            for line in yaml.read_text().split('\n'):
                if line.startswith('# Rule Name:'):
                    rn = line.split(':', 1)[1].strip()
                    if rn != sg:
                        berrors.append(f'Rule Name: 应为 {sg}，实为 {rn}')
                    break
            # README 标题
            for line in readme.read_text().split('\n'):
                if line.startswith('# '):
                    expected = f'📦 {sg} 规则集'
                    actual = line.strip('# \n')
                    if actual != expected:
                        berrors.append(f'README 标题: 应为 {expected}，实为 {actual}')
                    break
            # README 策略组
            for line in readme.read_text().split('\n'):
                if '- **策略组**:' in line:
                    actual = line.split('**:')[1].strip()
                    if actual != sg:
                        berrors.append(f'README 策略组: 应为 {sg}，实为 {actual}')
            if berrors:
                errors[b] = berrors
        if errors:
            detail = '; '.join(f'{b}: {e[0]}' for b, e in list(errors.items())[:5])
            rollback('校验', f'第 {round_num+1} 轮验证: {len(errors)} 个失败: {detail}')
        log(f'✅ 第 {round_num+1} 轮验证: 50/50 通过')

    log('✅ 全部校验通过')


# ── 主流程 ──────────────────────────────────────────────


def main():
    global LOG
    log('=== 🔄 batch_update.py 开始 ===')
    
    # 检查 --no-commit 模式（用于 GitHub Actions，由 workflow 处理提交）
    no_commit = '--no-commit' in sys.argv

    # 1. 获取锁
    acquire_lock()

    try:
        # 2. git pull（仅非 CI 模式）
        if not no_commit:
            log('=== 📥 拉取远程代码 ===')
            subprocess.run(['git', 'stash'], cwd=ROOT, capture_output=True)
            result = subprocess.run(['git', 'pull', 'origin', 'main'], cwd=ROOT,
                                    capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                log(f'❌ git pull 失败: {result.stderr[:200]}')
                send_discord("⚠️ git pull 失败，终止同步", 15844367, [
                    {"name": "📝 错误", "value": result.stderr[:200]},
                    {"name": "📅 时间", "value": datetime.now().isoformat()},
                ])
                sys.exit(1)
            log('✅ git pull 完成')
        else:
            log('🧪 CI 模式，跳过 git pull')

        # 3. 备份
        backup()

        # 4. 执行 8 步
        sys.path.insert(0, str(ROOT / 'scripts'))
        from commit_writer import STRATEGY_GROUP_MAP
        BASE_BRANDS = {'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications', 'LanCIDR'}

        for name, desc, timeout in STEPS:
            log(f'=== 🔧 {desc} ===')
            try:
                if name == 'batch_write':
                    # 批量写入所有品牌（导入模块直接调用，避免逐个启动子进程）
                    sys.path.insert(0, str(ROOT / 'scripts'))
                    from commit_writer import write_ruleset
                    from merge_and_dedup import merge_with_stats
                    from lib.canonical import parse_rule_line
                    from parse_v2fly import parse_v2fly_brand
                    from parse_loyalsoldier import parse_loyalsoldier_brand
                    from parse_blackmatrix7 import parse_blackmatrix7_brand
                    
                    v2fly_dir = str(ROOT / 'upstream' / 'v2fly' / 'data')
                    ls_dir = str(ROOT / 'upstream' / 'loyalsoldier')
                    bm7_dir = str(ROOT / 'upstream' / 'blackmatrix7' / 'rule' / 'Clash')
                    
                    brands = sorted([d.name for d in (ROOT / 'ruleset').iterdir()
                                     if d.is_dir() and d.name not in BASE_BRANDS])
                    ok_count = 0
                    fail_count = 0
                    for b in brands:
                        try:
                            v2fly_result = parse_v2fly_brand(b, v2fly_dir)
                            v2fly_rules = v2fly_result.get("main", [])
                            ls_rules = parse_loyalsoldier_brand(b, ls_dir)
                            bm7_rules = parse_blackmatrix7_brand(b, bm7_dir)
                            total = len(v2fly_rules) + len(ls_rules) + len(bm7_rules)
                            if total == 0:
                                # 无上游数据，跳过（保留现有规则集）
                                ok_count += 1
                                continue
                            merged = merge_with_stats(v2fly_rules, ls_rules, bm7_rules)
                            merged_rules = merged["rules"]
                            
                            # Union 合并：读取现有规则，保留手动添加的规则
                            existing_yaml = ROOT / 'ruleset' / b / f'{b}.yaml'
                            manual_rules = []
                            existing_seen = set()
                            if existing_yaml.exists():
                                with open(existing_yaml) as f:
                                    for line in f:
                                        line = line.strip()
                                        if not line or line.startswith('#') or line.startswith('payload'):
                                            continue
                                        if line in existing_seen:
                                            continue
                                        existing_seen.add(line)
                                        # 用 parse_rule_line 解析（自动处理 YAML 前缀）
                                        cr = parse_rule_line(line, 'manual')
                                        if cr is None:
                                            continue
                                        # 精确匹配 TYPE+VALUE
                                        if not any(r.rule_type == cr.rule_type and r.value == cr.value
                                                  for r in merged_rules):
                                            manual_rules.append(cr)
                            from lib.canonical import sort_rules
                            all_rules = sort_rules(merged_rules + manual_rules)
                            # 去重（sort_rules 只排序，不去重）
                            seen_keys = set()
                            deduped = []
                            for r in all_rules:
                                key = f"{r.rule_type}|{r.value.lower()}"
                                if key not in seen_keys:
                                    seen_keys.add(key)
                                    deduped.append(r)
                            all_rules = deduped
                            result = write_ruleset(b, all_rules, dry_run=False)
                            if result.success:
                                ok_count += 1
                            else:
                                fail_count += 1
                        except Exception as e:
                            log(f'  ❌ {b}: {e}')
                            fail_count += 1
                    log(f'✅ 写入 {ok_count} 个品牌, ❌ 失败 {fail_count} 个')
                    if fail_count > 0:
                        raise RuntimeError(f'{fail_count} 个品牌写入失败')
                elif name == 'resolve_ownership':
                    # 需要 --apply 参数
                    cmd = [sys.executable, f'scripts/{name}.py', '--apply']
                    result = subprocess.run(cmd, cwd=ROOT, timeout=timeout,
                                            capture_output=True, text=True)
                    if result.returncode != 0:
                        err_detail = result.stderr[:500] or result.stdout[:500] or '无输出'
                        rollback(name, f'退出码 {result.returncode}: {err_detail}')
                    log(result.stdout[:200] if result.stdout else 'OK')
                else:
                    # 其他步骤（fetch_upstream, generate_config）
                    cmd = [sys.executable, f'scripts/{name}.py']
                    result = subprocess.run(cmd, cwd=ROOT, timeout=timeout,
                                            capture_output=True, text=True)
                    if result.returncode != 0:
                        err_detail = result.stderr[:500] or result.stdout[:500] or '无输出'
                        rollback(name, f'退出码 {result.returncode}: {err_detail}')
                    log(result.stdout[:200] if result.stdout else 'OK')
            except subprocess.TimeoutExpired:
                rollback(name, f'超时（{timeout}s）')
            except Exception as e:
                rollback(name, f'{type(e).__name__}: {str(e)[:200]}')

        # 5. 校验
        try:
            validate()
        except Exception as e:
            rollback('校验', f'{type(e).__name__}: {str(e)[:200]}')

        # 6. 检查是否有变化
        result = subprocess.run(['git', 'diff', '--stat'], cwd=ROOT,
                                capture_output=True, text=True)
        diff = result.stdout.strip()
        elapsed = time.time() - START_TIME

        if not diff:
            log('无变化，跳过提交')
            send_nochange(elapsed)
            return

        # 7. 统计变化
        stats = {'brands_updated': 0, 'rules_added': 0, 'rules_removed': 0,
                 'configs_updated': 0, 'rules_total': 0}
        for line in diff.split('\n'):
            if ' ruleset/' in line and ' | ' in line:
                stats['brands_updated'] += 1
            if ' configs/' in line and ' | ' in line:
                stats['configs_updated'] += 1
        # 粗略统计规则变化
        result = subprocess.run(
            ['git', 'diff', '--numstat', '--', 'ruleset/'],
            cwd=ROOT, capture_output=True, text=True
        )
        for line in result.stdout.strip().split('\n'):
            parts = line.split('\t')
            if len(parts) >= 2:
                try:
                    stats['rules_added'] += int(parts[0]) if parts[0] != '-' else 0
                    stats['rules_removed'] += int(parts[1]) if parts[1] != '-' else 0
                except ValueError:
                    pass

        # 提取变更品牌名列表（从 git diff --name-only）
        updated_brands = []
        result_names = subprocess.run(
            ['git', 'diff', '--name-only', '--', 'ruleset/'],
            cwd=ROOT, capture_output=True, text=True
        )
        for line in result_names.stdout.strip().split('\n'):
            parts = line.strip().split('/')
            if len(parts) >= 2 and parts[0] == 'ruleset':
                brand = parts[1]
                if brand not in updated_brands:
                    updated_brands.append(brand)
        # 去重保序
        seen = set()
        updated_brands = [b for b in updated_brands if not (b in seen or seen.add(b))]

        # 8. 校验 configs（verify 失败则不提交）
        verify_result = subprocess.run(
            [sys.executable, 'scripts/verify_configs.py'],
            cwd=ROOT, capture_output=True, text=True, timeout=60
        )
        if verify_result.returncode != 0:
            log('❌ verify_configs 未通过，终止提交')
            log(verify_result.stdout[-500:] if verify_result.stdout else '')
            send_failure('verify_configs', 'verify_configs.py 校验未通过，已阻止提交')
            # 注：不回滚，ruleset 变更保留，问题修复后可手动提交
            return

        # 9. 提交 + 推送（仅非 CI 模式）
        if no_commit:
            log('🧪 CI 模式，跳过提交，由 workflow 处理')
            send_success(stats, elapsed, updated_brands)
        else:
            commit_msg = f"🔄 每日自动同步 {datetime.now().strftime('%Y-%m-%d')}"
            subprocess.run(['git', 'add', '-A'], cwd=ROOT)
            subprocess.run(['git', 'commit', '-m', commit_msg], cwd=ROOT,
                           capture_output=True)
            push = subprocess.run(['git', 'push', 'origin', 'main'], cwd=ROOT,
                                  capture_output=True, text=True, timeout=120)
            if push.returncode != 0:
                rollback('git push', push.stderr[:200])
            log('✅ 提交并推送成功')
            send_success(stats, elapsed, updated_brands)

    except Exception as e:
        rollback('未知', f'{type(e).__name__}: {str(e)[:200]}')
        traceback.print_exc()
    finally:
        # 清理备份
        if BACKUP_DIR and BACKUP_DIR.exists():
            shutil.rmtree(BACKUP_DIR)
            log('🗑️ 备份已清理')
        release_lock()
        log(f'=== 完成（{time.time() - START_TIME:.0f}s）===')


if __name__ == '__main__':
    main()