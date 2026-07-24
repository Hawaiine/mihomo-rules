<div align="center">
  <img src="https://raw.githubusercontent.com/MetaCubeX/mihomo/Meta/docs/logo.png" width="160" alt="mihomo-rules">
</div>

<div align="center">
  <h1>mihomo-rules</h1>
</div>

<p align="center">
  <em>Mihomo / clash-meta 通用 RULE-SET 规则集仓库 · 106 品牌 32 万+ 规则 · 每日自动同步</em>
</p>

<p align="center">
  <img src="https://img.shields.io/github/repo-size/Hawaiine/mihomo-rules" alt="仓库大小">
  <img src="https://img.shields.io/github/last-commit/Hawaiine/mihomo-rules" alt="最后更新">
  <img src="https://img.shields.io/github/actions/workflow/status/Hawaiine/mihomo-rules/daily-sync.yml?label=CI" alt="CI状态">
  <img src="https://img.shields.io/badge/platform-Nikki%20%7C%20Android-lightgrey" alt="支持平台">
  <img src="https://img.shields.io/badge/rulesets-106-blue" alt="规则集数量">
  <img src="https://img.shields.io/badge/brands-99-orange" alt="品牌数量">
  <img src="https://img.shields.io/github/license/Hawaiine/mihomo-rules" alt="许可证">
</p>

---

## 📖 概述

**mihomo-rules** 是一个为 Mihomo / clash-meta 内核设计的通用 RULE-SET 级规则集仓库。自动从 **v2fly/domain-list-community**、**Loyalsoldier/clash-rules**、**blackmatrix7/ios_rule_script** 三大上游同步数据，提供完整的**基础规则集** + **品牌规则集**（流媒体 / AI / 社交 / 云服务 / 游戏等 106 品牌）。

项目包含开箱即用的平台配置文件（**OpenWrt Nikki** + **Clash for Android**），自动品牌图标注入，每日 CI 自动同步，并支持 Discord 通知。

## ✨ 特性
| 特性 | 说明 |
|------|------|
| 🔄 **每日自动同步** | 北京时间 06:00 自动从 3 个上游合并最新规则，Discord 通知 |
| 📦 **即用配置** | 内置 Android + Nikki 完整配置，带注释版 + 无注释精简版，替换订阅链接即可使用 |
| 🎨 **品牌图标注入** | 自动匹配 Oasisic-Icons 品牌图标（99 品牌），`scripts/match_icons.py` 生成映射 |
| ⚡ **Python 管线** | 8 步串联（fetch → parse → merge → write → resolve → icons → config → verify），全自动幂等运行 |
| 🛡️ **双 verify 门禁** | `verify_configs` + `verify_rulesets` 提交前必过，失败则 `sys.exit(1)` 阻止 CI 提交 |
| 🔒 **PROCESS 大小写保护** | `PROCESS-NAME`/`PROCESS-PATH` 不做全局 lower，仅 strip 去尾点号，上游原始大小写保留 |
| 🔧 **写入幂等** | `has_meaningful_diff` 忽略 `Updated:` 噪音；payload 不变不写 YAML，统计不变不写 README |
| 📊 **增量清洗** | 只处理本次同步变更过的品牌，非全量文件扫描 |
| 🛡️ **格式校验** | 对 DOMAIN/DOMAIN-SUFFIX 域名格式做校验，异常报警不丢弃 |
| 🔍 **异常量级检测** | 品牌规则量突增超过历史倍数时，CI 日志 + Discord 双通道报警 |
| 🌐 **国内加速** | 内置 DNSPod/阿里 DNS 优先，国内 CDN 加速 geoip/geosite 数据库下载 |
| 📝 **失败归档** | 失败 Action 保留排障线索 |

## 🏗️ 项目架构

