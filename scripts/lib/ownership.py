"""
lib/ownership.py — 域名归属查询/认领/迁移

全局归属表，解决跨品牌域名归属冲突。
"""

import json
from typing import NamedTuple


# ── 迁移建议数据结构 ──────────────────────────────────────────

class MigrationSuggestion(NamedTuple):
    """迁移建议"""
    domain: str
    current_owner: str
    proposed_owner: str
    reason: str


class MigrationAction(NamedTuple):
    """迁移操作"""
    action: str   # "move" / "keep" / "split"
    domain: str
    from_brand: str
    to_brand: str
    note: str


# ── 归属注册表 ────────────────────────────────────────────────

class OwnershipRegistry:
    """
    全局域名归属注册表。

    维护域名 → 所属品牌的映射关系，支持：
    - 查询域名当前归属
    - 认领新域名
    - 检查跨品牌冲突
    - 生成迁移建议
    """

    def __init__(self):
        # 域名 → 品牌（小写域名作为 key）
        self._registry: dict[str, str] = {}
        # 品牌 → 域名集合（反向索引）
        self._brand_domains: dict[str, set[str]] = {}

    # ── 序列化 ──────────────────────────────────────────────

    @classmethod
    def load(cls, filepath: str) -> "OwnershipRegistry":
        """
        从 JSON 文件加载归属表。

        Args:
            filepath: ownership.json 路径

        Returns:
            OwnershipRegistry 实例
        """
        registry = cls()
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
            # 格式: {"domain": "brand", ...}
            for domain, brand in data.items():
                registry._registry[domain.lower()] = brand
                brand_set = registry._brand_domains.setdefault(brand, set())
                brand_set.add(domain.lower())
        except (FileNotFoundError, json.JSONDecodeError):
            pass  # 文件不存在或格式错误，返回空注册表
        return registry

    def save(self, filepath: str):
        """
        持久化到 JSON 文件。

        Args:
            filepath: 输出路径
        """
        with open(filepath, "w") as f:
            json.dump(self._registry, f, indent=2, ensure_ascii=False)

    # ── 查询与认领 ──────────────────────────────────────────

    def query_owner(self, domain: str) -> str | None:
        """
        查询域名当前归属品牌。

        Args:
            domain: 域名

        Returns:
            品牌名，无归属时返回 None
        """
        return self._registry.get(domain.lower())

    def claim_domain(self, domain: str, brand: str):
        """
        认领域名给指定品牌。

        Args:
            domain: 域名
            brand:  品牌名
        """
        domain_lower = domain.lower()
        # 如果域名已被其他品牌持有，先移除旧索引
        old_owner = self._registry.get(domain_lower)
        if old_owner and old_owner != brand:
            old_set = self._brand_domains.get(old_owner)
            if old_set:
                old_set.discard(domain_lower)

        self._registry[domain_lower] = brand
        self._brand_domains.setdefault(brand, set()).add(domain_lower)

    def get_owned_domains(self, brand: str) -> set[str]:
        """
        获取某品牌已认领的所有域名。

        Args:
            brand: 品牌名

        Returns:
            域名集合
        """
        return self._brand_domains.get(brand, set()).copy()

    def get_all_brands(self) -> list[str]:
        """获取所有已知品牌名"""
        return list(self._brand_domains.keys())

    # ── 冲突检查 ────────────────────────────────────────────

    def check_conflicts(
        self,
        brand: str,
        domains: set[str],
    ) -> list[MigrationSuggestion]:
        """
        检查 brand 要认领的域名中，哪些已被其他品牌持有。

        Args:
            brand:   要检查的品牌名
            domains: 该品牌要认领的域名集合

        Returns:
            迁移建议列表
        """
        suggestions: list[MigrationSuggestion] = []
        for domain in domains:
            owner = self._registry.get(domain.lower())
            if owner and owner != brand:
                suggestions.append(MigrationSuggestion(
                    domain=domain,
                    current_owner=owner,
                    proposed_owner=brand,
                    reason=f"域名 {domain} 当前归属 {owner}，建议迁移至 {brand}",
                ))
        return suggestions

    def generate_migration_plan(
        self,
        conflicts: list[MigrationSuggestion],
    ) -> list[MigrationAction]:
        """
        生成迁移操作计划（不自动执行，只输出报告）。

        Args:
            conflicts: check_conflicts 的输出

        Returns:
            迁移操作列表
        """
        plan: list[MigrationAction] = []
        for c in conflicts:
            plan.append(MigrationAction(
                action="move",
                domain=c.domain,
                from_brand=c.current_owner,
                to_brand=c.proposed_owner,
                note=f"建议从 {c.current_owner} 迁移至 {c.proposed_owner}",
            ))
        return plan

    # ── 批量操作 ────────────────────────────────────────────

    def claim_domains(self, domains: set[str], brand: str):
        """
        批量认领域名给指定品牌。

        Args:
            domains: 域名集合
            brand:   品牌名
        """
        for domain in domains:
            self.claim_domain(domain, brand)