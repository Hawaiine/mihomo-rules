# 🖥️ OpenWrt Nikki 配置

> 适用于 OpenWrt Nikki (mihomo 内核) — 透明代理插件

## 📋 文件

- `config.yaml` — 主配置文件（自动生成，带详细注释）
- `config.min.yaml` — 精简版（无注释，体积更小）

## 🚀 使用步骤

### 1. 安装 Nikki

```bash
# 添加 Nikki 源
wget -O - https://github.com/nikkinikki-org/OpenWrt-nikki/raw/main/feed.sh | ash

# 安装
opkg install nikki luci-app-nikki luci-i18n-nikki-zh-cn
```

### 2. 配置

```bash
# 将 config.yaml 复制到 Nikki 目录
cp config.yaml /etc/nikki/config/

# 替换 proxy-providers 中的订阅链接
vi /etc/nikki/config/config.yaml
```

### 3. 启动

```bash
# 重启 Nikki
/etc/init.d/nikki restart

# 检查日志
logread -e nikki
```

### 4. 品牌分流（可选）

按需取消注释 `rules:` 中的品牌 RULE-SET（如 `Netflix`、`Bilibili`），或通过 Nikki LuCI 面板的「mixin」功能添加。

## ⚙️ 配置要点

| 参数 | 值 | 说明 |
|------|-----|------|
| `mixed-port` / `port` / `socks-port` | 7890 / 8080 / 1080 | 混合 + HTTP + SOCKS5 端口 |
| `allow-lan` / `bind-address` | true / `"*"` | 局域网共享 |
| `mode` | rule | 规则模式 |
| `log-level` | info | 日志级别 |
| `ipv6` | true | 启用 IPv6 |
| `keep-alive-interval` / `keep-alive-idle` | 15 / 600 | TCP 保活（Nikki FAQ 推荐） |
| `find-process-mode` | off | Nikki 路由模式，不依赖进程匹配 |
| `external-controller` | 0.0.0.0:9090 | RESTful API（所有接口，LuCI 面板通信） |
| `external-ui` | ./ui | zashboard 面板（Nikki 自动下载） |
| `profile.store-selected` / `store-fake-ip` | true | 策略持久化 |
| `unified-delay` / `tcp-concurrent` | true | 统一延迟 + 并发连接 |
| `disable-icmp-forwarding` | true | TUN 防 ping 走代理 |
| `tun.enable` | true | 开启 TUN 透明代理 |
| `tun.device` | nikki | TUN 设备名（匹配 Nikki 默认） |
| `tun.stack` | mixed | 混合堆栈（system/gvisor） |
| `tun.dns-hijack` | tcp/udp://any:53 | 劫持 DNS 到 fake-ip |
| `tun.auto-route/auto-redirect` | false | 由 Nikki nftables 管理，不由 mihomo 控制 |
| `dns.listen` | 0.0.0.0:1053 | DNS 监听（Nikki 默认，nftables 劫持 53→1053） |
| `dns.enhanced-mode` | fake-ip | fake-ip 模式 |
| `dns.fake-ip-filter` | +.lan, +.local, +.corp, NTP 等 | 不使用 fake-ip 的域名 |
| `profile.store-selected` | true | 策略组选择持久化 |
| `profile.store-fake-ip` | true | fake-ip 缓存持久化 |

## 📡 DNS 分流说明

```
default-nameserver: 223.5.5.5, 119.29.29.29             ← 仅解析 nameserver 域名 IP
nameserver:         阿里 DoH, DNSPod DoH + UDP 兜底    ← 常规 DNS 查询
proxy-server-ns:    阿里 DoH, DNSPod DoH + UDP 兜底    ← 代理服务器域名专用（全国内）
nameserver-policy:
  geosite:private,cn        → 阿里/DNSPod DoH + UDP    ← 国内域名走国内 DNS
  geosite:geolocation-!cn   → Cloudflare/Google DoT    ← 国际域名走 DoT (tls://)
fallback:                   Cloudflare/Google DoT + UDP ← 兜底（并发查询，取最快）
fallback-filter:            geoip:cn + ipcidr           ← CN 域名不经过 fallback
```

## 🔗 规则匹配顺序

```
1. 拦截    RULE-SET,Reject + GEOSITE 广告
2. 品牌    Netflix/Bilibili 等（按需取消注释）
3. 局域网   LanCIDR + Private + Direct
4. 国内IP  CNCIDR + GEOIP,CN
5. 代理    RULE-SET,Proxy
6. 兜底    MATCH
```

品牌规则放在国内规则前，避免被 `GEOIP,CN` 提前截胡。

## 📦 策略组说明

| 策略组 | 类型 | 说明 |
|--------|------|------|
| ♻️ 自动选择 | url-test | 按延迟自动切换（仅代理节点） |
| 🔧 手动切换 | select | 手动选择节点 |
| 🛑 全球拦截 | select | 广告/恶意网站拦截 |
| 🐟 漏网之鱼 | select | 未匹配规则的兜底策略（DIRECT 兜底） |
| 🔯 故障转移 | fallback | 自动切换可用节点（♻️→🔧） |
| 🔀 负载均衡 | load-balance | 多节点并发复用 |
| 🌍 地区节点 | select × 5 | 🇭🇰🇯🇵🇺🇸🇸🇬🇹🇼 地区分组 |
| 品牌策略组(105个) | select | DIRECT → ♻️ → 🔧，每个品牌一个组 |

## 📡 数据库下载

`geox-url` 配置了 jsDelivr CDN 加速地址，首次启动自动下载：

```
geoip:   https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geoip.dat
geosite: https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geosite.dat
mmdb:    https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/country.mmdb
asn:     https://github.com/xishang0128/geoip/releases/download/latest/GeoLite2-ASN.mmdb
```

## 🔧 Mixin 自定义

Nikki 支持 `mixin.yaml` 覆盖或追加配置，可在 LuCI 面板中编辑。支持 `nikki-proxies`、`nikki-proxy-groups`、`nikki-rules` 预置节点/组/规则。