# 📋 CHANGELOG

> 本文记录工程与行为变更。日更各品牌规则条数增减见 git log / Discord 通知，不在此逐品牌罗列。

---

## 2026-07-24

### Added
- **verify_rulesets.py** — 新增 ruleset 一致性校验脚本，检查 header/payload/README/behavior 对齐，失败 exit≠0
- **send_idle()** — 无变化时发送「⏸️ 上游无变化」Discord 通知
- **has_meaningful_diff()** — 独立比较 YAML/README 忽略 Updated 噪音

### Changed
- **batch_update.py** — 校验失败改为 `sys.exit(1)`（含 `--no-commit`）；commit 前 `git status --porcelain` 门闩，无实质 diff 不 commit；`git add -A` 改为白名单 `ruleset/ configs/ scripts/`
- **commit_writer.py** — YAML/README 独立判断写入，payload 不变不碰 Updated，统计不变不写 README
- **daily-sync.yml** — 提交前显式 `verify_configs` + `verify_rulesets`；`setup-python@v5→v7` 消除 Node.js 20 弃用警告；失败通知补 User-Agent
- **canonical.py** — `PROCESS-NAME`/`PROCESS-PATH` 不做全局 lower，仅 strip 去尾点号，保留上游原始大小写
- **configs** — min rules 段内无空行；full 版文案去掉「97 品牌」旧数
- **README.md** — 全面对齐 Python 管线，清理过时 shell 描述，品牌口径修正为 99+7=106

### Fixed
- Google header 残留脏数据（`# IP-CIDR6: 5→0`）
- Apple/Amazon/Disney 等品牌 README 统计与 payload 不一致（逐品牌仅一次纠偏）
- 日更噪音：仅 `# Updated:` 变化不再产生写入/提交

---

## 2026-07-22

### Added
- **ownership_map.py** — SUB_PARENT 父子品牌映射单源化，`generate_config` 与 `resolve_ownership` 从此统一读取
- 策略组排序规则：子品牌优先于父品牌，其余按显示名字母序

### Changed
- **resolve_ownership.py** — 仅 `to_remove` 非空时写盘；`kept→CanonicalRule` 统一走 `commit_writer.write_ruleset`
- **generate_config.py** — 幂等加固（hash 相同不写 configs、不改 mtime）；两次生成 `git diff` 为空
- 命名两线（技术 ID + 显示名）文档化，RULE-SET 拼法规范，三套顺序规则确立

---

## 2026-07-17

### Added
- 品牌级 6 路并发拉取
- 域名/CIDR 格式校验
- 异常量级 Discord 双通道报警

### Changed
- CI rebase 冲突显式处理
- 增量清洗模式：只处理本次同步变更过的品牌

---

## 2026-07-15

- 项目初始化结束，进入稳定维护期
- 代码审计追溯 22 commits，全部通过
- CI 覆盖问题根治：sort -u 去重 + rebase 失败自动兜底

---

## 历史说明

2026-07-10～07-19 期间曾有大量按品牌记录的增量条目（反复出现 Netflix/Apple/Amazon 等 +N 条记录），因重复较多且无工程价值，已从此文件删除。历史明细可查阅 git log 或 Discord 通知。核心变更摘要：

- 2026-07-10：地区过滤 provider 启用 + 策略组环路修复 + DOMAIN-REGEX 支持 + DNS DNSPod 优先 + weekly geo 更新 + config.min.yaml 无注释版
- 2026-07-19：全量三源首次批量同步，约 30 万+ 条规则写入

> **日更策略**：各品牌规则条数增减由 `scripts/batch_update.py` 在 Discord 通知中报告，不自动写入 CHANGELOG。工程变更才记入此文件。