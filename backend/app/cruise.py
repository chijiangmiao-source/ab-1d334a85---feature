"""巡航周期长期安全占比审计（精确有理数）。

在有限命令串复核之外，审计两侧规程**无限重复同一巡航周期**时的长期安全占比：

- 巡航周期 ``c_1..c_k``（1 ≤ k ≤ 6，两侧共同声明的 ASCII 命令）被压缩为精确
  随机矩阵 ``P = M(c_1)…M(c_k)``，并保留各相位部分积 ``Q_j = M(c_1)…M(c_j)``；
- 对 ``P`` 做完整状态分解：暂态集合、**全部**闭类及每个闭类的周期与循环子类；
- 从原初始分布精确求出吸收到各闭类（及其循环子类）的权重——通过 ``P^D``
  的击中概率方程精确求解（``D`` 为可达闭类周期的最小公倍数），
  **不做固定轮数模拟，不使用浮点近似**；
- 由此给出每个命令相位 ``j = 0..k-1`` 上的**最终周期性安全概率**

  ``s(r, j) = lim_{m→∞} P(安全 | 经过 r + m·D 轮完整周期后再执行本周期的前 j 条命令)``，

  即沿子相位 ``r mod D`` 的精确周期序列；两侧按共同重复周期逐相位比较，
  差异时给出最早相位、两侧精确概率与闭类贡献权重。
"""

from __future__ import annotations

from collections import deque
from fractions import Fraction
from math import gcd
from typing import Any

from .equivalence import Procedure

# 巡航审计的规模上限：周期长度 ≤ 6、每侧状态数 ≤ 8
MAX_CYCLE_LENGTH = 6
MAX_STATES = 8


# ---------------------------------------------------------------------------
# 精确矩阵工具（全部在 fractions.Fraction 上运算）
# ---------------------------------------------------------------------------


def _identity(n: int) -> list[list[Fraction]]:
    return [[Fraction(1 if i == j else 0) for j in range(n)] for i in range(n)]


def _mat_mul(X: list[list[Fraction]], Y: list[list[Fraction]]) -> list[list[Fraction]]:
    n = len(X)
    out = [[Fraction(0)] * n for _ in range(n)]
    for i in range(n):
        row_out = out[i]
        for k in range(n):
            v = X[i][k]
            if v:
                row_y = Y[k]
                for j in range(n):
                    if row_y[j]:
                        row_out[j] += v * row_y[j]
    return out


def _mat_pow(P: list[list[Fraction]], exp: int) -> list[list[Fraction]]:
    """精确快速幂；``exp >= 0``，``P^0`` 为单位矩阵。"""
    result = _identity(len(P))
    base = P
    while exp > 0:
        if exp & 1:
            result = _mat_mul(result, base)
        base = _mat_mul(base, base)
        exp >>= 1
    return result


def _row_vec_mul(vec: list[Fraction], matrix: list[list[Fraction]]) -> list[Fraction]:
    n = len(matrix)
    out = [Fraction(0)] * n
    for i, value in enumerate(vec):
        if value == 0:
            continue
        row = matrix[i]
        for j in range(n):
            if row[j]:
                out[j] += value * row[j]
    return out


