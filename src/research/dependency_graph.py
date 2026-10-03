from __future__ import annotations

"""命题/假设依赖图 (P0)。

依赖边方向为 dependency -> dependent (如 假设A -> 引理L1 -> 定理T)。
- 禁止循环依赖 (命题不得通过自身或其后代证明自身)。
- 上游版本变化时, 沿图传播失效 (stale)。
"""

from collections import defaultdict


class CyclicDependencyError(ValueError):
    pass


class DependencyGraph:
    def __init__(self, edges: list[tuple[str, str]] | None = None):
        self._deps: dict[str, set[str]] = defaultdict(set)      # node -> its dependencies
        self._dependents: dict[str, set[str]] = defaultdict(set)  # node -> nodes depending on it
        self._nodes: set[str] = set()
        for src, dst in edges or []:
            self.add_edge(src, dst)

    # ---- 构建 ----
    def add_node(self, node: str) -> None:
        self._nodes.add(node)

    def add_edge(self, dependency: str, dependent: str) -> None:
        if dependency == dependent:
            raise CyclicDependencyError(f"自依赖不被允许: {dependency}")
        self._nodes.update({dependency, dependent})
        self._deps[dependent].add(dependency)
        self._dependents[dependency].add(dependent)
        # 立即拒绝会形成环的边 (fail fast): dependency 已依赖 dependent 时, 该边闭合环
        if self._reaches(dependency, dependent):
            self._deps[dependent].discard(dependency)
            self._dependents[dependency].discard(dependent)
            raise CyclicDependencyError(f"检测到循环依赖: {dependency} -> {dependent}")

    def set_edges(self, edges: list[tuple[str, str]]) -> None:
        self._deps = defaultdict(set)
        self._dependents = defaultdict(set)
        self._nodes = set()
        for src, dst in edges:
            self.add_edge(src, dst)

    # ---- 查询 ----
    @property
    def nodes(self) -> set[str]:
        return set(self._nodes)

    def edges(self) -> list[tuple[str, str]]:
        return [
            (dep, node)
            for node, deps in self._deps.items()
            for dep in sorted(deps)
        ]

    def dependencies_of(self, node: str) -> set[str]:
        return set(self._deps.get(node, set()))

    def dependents_of(self, node: str, transitive: bool = True) -> set[str]:
        if not transitive:
            return set(self._dependents.get(node, set()))
        seen: set[str] = set()
        stack = list(self._dependents.get(node, set()))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self._dependents.get(cur, set()))
        return seen

    def dependencies_closure(self, node: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self._deps.get(node, set()))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self._deps.get(cur, set()))
        return seen

    def _reaches(self, src: str, dst: str) -> bool:
        """src 是否可沿依赖边到达 dst (含多跳)。"""
        seen: set[str] = set()
        stack = [src]
        while stack:
            cur = stack.pop()
            if cur == dst:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self._deps.get(cur, set()))
        return False

    def find_cycle(self) -> list[str] | None:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self._nodes}
        parent: dict[str, str] = {}

        def visit(node: str) -> list[str] | None:
            color[node] = GRAY
            for dep in self._deps.get(node, set()):
                if color[dep] == GRAY:
                    # 重建环
                    cycle = [dep, node]
                    cur = node
                    while parent.get(cur) and parent[cur] != dep:
                        cur = parent[cur]
                        cycle.append(cur)
                    cycle.append(dep)
                    return list(reversed(cycle))
                if color[dep] == WHITE:
                    parent[dep] = node
                    found = visit(dep)
                    if found:
                        return found
            color[node] = BLACK
            return None

        for n in list(self._nodes):
            if color[n] == WHITE:
                found = visit(n)
                if found:
                    return found
        return None

    def assert_acyclic(self) -> None:
        cycle = self.find_cycle()
        if cycle:
            raise CyclicDependencyError("循环依赖: " + " -> ".join(cycle))

    def topological_order(self) -> list[str]:
        """依赖优先的拓扑序 (被依赖者在前)。含环时抛异常。"""
        self.assert_acyclic()
        order: list[str] = []
        visited: set[str] = set()

        def visit(n: str) -> None:
            if n in visited:
                return
            visited.add(n)
            for dep in self._deps.get(n, set()):
                visit(dep)
            order.append(n)

        for n in list(self._nodes):
            visit(n)
        return order

    def stale_closure(self, changed: str | list[str]) -> set[str]:
        """上游对象变化时, 返回所有需要标记失效 (stale) 的下游对象。"""
        changed_list = [changed] if isinstance(changed, str) else list(changed)
        stale: set[str] = set()
        for c in changed_list:
            stale |= self.dependents_of(c, transitive=True)
        stale -= set(changed_list)
        return stale
