"""带列记录的字段解析契约：数据过滤与结果格式化共用的字段读取约定。

一条"带列记录"是一个 dict 行：键是列名，值是列内容。读取某一列时只有三种状态：

- 缺列（missing）：该行根本没有提供这一列；
- 空字符串（empty）：该列明确填了 ""；
- 实际值（present）：该列有其他值（包括显式的 None）。

两个原入口共用这里的解析结果，但各自按既有规则处理三种状态：

- data_filter.apply_filters：缺列、显式 None、空字符串都按"空值"走空值处理规则；
  只有实际值才参与过滤条件匹配。
- result_format.format_results：缺列在映射输出中落为 ""；空字符串与实际值
  （含显式 None）原样进入映射列。

契约只负责"怎么读列、怎么解析列定义"，不改变任何一方的处理规则，
保证同一次交付中列含义前后一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 字段映射列定义的写法："原列:新列,原列2:新列2"
MAPPING_PAIR_SEP = ","
MAPPING_COL_SEP = ":"


@dataclass(frozen=True)
class ColumnRead:
    """一次列读取的结果：原始值 + 状态判定，供两个入口各自套用既有规则。"""

    value: Any
    is_missing: bool

    @property
    def is_empty(self) -> bool:
        """过滤口径的空值：缺列、显式 None 或空字符串。"""
        return self.value is None or self.value == ""

    def or_default(self, default: Any) -> Any:
        """格式化口径的取值：缺列时退回默认值，其余情况（含空字符串/None）原样返回。"""
        return self.value if not self.is_missing else default


def read_column(row: dict[str, Any], column: str) -> ColumnRead:
    """按契约读取一列：把"缺列"与"明确填空串/空值"区分开。"""
    if column in row:
        return ColumnRead(row[column], is_missing=False)
    return ColumnRead(None, is_missing=True)


def parse_field_mapping(mapping: str) -> list[tuple[str, str]]:
    """解析"原列:新列,..."形式的列定义，保持原有切分、过滤与去空白规则。

    不含冒号的片段被跳过；含多个冒号的片段与原来一样抛 ValueError。
    """
    pairs: list[tuple[str, str]] = []
    for pair in mapping.split(MAPPING_PAIR_SEP):
        if MAPPING_COL_SEP not in pair:
            continue
        orig, new = pair.split(MAPPING_COL_SEP)
        pairs.append((orig.strip(), new.strip()))
    return pairs