```
mihomo-rules/
├── ruleset/                      # 规则集（每个品牌独立子目录）
│   ├── Direct/                   # 基础规则集 (7个)
│   │   ├── Direct.yaml           # 规则集文件（Header + payload）
│   │   └── README.md             # 品牌说明（统计/behavior/使用方式）
│   ├── Netflix/                  # 品牌规则集 (99个)
│   ├── OpenAI/
│   └── .../
├── configs/                      # 平台配置文件
│   ├── Android/                  # Clash for Android
│   │   ├── config.yaml           # 带注释版
│   │   ├── config.min.yaml       # 无注释精简版
│   │   └── README.md
│   └── Nikki/                    # OpenWrt Nikki（mihomo 内核）
│       ├── config.yaml           # 带注释版
│       ├── config.min.yaml       # 无注释精简版
│       └── README.md
├── providers/                    # 节点配置模板
│   ├── airport/                  # 机场订阅配置
│   │   ├── http/                 # 远程订阅
│   │   ├── file/                 # 本地订阅
│   │   └── filter/               # 地区过滤正则
│   └── nodes/                    # 单节点配置（按协议分组）
├── scripts/                      # Python 核心管线
│   ├── batch_update.py           # 日更入口：8 步串联 + 校验 + Discord 通知
│   ├── fetch_upstream.py         # 从 3 个上游拉取原始数据
│   ├── parse_v2fly.py            # v2fly 数据解析
│   ├── parse_loyalsoldier.py     # Loyalsoldier 数据解析
│   ├── parse_blackmatrix7.py     # blackmatrix7 数据解析
│   ├── merge_and_dedup.py        # 三源合并 + 去重
│   ├── commit_writer.py          # 写入 ruleset YAML + README（含幂等）
│   ├── resolve_ownership.py      # 品牌归属去重（子品牌规则移到父品牌）
│   ├── match_icons.py            # Oasisic-Icons 品牌图标映射
│   ├── generate_config.py        # 生成 Android + Nikki 双平台配置
│   ├── verify_configs.py         # 配置校验（10 项检查）
│   ├── verify_rulesets.py        # ruleset 一致性校验（header/payload/README）
│   └── lib/                      # 共享库
│       ├── canonical.py          # 规则解析、标准化、排序
│       ├── ownership.py          # 品牌归属逻辑
│       ├── ownership_map.py      # SUB_PARENT 父子品牌映射
│       └── validators.py         # 格式校验
├── .github/workflows/
│   └── daily-sync.yml            # 每日 CI 自动同步 + 双 verify + Discord 通知
├── CHANGELOG.md
└── README.md
```

## 📡 上游数据源