def _solve(A: list[list[Fraction]], b: list[Fraction]) -> list[Fraction]:
    """精确高斯消元求解非奇异方阵系统 ``A x = b``。"""
    n = len(A)
    aug = [list(A[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        pivot = next((r for r in range(col, n) if aug[r][col] != 0), None)
        if pivot is None:
            raise ValueError("奇异矩阵，无法精确求解")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        lead = aug[col][col]
        aug[col] = [x / lead for x in aug[col]]
        for r in range(n):
            if r != col:
                factor = aug[r][col]
                if factor:
                    aug[r] = [x - factor * y for x, y in zip(aug[r], aug[col])]
    return [aug[i][n] for i in range(n)]


# ---------------------------------------------------------------------------
# 周期矩阵的状态分解：暂态、全部闭类及其周期
# ---------------------------------------------------------------------------


def _communicating_classes(
    P: list[list[Fraction]],
) -> tuple[list[list[int]], list[int]]:
    """返回 ``(闭类列表, 暂态状态列表)``，状态编号均升序。"""
    n = len(P)
    reach = [[P[i][j] != 0 or i == j for j in range(n)] for i in range(n)]
    for k in range(n):
        for i in range(n):
            if reach[i][k]:
                row_i, row_k = reach[i], reach[k]
                for j in range(n):
                    if row_k[j]:
                        row_i[j] = True
    seen = [False] * n
    closed_classes: list[list[int]] = []
    transient: list[int] = []
    for i in range(n):
        if seen[i]:
            continue
        cls = sorted(j for j in range(n) if reach[i][j] and reach[j][i])
        for j in cls:
            seen[j] = True
        member = set(cls)
        is_closed = all(P[u][v] == 0 for u in cls for v in range(n) if v not in member)
        if is_closed:
            closed_classes.append(cls)
        else:
            transient.extend(cls)
    return closed_classes, sorted(transient)


def _period_and_cyclic_classes(
    P: list[list[Fraction]], cls: list[int]
) -> tuple[int, list[list[int]]]:
    """不可约闭类的周期与循环子类划分（类内 BFS 深度的 gcd 法）。"""
    root = cls[0]
    depth = {root: 0}
    queue: deque[int] = deque([root])
    while queue:
        u = queue.popleft()
        for v in cls:
            if P[u][v] != 0 and v not in depth:
                depth[v] = depth[u] + 1
                queue.append(v)
    period = 0
    for u in cls:
        for v in cls:
            if P[u][v] != 0:
                period = gcd(period, depth[u] + 1 - depth[v])
    period = max(period, 1)
    cycles: list[list[int]] = [[] for _ in range(period)]
    for u in cls:
        cycles[depth[u] % period].append(u)
    return period, [sorted(c) for c in cycles]


def _stationary_on_class(P: list[list[Fraction]], cls: list[int]) -> dict[int, Fraction]:
    """不可约闭类上的平稳分布：精确求解 ``μP = μ`` 且 ``Σμ = 1``。"""
    m = len(cls)
    A: list[list[Fraction]] = [[Fraction(1)] * m]
    b: list[Fraction] = [Fraction(1)]
    for jj in range(1, m):
        j = cls[jj]
        A.append(
            [
                P[cls[ii]][j] - (Fraction(1) if cls[ii] == j else Fraction(0))
                for ii in range(m)
            ]
        )
        b.append(Fraction(0))
    mu = _solve(A, b)
    return {cls[ii]: mu[ii] for ii in range(m)}


def _hitting_probabilities(
    P: list[list[Fraction]], transient: list[int], target: list[int]
) -> list[Fraction]:
    """每个状态出发最终被吸收进 ``target``（闭集）的精确概率向量。

    ``target`` 上为 1，其余闭类状态上为 0，暂态部分由
    ``(I - Q) h = b`` 精确解出（Q 为暂态-暂态子矩阵，谱半径 < 1，可解）。
    """
    n = len(P)
    out = [Fraction(0)] * n
    for j in target:
        out[j] = Fraction(1)
    if transient:
        t = len(transient)
        A = [
            [
                (Fraction(1) if a == b else Fraction(0)) - P[transient[a]][transient[b]]
                for b in range(t)
            ]
            for a in range(t)
        ]
        bvec = [sum((P[i][j] for j in target), Fraction(0)) for i in transient]
        h = _solve(A, bvec)
        for a, i in enumerate(transient):
            out[i] = h[a]
    return out


# ---------------------------------------------------------------------------
# 单侧审计
# ---------------------------------------------------------------------------


def audit(proc: Procedure, cycle: list[str]) -> dict[str, Any]:
    """单侧规程在固定巡航周期下的长期安全占比（全部精确有理数）。

    返回：整体周期 ``period``、暂态状态与暂态质量、**全部**闭类（含周期、
    循环子类、吸收权重、类内平稳安全占比），以及每个命令相位的最终周期性
    安全概率序列。
    """
    n = proc.n
    k = len(cycle)

    # 周期矩阵 P 与各相位部分积 Q_j = M(c_1)…M(c_j)（Q_0 = I）
    partials = [_identity(n)]
    for c in cycle:
        partials.append(_mat_mul(partials[-1], proc.matrices[c]))
    P = partials[k]

    closed_classes, transient = _communicating_classes(P)
    recurrent = sorted(j for cls in closed_classes for j in cls)

    # 逐闭类：周期、循环子类、平稳分布、自初始分布的吸收权重
    classes: list[dict[str, Any]] = []
    for cls in closed_classes:
        period, cycles = _period_and_cyclic_classes(P, cls)
        mu = _stationary_on_class(P, cls)
        hit = _hitting_probabilities(P, transient, cls)
        alpha = sum((proc.initial[i] * hit[i] for i in range(n)), Fraction(0))
        safe_share = sum((mu[j] for j in cls if j in proc.safe), Fraction(0))
        classes.append(
            {
                "states": cls,
                "period": period,
                "cycles": cycles,
                "mu": mu,
                "weight": alpha,
                "safeShare": safe_share,
            }
        )

    # 整体周期 D：可达闭类（吸收权重 > 0）周期的最小公倍数
    period_all = 1
    for info in classes:
        if info["weight"] > 0:
            period_all = period_all * info["period"] // gcd(period_all, info["period"])

    # P^D 下各循环子类都是非周期闭类；精确求吸收到每个循环子类的权重
    PD = _mat_pow(P, period_all)
    nu0 = [Fraction(0)] * n  # ν_0 = lim_{m→∞} π P^{mD}
    for info in classes:
        cyclic_weights: list[dict[str, Any]] = []
        if info["weight"] > 0:
            d = info["period"]
            for cyc in info["cycles"]:
                hit = _hitting_probabilities(PD, transient, cyc)
                w = sum((proc.initial[i] * hit[i] for i in range(n)), Fraction(0))
                cyclic_weights.append({"states": cyc, "weight": w})
                if w:
                    # P^D 在该循环子类上的平稳分布为 d·μ|_G
                    for j in cyc:
                        nu0[j] += w * d * info["mu"][j]
        else:
            cyclic_weights = [
                {"states": cyc, "weight": Fraction(0)} for cyc in info["cycles"]
            ]
        info["cyclic"] = cyclic_weights

    # 相位观测量 u_j = Q_j · 1_S：从各状态出发、本周期前 j 条命令后的安全概率
    safe_vec = [Fraction(1 if j in proc.safe else 0) for j in range(n)]
    phase_obs = [
        [
            sum((partials[j][y][z] * safe_vec[z] for z in range(n)), Fraction(0))
            for y in range(n)
        ]
        for j in range(k)
    ]

    # 子相位 r 的极限分布 ν_r = ν_0 P^r；相位 j 的安全概率 s(r, j) = ν_r · u_j
    phases: list[dict[str, Any]] = [{"phase": j, "safety": []} for j in range(k)]
    nu = list(nu0)
    for r in range(period_all):
        if r > 0:
            nu = _row_vec_mul(nu, P)
        for j in range(k):
            phases[j]["safety"].append(
                sum((nu[y] * phase_obs[j][y] for y in range(n)), Fraction(0))
            )

    return {
        "period": period_all,
        "transientStates": transient,
        "transientMass": sum((proc.initial[i] for i in transient), Fraction(0)),
        "closedClasses": [
            {
                "states": info["states"],
                "period": info["period"],
                "weight": info["weight"],
                "safeShare": info["safeShare"],
                "cyclic": info["cyclic"],
            }
            for info in classes
        ],
        "phases": phases,
    }


def audit_pair(a: Procedure, b: Procedure, cycle: list[str]) -> dict[str, Any]:
    """两侧按共同巡航周期逐相位比较最终周期性安全概率。"""
    report_a = audit(a, cycle)
    report_b = audit(b, cycle)
    k = len(cycle)

    # 两侧序列分别以其周期重复；在 lcm(D_A, D_B) 的子相位网格上逐相位对齐比较
    span = report_a["period"] * report_b["period"] // gcd(
        report_a["period"], report_b["period"]
    )
    first: dict[str, Any] | None = None
    for j in range(k):
        seq_a = report_a["phases"][j]["safety"]
        seq_b = report_b["phases"][j]["safety"]
        for r in range(span):
            value_a = seq_a[r % len(seq_a)]
            value_b = seq_b[r % len(seq_b)]
            if value_a != value_b:
                first = {
                    "phase": j,
                    "subPhase": r,
                    "A": value_a,
                    "B": value_b,
                    "difference": value_a - value_b,
                }
                break
        if first is not None:
            break

    return {
        "cycle": list(cycle),
        "cycleLength": k,
        "consistent": first is None,
        "firstDifference": first,
        "A": report_a,
        "B": report_b,
    }
