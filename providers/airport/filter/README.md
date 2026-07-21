# 📡 地区过滤正则

> 用于 `proxy-providers` 的 `filter` 和 `exclude-filter` 参数。

## 地区匹配正则

| 地区 | 正则 |
|------|------|
| 香港 | `(?i)(香港|Hong.Kong|HongKong|HK|HKG|🇭🇰)` |
| 日本 | `(?i)(日本|Japan|JP|JPN|Tokyo|🇯🇵)` |
| 美国 | `(?i)(美国|United.States|UnitedStates|US|USA|🇺🇸)` |
| 新加坡 | `(?i)(新加坡|Singapore|SG|SGP|🇸🇬)` |
| 台湾 | `(?i)(台湾|Taiwan|TW|TWN|🇹🇼)` |
| 全部（排除特定） | `(?i)(^(?!(剩余|过期|GIA)).*$)` |

## 使用示例

```yaml
proxy-providers:
  hk_provider:
    type: http
    url: "https://your-subscription-link"
    filter: "(?i)(香港|HK|HKG)"
    exclude-filter: "(?i)(剩余|过期)"
```
