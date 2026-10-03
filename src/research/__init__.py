from __future__ import annotations

"""理论研究模式 (theory mode) 数据对象与运行时。

与综述模式的 ``PipelineState`` 完全隔离: 综述流程继续使用原状态与图,
理论研究使用本包内的 Pydantic 对象、版本化存储、依赖图与研究循环。
"""
