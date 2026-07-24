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
  <img src="https://img.shields.io/badge/brands-106-orange" alt="品牌数量">
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
| 🎨 **品牌图标注入** | 自动匹配 Oasisic-Icons 品牌图标（106 品牌），`icon-map.sh` 唯一数据源 |
| 🧹 **格式自动修复** | awk 驱动，全量 106 文件校验 < 1 秒；自动修复裸域名、`+.xxx` 前缀、@标签、Header 计数 |
| ⚡ **并发同步** | 品牌级别 6 路并发拉取，大幅缩短同步时间 |
| 📊 **增量清洗** | 只处理本次同步变更过的品牌，非全量 106 文件扫描 |
| 🛡️ **格式校验** | 对 DOMAIN/DOMAIN-SUFFIX 域名格式 + IP-CIDR/IP-CIDR6 掩码范围做校验，异常报警不丢弃 |
| 🔍 **异常量级检测** | 品牌规则量突增超过历史 5 倍时，CI 日志 + Discord 双通道报警 |
| ⚡ **幂等更新** | 无变更不覆盖，`Updated` 时间仅内容变更时更新 |
| 📋 **CHANGELOG 自动管理** | 已有条目原地覆盖，无则追加 |
| 🌐 **国内加速** | 内置 jsDelivr CDN 地址用于 geoip/geosite 数据库下载 |
| 🔒 **安全优先** | 写入 temp → diff 对比 → 无变化不覆盖，零数据丢失风险 |
| 🛡️ **全局清洗兜底** | 每次同步末尾强制修复 regexp 格式 + Cloudflare IP 剥离 + sort -u 去重，上游脏数据无法累积 |
| 🔄 **CI 防冲突** | rebase 先普通→冲突打印→`-X theirs` 兜底，Discord 通知冲突文件 |
| 📝 **失败归档** | 失败 Action 7 天后自动归档到 `ci-failure-log.md`，保留排障线索 |

## 🏗️ 项目架构

```
mihomo-rules/
├── ruleset/                      # 规则集（每个品牌独立子目录）
│   ├── Direct/                   # 基础规则集 (8个)
│   │   ├── Direct.yaml           # 规则集文件（Header + payload）
│   │   └── README.md             #106 品牌说明（统计/behavior/使用方式）
│   ├── Netflix/                  #106 品牌规则集 (105个)
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
│   │   ├── http/                 # 远程订阅 (config.yaml + README)
│   │   ├── file/                 # 本地订阅 (config.yaml + README)
│   │   └── filter/               # 地区过滤正则 (examples + README)
│   └── nodes/                    # 单节点配置 (按协议分组)
│       ├── shadowsocks/          # 4 种配置变体
│       ├── vmess/                # 5 种配置变体
│       ├── vless/                # 5 种配置变体
│       ├── trojan/               # 4 种配置变体
│       ├── hysteria/             # 5 种配置变体
│       ├── tuic/                 # 3 种配置变体
│       ├── wireguard/            # 3 种配置变体
│       └── ssh-snell-anytls/     # 4 种配置变体
├── scripts/                      # 核心工具脚本
│   ├── sync-upstream.sh          # 上游同步（v2fly + Loyalsoldier + blackmatrix7）
│   ├── validate-ruleset.sh       # 格式校验 + 自动修复 + README 同步
│   ├── generate-config.sh        # 配置生成（behavior 检测 + 图标注入 + DNS 分流）
│   ├── sync-icons.sh             # Oasisic-Icons 图标同步
│   ├── parse-loyalsoldier.awk    # Loyalsoldier 解析器
│   └── parse-v2fly.awk           # v2fly 解析器
├── .github/workflows/
│   └── daily-sync.yml            # 每日 CI 自动同步 + 图标 + 验证 + Discord 通知
├── CHANGELOG.md
└── README.md
```

## 📡 上游数据源

