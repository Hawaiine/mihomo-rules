# 📡 远程订阅配置

> 适用于 Mihomo / Clash Meta 的 `proxy-providers` 配置，`type: http`。

## 使用方式

1. 将 `config.yaml` 的内容复制到你的 `config.yaml` 文件中
2. 替换 `url` 为你的真实订阅链接
3. 根据需要调整 `filter` 和 `exclude-filter`

## 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | `http` 远程订阅 / `file` 本地文件 |
| `url` | 是(type=http) | 订阅链接 |
| `interval` | 否 | 更新间隔(秒), 默认 86400 |
| `path` | 是 | 本地缓存路径 |
| `filter` | 否 | 正则匹配保留节点名 |
| `exclude-filter` | 否 | 正则排除节点名 |
| `exclude-type` | 否 | 排除协议类型 (如 `ss|http`) |
| `health-check` | 否 | 健康检查配置 |
| `override` | 否 | 覆盖所有节点参数 |
| `header` | 否 | 自定义 HTTP 请求头 |
