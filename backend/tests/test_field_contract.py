"""字段解析契约重构的回归与场景测试。

覆盖：
1. 共用契约 read_column / parse_field_mapping 的三种列状态（缺列、空串、实际值）。
2. 两个原入口各自既有规则不变：
   - data_filter.apply_filters：缺列/空串/显式 None 按空值处理，实际值参与条件匹配；
   - result_format.format_results：缺列落 ""，空串与显式 None 原样进映射列。
3. 场景：一批含缺值和空值的输入先取一次结果，再在处理中变更字段映射（版本冲突），
   核查已支持用例前后保持、实际参加格式化的行（只有通过过滤的行），
   以及格式化后的值能追到通过筛选的原始行。
"""

import os
import sys
import tempfile

# 设置测试环境
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 设置临时DATA_ROOT
temp_data = tempfile.mkdtemp(prefix='field_contract_test_')
os.environ['DATA_ROOT'] = temp_data


def _reset_store():
    from app.seed import SEED_ROWS
    from app.store import store as s
    s._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    return s


def _add_filter_rule(store, api, field, condition="", null_handling="", version="v1"):
    store.rows("data_filter").append({
        "id": 900 + len(store.rows("data_filter")),
        "status": "已生效",
        "pending": False,
        "abnormal": False,
        "规则编号": "DATA-TEST",
        "关联API": api,
        "过滤字段": field,
        "过滤条件": condition,
        "模糊策略": "",
        "精度约束": "",
        "空值处理": null_handling,
        "规则版本": version,
    })


def _add_format_template(store, api, mapping, version="v1", output_format="json", sort=""):
    store.rows("result_format").append({
        "id": 900 + len(store.rows("result_format")),
        "status": "已生效",
        "pending": False,
        "abnormal": False,
        "模板编号": "RESU-TEST",
        "关联API": api,
        "输出格式": output_format,
        "字段映射": mapping,
        "嵌套展开": "",
        "分页策略": "",
        "排序字段": sort,
        "模板版本": version,
    })


class TestFieldContract:
    """共用字段解析契约本身的约定"""

    def setup_method(self):
        _reset_store()

    def test_read_column_distinguishes_missing_empty_value(self):
        from app.services.field_contract import read_column

        row = {"a": "x", "b": "", "c": None}

        missing = read_column(row, "zzz")
        assert missing.is_missing is True
        assert missing.is_empty is True
        assert missing.or_default("") == ""

        empty = read_column(row, "b")
        assert empty.is_missing is False
        assert empty.is_empty is True
        assert empty.or_default("") == ""

        explicit_none = read_column(row, "c")
        assert explicit_none.is_missing is False
        assert explicit_none.is_empty is True
        # 格式化口径：显式 None 是"列存在"，不落默认值
        assert explicit_none.or_default("") is None

        value = read_column(row, "a")
        assert value.is_missing is False
        assert value.is_empty is False
        assert value.or_default("") == "x"

    def test_parse_field_mapping_rules(self):
        from app.services.field_contract import parse_field_mapping

        assert parse_field_mapping("a:x, b : y ,c:z") == [("a", "x"), ("b", "y"), ("c", "z")]
        # 无冒号片段跳过
        assert parse_field_mapping("a:x,垃圾片段,b:y") == [("a", "x"), ("b", "y")]
        assert parse_field_mapping("") == []

    def test_parse_field_mapping_malformed_raises(self):
        from app.services.field_contract import parse_field_mapping

        try:
            parse_field_mapping("a:b:c")
        except ValueError:
            pass
        else:
            raise AssertionError("多冒号片段应与原实现一样抛 ValueError")


class TestEntryRulesPreserved:
    """共用解析后，两个原入口对空串/缺列/实际值仍按各自既有规则处理"""

    def setup_method(self):
        self.store = _reset_store()

    def test_filter_exclude_nulls_drops_missing_and_empty(self):
        from app.services.data_filter import DataFilterService

        _add_filter_rule(self.store, "订单API", "phone", null_handling="排除空值")
        svc = DataFilterService()
        rows = [
            {"id": 1, "phone": "138"},
            {"id": 2, "phone": ""},          # 明确填空串
            {"id": 3},                        # 未提供该列
            {"id": 4, "phone": None},         # 显式 None
        ]
        result = svc.apply_filters("订单API", rows)
        assert [r["id"] for r in result] == [1]

    def test_filter_keep_nulls_skips_condition_for_empty(self):
        from app.services.data_filter import DataFilterService

        _add_filter_rule(self.store, "订单API", "phone", condition="138", null_handling="保留空值")
        svc = DataFilterService()
        rows = [
            {"id": 1, "phone": "13800"},
            {"id": 2, "phone": ""},
            {"id": 3},
            {"id": 4, "phone": "139"},
        ]
        result = svc.apply_filters("订单API", rows)
        # 空值行保留且不参与条件匹配；实际值按条件（忽略大小写包含）过滤
        assert [r["id"] for r in result] == [1, 2, 3]

    def test_filter_condition_casefold_on_real_values(self):
        from app.services.data_filter import DataFilterService

        _add_filter_rule(self.store, "订单API", "name", condition="ABC")
        svc = DataFilterService()
        rows = [{"id": 1, "name": "xxAbCxx"}, {"id": 2, "name": "zzz"}, {"id": 3}]
        result = svc.apply_filters("订单API", rows)
        # 无空值处理配置时，缺列行不参与条件匹配而保留
        assert [r["id"] for r in result] == [1, 3]

    def test_format_mapping_missing_empty_none_real(self):
        from app.services.result_format import ResultFormatService

        _add_format_template(self.store, "订单API", "a:x,b:y,c:z,d:w")
        svc = ResultFormatService()
        rows = [{"a": "1", "b": "", "c": None}]  # d 缺列
        result = svc.format_results("订单API", rows)
        assert result == [{"x": "1", "y": "", "z": None, "w": ""}]

    def test_format_malformed_mapping_behaviour_unchanged(self):
        from app.services.result_format import ResultFormatService

        _add_format_template(self.store, "订单API", "a:b:c")
        svc = ResultFormatService()
        # 有行时与原实现一样抛 ValueError
        try:
            svc.format_results("订单API", [{"a": 1}])
        except ValueError:
            pass
        else:
            raise AssertionError("多冒号映射应与原实现一样抛 ValueError")
        # 空行时不触发解析，原样返回
        assert svc.format_results("订单API", []) == []

    def test_format_csv_and_sort_via_contract(self):
        from app.services.result_format import ResultFormatService

        _add_format_template(self.store, "订单API", "", output_format="csv", sort="k")
        svc = ResultFormatService()
        rows = [{"k": "b", "v": 2}, {"k": "a", "v": 1}, {"k": "c"}]  # 末行缺 v 列
        csv_text = svc.format_results("订单API", rows)
        # 排序后首行决定 CSV 列；缺列的行在 CSV 中落空串
        assert csv_text == "k,v\na,1\nb,2\nc,"