| # | 上游 | 内容 | 合并策略 |
|---|------|------|---------|
| ① | [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community) | 域名 | `data/<brand>` 文件，递归解析 `include:` 引用（≤5 层） |
| ② | [Loyalsoldier/clash-rules](https://github.com/Loyalsoldier/clash-rules) | 域名 + IP-CIDR | `release/` 目录，外部 awk 解析 YAML payload |
| ③ | [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script) | 域名 + IP-CIDR + IP-CIDR6 + PROCESS-NAME | 域名仅补漏，IP-CIDR/PROCESS-NAME 无条件全加 |

**合并逻辑：** 已有规则集 → ① v2fly 补充域名 → ② Loyalsoldier 补充域名+IP-CIDR → ③ blackmatrix7 域名仅补漏、IP-CIDR/PROC-NAME 无条件全加 → 全局去重（`sort -u`）→ 按类型分组 → 字母序排列 → 重写 Header

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
2️⃣106 品牌    Netflix/Bilibili 等（按需取消注释，放在国内前避免被GEOIP截胡）
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
| 2026-07-17 | 品牌级 6 路并发拉取、`clean_file()` awk 重写、sanitize 增量模式、域名/CIDR 格式校验、异常量级 Discord 双通道报警、CI rebase 冲突显式处理+失败 7 天归档、HEADER_ORDER 共享、分组注释移除、图标映射去重 |
| 2026-07-10 | 地区过滤 provider 启用 + 策略组环路修复 + DOMAIN-REGEX 支持 + DNS DNSPod 优先 + geo 每周更新 + config.min.yaml 无注释版 |

## 🛠️ 脚本说明

| 脚本 | 说明 | 用法 |
|------|------|------|
| `sync-upstream.sh` | 从 3 个上游同步规则（6 路并发拉取，单品牌/全量支持） | `bash scripts/sync-upstream.sh` 或 `bash scripts/sync-upstream.sh Netflix` |
| `validate-ruleset.sh` | 校验 + 自动修复 + README 同步 (7 种规则类型全量顺序校验) | `bash scripts/validate-ruleset.sh` 或 `bash scripts/validate-ruleset.sh ruleset/X/X.yaml` |
| `generate-config.sh` | 生成 Android + Nikki 双平台配置（图标从 `icon-map.sh` 读取） | `bash scripts/generate-config.sh` |
| `sync-icons.sh` | 从 Oasisic-Icons 同步品牌图标映射（唯一数据源，含 23 条手动补丁） | `bash scripts/sync-icons.sh` |
| `header-order.sh` | 共享的 HEADER 顺序定义（被 `sync-upstream.sh` 和 `validate-ruleset.sh` source） | 不独立执行 |

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

1. **新增品牌**：在 `ruleset/` 下创建 `<Brand>/<Brand>.yaml`，运行 `bash scripts/sync-upstream.sh <Brand>`
2. **修复规则**：修改 YAML 文件后运行 `bash scripts/validate-ruleset.sh` 校验格式
3. **提交前检查**：
   ```bash
   bash -n scripts/*.sh                    # 脚本语法检查
   bash scripts/validate-ruleset.sh        # 规则集格式校验
   bash scripts/generate-config.sh         # 配置生成测试
   ```

## ❓ 常见问题

### Q: 规则集更新时间？
每日北京时间 06:00（UTC 22:00）自动触发 CI 同步。也可在 GitHub Actions 手动触发 `workflow_dispatch`。

### Q: 如何只同步某个品牌？
```bash
bash scripts/sync-upstream.sh Netflix     # 只同步 Netflix
bash scripts/sync-upstream.sh OpenAI      # 只同步 OpenAI
```

### Q: 配置中的订阅链接如何填写？
编辑 `configs/Android/config.yaml` 或 `configs/Nikki/config.yaml`（或同目录下的 `config.min.yaml` 无注释版），找到 `proxy-providers` 下的 `url: "此处填入你的订阅链接"`，替换为真实机场订阅地址。

### Q: 图标不显示怎么办？
```bash
bash scripts/sync-icons.sh                # 从 Oasisic-Icons 同步图标映射
bash scripts/generate-config.sh           # 重新生成配置
```

### Q: 如何自定义策略组？
编辑 `scripts/generate-config.sh` 中的 `write_global_groups()` 和 `write_brand_groups()` 函数，重新生成配置即可。

## 🔗 相关资源

- **[Mihomo 官方文档](https://wiki.metacubex.one/config/)** — 配置参考
- **[Nikki](https://github.com/nikkinikki-org/OpenWrt-nikki)** — OpenWrt 透明代理插件
- **[Oasisic-Icons](https://github.com/Hawaiine/Oasisic-Icons)** —106 品牌图标库
- **[mihomo-rules-skill](https://github.com/Hawaiine/mihomo-rules-skill)** — Hermes Agent Skill
- **[问题反馈](https://github.com/Hawaiine/mihomo-rules/issues)**

## 📄 License

MIT License — 自由使用、修改、分发。