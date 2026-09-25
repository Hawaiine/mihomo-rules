# 远程订阅配置

`proxy-providers` 片段，不是完整配置。复制到主配置的 `proxy-providers:` 下面。

三种来源：

- `http/`：远程订阅
- `file/`：本地订阅文件（文件本身要有顶层 `proxies:`）
- `filter/`：节点名过滤示例

`type: inline` 把节点直接写在 `payload` 里，示例在 `http/config.yaml`。

## 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | `http` / `file` / `inline` |
| `url` | http 必填 | 订阅地址 |
| `path` | file 必填，http 可选 | 本地路径。http 留空时用 URL 的 MD5，且必须在 HomeDir（`-d`）或 `SAFE_PATHS` 内 |
| `interval` | 否 | 更新间隔，秒 |
| `proxy` | 否 | 下载时走的出站，默认直连 |
| `size-limit` | 否 | 下载大小上限，字节，`0` 为不限制 |
| `age-secret-key` | 否 | age 私钥，只解密 armor 格式 |
| `header` | 否 | 自定义请求头。值是字符串列表 |
| `filter` | 否 | 保留节点名。regexp2，多段用反引号分隔 |
| `exclude-filter` | 否 | 排除节点名，语法同 `filter` |
| `exclude-type` | 否 | 按 `type` 排除，用 `\|` 分隔，不是正则 |
| `health-check` | 否 | 延迟测试 |
| `override` | 否 | 覆盖节点字段，见 `http/README.md` |
| `payload` | inline 使用 | http/file 解析失败时也可作为备用节点 |

不要把这份片段再套一层 `proxy-providers:`，否则键会重复。
