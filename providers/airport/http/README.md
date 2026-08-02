# 📡 远程订阅配置

> 适用于 Mihomo / Clash Meta 的 `proxy-providers` 配置，`type: http`。

## 完整参数参考

```yaml
proxy-providers:
  provider_name:
    type: http
    url: "https://example.com/subscribe?token=xxx"  # 必填
    interval: 86400                                   # 更新间隔 (秒)
    path: ./providers/provider_name.yaml             # 必填，缓存路径
    header:                                           # 自定义请求头
      User-Agent:
        - "mihomo/1.18.3"
      # Authorization:
      #   - "Bearer your_token"
    filter: "(?i)(香港|HK|HKG)"                      # 保留节点 (正则)
    exclude-filter: "(?i)(剩余|过期)"                 # 排除节点 (正则)
    exclude-type: ""                                  # 排除类型: "ss|http|vmess"
    health-check:
      enable: true
      url: https://cp.cloudflare.com/generate_204
      interval: 300
      timeout: 5000
      lazy: true                                      # 仅当被代理组引用时检查
      expected-status: 204
    override:
      udp: true
      skip-cert-verify: true
      # additional-prefix: "[provider] "
      # additional-suffix: " |"
      # down: "50 Mbps"
      # up: "10 Mbps"
      # ip-version: ipv4-prefer
      # proxy-name:
      #   - pattern: "IPLC-(.?)倍"
      #     target: "iplc $1"
      # override-expr:
      #   - '.name = "[provider] " + .name'
    payload:                                         # 直接嵌入节点
      - name: "inline"
        type: vless
        server: example.com
        port: 443
        uuid: xxx
```

## 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | `http` 远程订阅 |
| `url` | 是 | 订阅链接 |
| `path` | 是 | 本地缓存路径 |
| `interval` | 否 | 更新间隔(秒), 默认 86400 |
| `header` | 否 | 自定义 HTTP 请求头 |
| `filter` | 否 | 正则匹配保留节点名 |
| `exclude-filter` | 否 | 正则排除节点名 |
| `exclude-type` | 否 | 排除协议类型 (如 `ss|http`) |
| `health-check` | 否 | 健康检查配置 |
| `override` | 否 | 覆盖所有节点参数 |
| `payload` | 否 | 直接嵌入的节点列表 |
