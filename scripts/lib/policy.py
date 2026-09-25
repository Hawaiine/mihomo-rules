"""基础集域名策略：Direct / Proxy 的无点品牌词白名单。

Private 不在这张表里。Private 的无点词与上游 private.txt 保持一致，
不按白名单删除。
"""

# Direct 无点词：只留 cn，以及 xn-- 开头的 punycode。
DIRECT_BARE_ALLOW = frozenset({"cn"})

# Proxy 无点词：只留 xn-- 开头的 punycode。
# 不加入 microsoft、google、baidu、apple、aws。
PROXY_BARE_ALLOW = frozenset()

_BARE_ALLOW = {
    "Direct": DIRECT_BARE_ALLOW,
    "Proxy": PROXY_BARE_ALLOW,
}


def is_bare(value: str) -> bool:
    """无点品牌词：值里没有 '.'。"""
    return "." not in value


def is_single_char(value: str) -> bool:
    """单字符。白名单也不能留下单字符。"""
    return len(value) == 1


def is_allowed_bare_suffix(ruleset: str, value: str) -> bool:
    """Direct / Proxy 的无点词是否允许留下。

    单字符一律不允许。xn-- 开头的 punycode 两个规则集都留。
    Private 和其他规则集不使用这张表。
    """
    if is_single_char(value):
        return False
    if value.lower().startswith("xn--"):
        return True
    allow = _BARE_ALLOW.get(ruleset)
    if allow is None:
        return False
    return value.lower() in allow
