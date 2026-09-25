# HTTP 订阅

复制 `config.yaml` 到主配置的 `proxy-providers:` 下，替换 `url`。这是 provider 定义，不是带 `proxies:` 的订阅文件。

```yaml
proxy-providers:
  provider_name:
    type: http
    url: "https://example.com/subscribe?token=YOUR_TOKEN"
    path: ./providers/provider_name.yaml
    interval: 86400
    proxy: DIRECT
    size-limit: 0
    header:
      User-Agent:
        - "mihomo"
    filter: "(?i)(香港|\\bHK\\b|HKG)"
    exclude-filter: "(?i)(剩余|过期|流量耗尽)"
    exclude-type: "ss|http"
    health-check:
      enable: true
      url: https://cp.cloudflare.com/generate_204
      interval: 300
      timeout: 5000
      lazy: true
      expected-status: 204
    override:
      udp: true
      # skip-cert-verify: false
```

## 字段

| 参数 | 说明 |
|------|------|
| `health-check.interval` | 秒。启用后默认 300 |
| `health-check.timeout` | 毫秒 |
| `health-check.lazy` | 默认 `true`，这组节点没人用时不测速 |
| `health-check.expected-status` | 期望 HTTP 状态，如 `204` 或 `200/204/301-308` |
| `override.skip-cert-verify` | 默认不要开。只在目标证书确实不可验证时单独打开 |
| `override.udp-over-tcp` | 写到节点的 `udp-over-tcp`。Shadowsocks 会用，不是 TUIC 的字段 |
| `override.up` / `down` | 只对认这个字段的协议生效（Hysteria / Hysteria2） |
| `override.proxy-name` | `pattern` 是 regexp2，`target` 用 `$1` 引用分组。替换后才加前后缀 |
| `override.override-expr` | yq v4 风格的子集，按顺序执行，晚于上面的固定字段 |

`override-expr` 不是完整 jq/yq。没有变量、`reduce`、递归下降，也不能读环境变量或文件。每条结果必须是一个 mapping。条件筛选用 `select`。

`age-secret-key` 只解密 age armor。核心不会主动把公钥发给服务器，要自己放进 `header` 或在服务器侧配置。私钥用 `mihomo age keygen` 生成，不要把真私钥写进仓库。

`${ENV}` 不会被 Mihomo 展开。
