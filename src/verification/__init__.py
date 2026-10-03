from __future__ import annotations

"""受限数学验证执行器 (P1)。

分层工具 (对应《下一步执行方案》第 7.2 节):
- SymPy: 恒等变换、不等式、等式条件 (局部/表达式级)。
- Z3: 逻辑与约束可满足性 (精确编码的片段)。
- Lean: 形式化内核检查 (P4 增强, 未安装时返回 unsupported)。

任一工具返回 unknown / timeout / unsupported 时保持未决, 不得映射为通过或数学反驳。
"""
