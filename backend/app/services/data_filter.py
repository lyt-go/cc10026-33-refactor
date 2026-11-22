"""数据过滤领域服务：管理API数据过滤规则、字段过滤和精度约束。

跨模块数据流：
- 消费 api_registry 快照 → 关联API过滤规则自动生效
- 被 field_mask 消费 → 过滤后的敏感字段进入脱敏流程
- 被 result_format 消费 → 过滤结果进入格式化
- 被 query_optimize 消费 → 过滤条件优化查询计划
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.field_contract import read_column
from app.store import store


class DataFilterService(BaseModuleService):
    MODULE = "data_filter"
    ENTITY = "过滤规则"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["规则编号", "关联API", "过滤字段", "过滤条件", "模糊策略", "精度约束", "空值处理", "规则版本"]
    REQUIRED_FIELDS = ["规则编号", "关联API", "过滤字段"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    def apply_filters(self, api_name: str, data_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """对指定API的查询结果应用过滤规则。"""
        rows = store.rows(self.MODULE)
        active_rules = [r for r in rows if r.get("关联API") == api_name and r.get("status") == "已生效"]
        if not active_rules:
            return data_rows

        filtered = []
        for row in data_rows:
            passed = True
            for rule in active_rules:
                field = rule.get("过滤字段", "")
                condition = rule.get("过滤条件", "")
                null_handling = rule.get("空值处理", "")
                cell = read_column(row, field)

                # 空值处理
                if cell.is_empty:
                    if null_handling == "排除空值":
                        passed = False
                        break
                    elif null_handling == "保留空值":
                        continue

                # 条件匹配
                if condition and cell.value:
                    if condition.casefold() not in str(cell.value).casefold():
                        passed = False
                        break

            if passed:
                filtered.append(row)

        return filtered

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        if action == "标记生效":
            from app.services.data_manager import read_api_snapshot
            api_name = entry.get("关联API", "")
            registry = read_api_snapshot()
            for api_data in (registry.values() if isinstance(registry, dict) else []):
                if isinstance(api_data, dict) and api_data.get("api_name") == api_name:
                    break
            else:
                return None, "关联API尚未注册，无法标记生效过滤规则"

        result = super().run_action(entry_id, action)
        from app.services.data_manager import write_module_snapshot
        write_module_snapshot(self.MODULE, self.domain_snapshot())
        return result

    def domain_snapshot(self) -> dict[str, Any]:
        rows = store.rows(self.MODULE)
        filter_list = []
        for row in rows:
            filter_list.append({
                "id": row.get("id"),
                "rule_no": row.get("规则编号"),
                "api": row.get("关联API"),
                "field": row.get("过滤字段"),
                "condition": row.get("过滤条件"),
                "fuzzy": row.get("模糊策略"),
                "precision": row.get("精度约束"),
                "null_handling": row.get("空值处理"),
                "version": row.get("规则版本"),
                "status": row.get("status"),
            })
        return {
            "module": self.MODULE,
            "entity": self.ENTITY,
            "artifact": f"{self.ENTITY}数据集",
            "count": len(filter_list),
            "filters": filter_list,
            "active_count": sum(1 for r in filter_list if r.get("status") == "已生效"),
        }