class TestFilterThenFormatWithMappingChange:
    """场景：含缺值/空值的一批输入，先取一次结果，再在处理时变更映射。

    核查点：
    - 只有已通过过滤的行被交到格式化（格式化输入 == 过滤输出）；
    - 同一次交付中列含义一致（v1 结果只用 v1 列，v2 结果只用 v2 列）；
    - 映射变更不影响已支持的过滤用例（过滤输出前后一致）；
    - 格式化后的值能追到通过筛选的原始行。
    """

    API = "订单API"
    BATCH = [
        {"id": 1, "name": "张三", "phone": "13800138000"},
        {"id": 2, "name": "李四", "phone": ""},                    # 明确填空串 → 被过滤
        {"id": 3, "name": "王五"},                                  # 未提供该列 → 被过滤
        {"id": 4, "name": "赵六", "phone": "13700137000", "email": "zl@x.com"},
    ]

    def setup_method(self):
        self.store = _reset_store()
        _add_filter_rule(self.store, self.API, "phone", null_handling="排除空值", version="v1")
        _add_format_template(self.store, self.API, "name:姓名,phone:电话,email:邮箱", version="v1")

        from app.services.data_filter import DataFilterService
        from app.services.result_format import ResultFormatService

        self.filter_svc = DataFilterService()
        self.format_svc = ResultFormatService()

        # 监听实际进入格式化的行（这一批的格式化输入）
        self.format_inputs = []
        original = self.format_svc.format_results

        def spy(api_name, data_rows, format_type="json"):
            self.format_inputs.append([dict(r) for r in data_rows])
            return original(api_name, data_rows, format_type)

        self.format_svc.format_results = spy

    def _run_pipeline(self):
        filtered = self.filter_svc.apply_filters(self.API, list(self.BATCH))
        return filtered, self.format_svc.format_results(self.API, filtered)

    def test_mapping_change_mid_processing(self):
        # 第一次：按 v1 映射取结果
        filtered_v1, result_v1 = self._run_pipeline()

        # 只有已通过过滤的行交到格式化：phone 为空串/缺列的行被排除
        assert [r["id"] for r in filtered_v1] == [1, 4]
        assert self.format_inputs[-1] == [dict(r) for r in filtered_v1]

        # v1 交付的列含义一致：全部使用 v1 列名
        assert result_v1 == [
            {"姓名": "张三", "电话": "13800138000", "邮箱": ""},   # email 缺列 → ""
            {"姓名": "赵六", "电话": "13700137000", "邮箱": "zl@x.com"},
        ]

        # 格式化后的值追到通过筛选的原始行
        for formatted, source in zip(result_v1, filtered_v1):
            assert formatted["姓名"] == source["name"]
            assert formatted["电话"] == source["phone"]
            assert formatted["邮箱"] == source.get("email", "")

        # 处理中变更映射：模板升到 v2（列名换新、去掉 email 列）
        template = next(r for r in self.store.rows("result_format")
                        if r.get("关联API") == self.API and r.get("status") == "已生效")
        template["字段映射"] = "name:客户名,phone:联系电话"
        template["模板版本"] = "v2"

        # 第二次：同一批输入按 v2 映射取结果
        filtered_v2, result_v2 = self._run_pipeline()

        # 已支持的过滤用例前后保持：映射变更不改变过滤输出
        assert filtered_v2 == filtered_v1
        # 这一批的格式化输入仍然只有已通过过滤的行
        assert self.format_inputs[-1] == [dict(r) for r in filtered_v2]

        # v2 交付的列含义一致：全部使用 v2 列名，不混 v1 列
        assert result_v2 == [
            {"客户名": "张三", "联系电话": "13800138000"},
            {"客户名": "赵六", "联系电话": "13700137000"},
        ]
        for formatted, source in zip(result_v2, filtered_v2):
            assert formatted["客户名"] == source["name"]
            assert formatted["联系电话"] == source["phone"]

        # 两次交付互不染指：v1 结果里没有 v2 列，反之亦然
        assert all("姓名" not in row for row in result_v2)
        assert all("客户名" not in row for row in result_v1)
