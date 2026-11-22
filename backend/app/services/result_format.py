"""结果格式化领域服务：管理查询结果的输出格式、字段映射和分页配置。

跨模块数据流：
- 消费 api_registry 快照 → 关联API自动选择格式模板
- 消费 data_filter 快照 → 过滤后数据按模板格式化
- 消费 field_mask 快照 → 脱敏后数据按模板输出
- 被 export_task 消费 → 格式化结果写入导出文件
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.field_contract import parse_field_mapping, read_column
from app.store import store


class ResultFormatService(BaseModuleService):
    MODULE = "result_format"
    ENTITY = "格式模板"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["模板编号", "关联API", "输出格式", "字段映射", "嵌套展开", "分页策略", "排序字段", "模板版本"]
    REQUIRED_FIELDS = ["模板编号", "关联API", "输出格式"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用模板": "已停用"}
    NEGATIVE_ACTIONS = ["停用模板"]

    def format_results(self, api_name: str, data_rows: list[dict[str, Any]],
                       format_type: str = "json") -> Any:
        """对查询结果按模板格式化。"""
        rows = store.rows(self.MODULE)
        template = None
        for row in rows:
            if row.get("关联API") == api_name and row.get("status") == "已生效":
                template = row
                break

        if not template:
            return data_rows

        output_format = template.get("输出格式", format_type)
        field_mapping = template.get("字段映射", "")
        sort_field = template.get("排序字段", "")

        # 字段映射
        if field_mapping and data_rows:
            pairs = parse_field_mapping(field_mapping)
            mapped = []
            for row in data_rows:
                mapped_row = {}
                for orig, new in pairs:
                    mapped_row[new] = read_column(row, orig).or_default("")
                mapped.append(mapped_row if mapped_row else row)
            data_rows = mapped

        # 排序
        first_row = data_rows[0] if data_rows else {}
        if sort_field and not read_column(first_row, sort_field).is_missing:
            data_rows = sorted(data_rows, key=lambda r: str(read_column(r, sort_field).or_default("")))

        if output_format == "csv":
            return self._to_csv(data_rows)
        elif output_format == "nested":
            return {"nested": True, "total": len(data_rows), "items": data_rows}
        return data_rows

    def _to_csv(self, data_rows: list[dict[str, Any]]) -> str:
        if not data_rows:
            return ""
        keys = list(data_rows[0].keys())
        lines = [",".join(keys)]
        for row in data_rows:
            lines.append(",".join(str(read_column(row, k).or_default("")) for k in keys))
        return "\n".join(lines)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        result = super().run_action(entry_id, action)
        from app.services.data_manager import write_module_snapshot
        write_module_snapshot(self.MODULE, self.domain_snapshot())
        return result

    def domain_snapshot(self) -> dict[str, Any]:
        rows = store.rows(self.MODULE)
        format_list = []
        for row in rows:
            format_list.append({
                "id": row.get("id"),
                "template_no": row.get("模板编号"),
                "api": row.get("关联API"),
                "output_format": row.get("输出格式"),
                "field_mapping": row.get("字段映射"),
                "nesting": row.get("嵌套展开"),
                "pagination": row.get("分页策略"),
                "sort": row.get("排序字段"),
                "version": row.get("模板版本"),
                "status": row.get("status"),
            })
        return {
            "module": self.MODULE,
            "entity": self.ENTITY,
            "artifact": f"{self.ENTITY}数据集",
            "count": len(format_list),
            "templates": format_list,
            "active_count": sum(1 for r in format_list if r.get("status") == "已生效"),
        }