| # | 上游 | 内容 | 合并策略 |
|---|------|------|---------|
| ① | [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community) | 域名 | `data/<brand>` 文件，递归解析 `include:` 引用（≤5 层） |
| ② | [Loyalsoldier/clash-rules](https://github.com/Loyalsoldier/clash-rules) | 域名 + IP-CIDR | `release/` 目录，Python 解析 YAML payload |
| ③ | [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script) | 域名 + IP-CIDR + IP-CIDR6 + PROCESS-NAME | 域名仅补漏，IP-CIDR/PROCESS-NAME 无条件全加 |

**合并逻辑：** 已有规则集 → ① v2fly 补充域名 → ② Loyalsoldier 补充域名+IP-CIDR → ③ blackmatrix7 域名仅补漏、IP-CIDR/PROC-NAME 无条件全加 → 合并去重（`CanonicalRule` 精确匹配）→ 按类型分组 → 字母序排序 → 重写 Header

## 🚀 快速使用

### 在现有 mihomo 配置中引用

```yaml
rule-providers:
  Netflix:
    type: http
    behavior: classical
    url: "https://raw.githubusercontent.com/Hawaiine/mihomo-rules/main/ruleset/Netflix/Netflix.yaml"
    interval: 86400
    path: ./ruleset/Netflix.yaml

rules:
  - RULE-SET,Reject,🛑 全球拦截
  - RULE-SET,Netflix,Netflix
  - MATCH,🐟 漏网之鱼
```

### 直接使用内置配置

```bash
# Clash for Android (带注释版 / 无注释精简版)
configs/Android/config.yaml
configs/Android/config.min.yaml

# OpenWrt Nikki (带注释版 / 无注释精简版)
configs/Nikki/config.yaml
configs/Nikki/config.min.yaml
```

替换 `proxy-providers` 中的订阅链接后直接导入。也可启用内置的地区过滤 provider 实现节点自动归类。

## ⚙️ 配置结构

生成的 config 按官方 mihomo 文档顺序排列：

```
mixed-port → port → socks-port → allow-lan → find-process-mode → mode →
log-level → ipv6 → tcp-concurrent → unified-delay → keep-alive-interval →
keep-alive-idle → external-controller → secret → external-ui → profile →
geodata-mode → geodata-loader → geo-auto-update → geo-update-interval →
geox-url → tun → dns → proxy-providers → proxy-groups → rule-providers → rules
```

### 端口配置（双平台）

| 配置 | Android | Nikki |
|------|---------|-------|
| mixed-port | 7890 (HTTP+SOCKS5) | 7890 |
| port (HTTP) | 7891 | 8080 |
| socks-port | 7892 | 1080 |
| dns.listen | 0.0.0.0:53 | 0.0.0.0:1053 |
| external-controller | 127.0.0.1:9090 | 0.0.0.0:9090 |

### DNS 三段式分流

```
default-nameserver: 119.29.29.29, 223.5.5.5       ← DNSPod 优先
nameserver:         DNSPod DoH, 阿里 DoH           ← 国内 DoH
proxy-server-ns:    Cloudflare DoH                 ← 代理服务器专用（防死循环）
nameserver-policy:
  geosite:cn              → 国内 DNS               ← 按 geosite 分流
  geosite:geolocation-!cn → 国际 DNS
fallback:                 Cloudflare/Google DoH    ← 兜底
fallback-filter:          geoip:cn + geosite:cn    ← CN 域名不经过 fallback
fake-ip-filter-mode:      blacklist                ← 黑名单模式
fake-ip-filter:           geosite:private, +.lan, +.local, +.corp
```

### 地区过滤 provider

配置文件内置 5 个地区过滤 provider，填入与 `provider1` 相同的订阅地址即可自动归类节点：

| Provider | Filter | 对应策略组 |
|----------|--------|-----------|
| `provider_hk` | `(?i)(香港\|Hong.Kong\|HK\|HKG\|🇭🇰)` | 🇭🇰 香港节点 |
| `provider_jp` | `(?i)(日本\|Japan\|JP\|Tokyo\|🇯🇵)` | 🇯🇵 日本节点 |
| `provider_us` | `(?i)(美国\|United.States\|US\|USA\|🇺🇸)` | 🇺🇸 美国节点 |
| `provider_sg` | `(?i)(新加坡\|Singapore\|SG\|🇸🇬)` | 🇸🇬 新加坡节点 |
| `provider_tw` | `(?i)(台湾\|Taiwan\|TW\|🇹🇼)` | 🇹🇼 台湾节点 |

### 策略组引用结构（无环路）

```
♻️ 自动选择 → 🇭🇰 🇯🇵 🇺🇸 🇸🇬 🇹🇼 / DIRECT
🔧 手动切换 → ♻️ 自动选择 / DIRECT / use: provider1
🇭🇰 香港节点 → DIRECT / use: provider_hk (过滤后只显示香港节点)
品牌组      → ♻️ 自动选择 / 🔧 手动切换 / DIRECT
```

### 规则匹配顺序

```
1️⃣ 拦截    RULE-SET,Reject + GEOSITE 广告
2️⃣ 品牌    Netflix/Bilibili 等 99 品牌（按需取消注释，放在国内前避免被GEOIP截胡）
3️⃣ 直连    Applications(DIRECT) + LanCIDR/Private(DIRECT, 硬直连不可改)
4️⃣ 国内IP  CNCIDR + GEOIP,CN → DIRECT
| 5️⃣ 代理    RULE-SET,Proxy → 🔧 手动切换
| 6️⃣ 兜底    MATCH
```

---

## 📐 配置与命名约定

### 命名两线

每个品牌有两套名字，**不是**六处相同：

| 线 | 用途 | 规则 | 示例 |
|----|------|------|------|
| **技术 ID**（无空格） | 目录名、文件名、rule-providers key、url/path 中的 Brand 段、RULE-SET 第一段 | `ruleset/<ID>/<ID>.yaml` | `AppleTV`、`PrimeVideo`、`myTVSuper` |
| **显示名**（可有空格/符号） | 策略组名、`# Rule Name`、README 标题、RULE-SET 第二段 | `STRATEGY_GROUP_MAP` 中定义，无则 = ID | `Apple TV`、`Prime Video`、`myTV Super` |

**RULE-SET 拼法：** `RULE-SET,<技术ID>,<显示名>`

正确：`RULE-SET,AppleTV,Apple TV`
错误：`RULE-SET,Apple TV,Apple TV`（第二段可以有空格，第一段不能）

### 三套顺序（不同序是预期，集合不能变）

| 区块 | 顺序规则 | 说明 |
|------|---------|------|
| **proxy-groups** | 子品牌优先于父品牌（SUB_PARENT），其余按显示名字母序 | 系统组 11 个固定在前 |
| **full 注释 RULE-SET** | 与 proxy-groups 品牌段顺序一致 | 仅 full 版有注释，min 版无 |
| **rule-providers** | 7 兜底固定序（Reject→Direct→Proxy→Applications→Private→LanCIDR→CNCIDR）+ 品牌 key 字母序 | 与 proxy-groups **不同序是预期行为** |

### 格式约定

| 变体 | `rules:` 前 | `rules:` 后 |
|------|------------|------------|
| **full**（`config.yaml`） | 空行（与 `rule-providers` 段分隔） | 直接跟注释，无多余空行 |
| **min**（`config.min.yaml`） | 无空行（紧接 `rule-providers` 段） | 直接跟规则，无多余空行 |

### 校验与幂等

```bash
# 全量校验（10 项检查，失败 exit≠0）
python3 scripts/verify_configs.py

# ruleset 一致性校验（header/payload/README/behavior，失败 exit≠0）
python3 scripts/verify_rulesets.py

# 生成配置（幂等，无实质变化会 [=] 跳过）
python3 scripts/generate_config.py

# 日更提交前两者均须 PASS；CI 与 batch_update 双门禁
```

## 📋 近期更新

| 日期 | 内容 |
|------|------|
| 2026-07-24 | CI 门禁加固（verify_configs + verify_rulesets 双门禁，失败 sys.exit(1)）；git add 白名单；setup-python 升级消除 Node 20 警告；写入幂等加固（YAML/README 独立判断，无实质变化不写盘）；日更无实质 diff 不 commit；PROCESS 大小写保护；min rules 去空行；verify_rulesets 新增（header/payload/README/behavior 一致性） |
| 2026-07-22 | SUB_PARENT 单源化（ownership_map.py）；resolve_ownership 噪音修复；generate_config 幂等加固；命名两线文档 |
| 2026-07-17 | 品牌级 6 路并发拉取、sanitize 增量模式、域名/CIDR 格式校验、异常量级 Discord 双通道报警、CI rebase 冲突显式处理 |
| 2026-07-10 | 地区过滤 provider 启用 + 策略组环路修复 + DOMAIN-REGEX 支持 + DNS DNSPod 优先 + geo 每周更新 + config.min.yaml 无注释版 |

## 🛠️ 脚本说明

| 脚本 | 说明 | 用法 |
|------|------|------|
| `batch_update.py` | 日更入口：8 步串联（fetch → parse → merge → write → resolve → icons → config → verify） | `python3 scripts/batch_update.py` 或 `--no-commit` |
| `verify_configs.py` | 配置校验（10 项检查，集合等价/命名两线/顺序约束/格式约定） | `python3 scripts/verify_configs.py` |
| `verify_rulesets.py` | ruleset 一致性校验（header/payload/README/behavior 对齐） | `python3 scripts/verify_rulesets.py` |
| `generate_config.py` | 生成 Android + Nikki 双平台配置（幂等，无实质变化跳过） | `python3 scripts/generate_config.py` |
| `resolve_ownership.py` | 品牌归属去重（子品牌规则移到父品牌目录） | `python3 scripts/resolve_ownership.py --apply` |
| `commit_writer.py` | 写入单个品牌 YAML + README（含幂等，跳过 Updated 噪音） | 由 batch_update 调用 |
| `match_icons.py` | 从 Oasisic-Icons 生成品牌图标映射 | `python3 scripts/match_icons.py` |

> 日更入口：`python3 scripts/batch_update.py`（自动 pull → 同步 → 校验 → commit → push）  
> CI 入口：`.github/workflows/daily-sync.yml`（`batch_update --no-commit` + 显式 verify + 提交）  
> 单品牌操作：暂由 batch_update 全量管线处理，不支持单独指定品牌 CLI

## 📊 规则集统计

| 分类 | 数量 | 规则数 | 说明 |
|------|:----:|:------:|------|
| 基础规则集 | 7 | 312,828 | Reject(167K) · Direct(112K) · Proxy(27K) · CNCIDR(5.8K) · Private · Applications · LanCIDR |
| 品牌规则集 | 99 | 13,290 | 流媒体 / AI / 社交 / 云服务 / 游戏 / 电商 / 音乐 / 金融 |
| **合计** | **106** | **326,118** | DOMAIN + DOMAIN-SUFFIX + DOMAIN-KEYWORD + IP-CIDR + IP-CIDR6 + PROCESS-NAME + IP-ASN |

### 品牌分类统计

| 类别 | 品牌数 | 规则数 | 品牌 |
|------|:-----:|:------:|------|
| 🎬 流媒体 | 43 | 876 | Netflix · Disney · HBO · Prime Video · Hulu · YouTube · AbemaTV · Bahamut · Bilibili · DAZN · F1 TV · DMM TV · D Anime Store · Fuji TV · Game Japan · HOY TV · Hami Video · Hotstar · KKTV · LiTV · LINE TV · MyTVSuper · My Video · Now E · Rakuten TV · Telasa · Tubi · TVer · U-NEXT · Video Market · Viu · WOWOW · CATCHPLAY+ · Lemino · friDay video · Music Japan · Reads Japan · Mora · Podcast · Radiko · Niconico · Karaoke@DAM |
| 🤖 AI | 9 | 135 | OpenAI · Anthropic · Google AI · General AI · Perplexity · Manus · Poe · Cursor · Siri AI |
| 📱 社交 | 13 | 920 | X · Instagram · Facebook · Discord · Telegram · Reddit · TikTok · Threads · Bluesky · Messenger · WhatsApp · Pinterest · Pixiv |
| ☁️ 云服务 | 10 | 2,148 | GitHub · Cloudflare · Microsoft · Google · OneDrive · Docker · Synology · AWS · iCloud · iCloud Private Relay |
| 🎮 游戏 | 2 | 204 | Steam · Nintendo |
| 🛍️ 电商 | 2 | 485 | Amazon · PayPal |
| 🎵 音乐 | 6 | 76 | Spotify · YouTube Music · Deezer · Tidal · Qobuz · Musixmatch |
| 🏢 企业 | 4 | 1,602 | Apple · Apple TV · Z-Library · MetaBrainz |
| 🏦 金融 | 7 | 6,844 | Bank · PT · PT China · Porn China · Porn · WSJ · Wallpaper |

## 🤝 贡献指南

1. **新增品牌**：在 `ruleset/` 下创建 `<Brand>/<Brand>.yaml`，按 8 种规则类型规范编写，运行 `python3 scripts/verify_rulesets.py` 校验
2. **修复规则**：修改 YAML 文件后运行 `python3 scripts/verify_rulesets.py` 校验格式一致性
3. **提交前检查**：
   ```bash
   python3 scripts/verify_configs.py        # 配置校验
   python3 scripts/verify_rulesets.py       # ruleset 一致性校验
   python3 scripts/generate_config.py       # 配置生成测试
   ```

> 日更由 `scripts/batch_update.py` 或 CI `daily-sync.yml` 全量管线处理，不单独同步单一品牌。如需新增品牌，编辑 `scripts/lib/ownership_map.py` 添加品牌映射后运行全量管线。

## ❓ 常见问题

### Q: 规则集更新时间？
每日北京时间 06:00（UTC 22:00）自动触发 CI 同步。也可在 GitHub Actions 手动触发 `workflow_dispatch`。

### Q: 如何只同步某个品牌？
日更由 `scripts/batch_update.py` 全量管线处理，不支持单品牌 CLI。如需单独修复品牌规则，直接编辑 `ruleset/<Brand>/<Brand>.yaml` 后运行 `python3 scripts/verify_rulesets.py` 校验即可。

### Q: 配置中的订阅链接如何填写？
编辑 `configs/Android/config.yaml` 或 `configs/Nikki/config.yaml`（或同目录下的 `config.min.yaml` 无注释版），找到 `proxy-providers` 下的 `url: "此处填入你的订阅链接"`，替换为真实机场订阅地址。

### Q: 图标不显示怎么办？
```bash
python3 scripts/match_icons.py           # 从 Oasisic-Icons 同步图标映射
python3 scripts/generate_config.py       # 重新生成配置
```

### Q: 如何自定义策略组？
编辑 `scripts/generate_config.py` 中的策略组生成函数，重新生成配置即可。

## 🔗 相关资源

- **[Mihomo 官方文档](https://wiki.metacubex.one/config/)** — 配置参考
- **[Nikki](https://github.com/nikkinikki-org/OpenWrt-nikki)** — OpenWrt 透明代理插件
- **[Oasisic-Icons](https://github.com/Hawaiine/Oasisic-Icons)** —106 品牌图标库
- **[mihomo-rules-skill](https://github.com/Hawaiine/mihomo-rules-skill)** — Hermes Agent Skill
- **[问题反馈](https://github.com/Hawaiine/mihomo-rules/issues)**

## 📄 License

MIT License — 自由使用、修改、分发。