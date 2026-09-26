"""巡航周期长期安全占比审计（精确有理数，无浮点、无固定轮数模拟）。

工程师在有限命令串复核之外，可给定一个由共同 ASCII 命令构成、长度不超过
``MAX_CYCLE_LEN`` 的巡航周期 ``c₀…c_{k-1}``，审计两侧**无限重复该周期**时的
长期安全行为。

数学模型（对每侧独立进行，全部在 :class:`fractions.Fraction` 上精确计算）：

- 把一轮周期压缩为精确随机矩阵 ``P = M(c₀)…M(c_{k-1})``（精确矩阵乘积）。
- 对 ``P`` 的支撑图求强连通分量，区分**暂态**与全部**闭类**；每个闭类都是
  不可约马尔可夫链，用 BFS 层次同余求其**周期** ``d`` 与循环类划分
  ``C₀…C_{d-1}``（``P`` 把 ``C_q`` 映入 ``C_{q+1 mod d}``）。
- 第 ``r`` 轮、命令相位 ``p`` 时刻（全局相位 ``t = r·k + p``）的分布为
  ``π Pʳ H_p``，其中 ``H_p = M(c₀)…M(c_{p-1})`` 为轮内前缀。安全概率关于轮次
  ``r`` **最终周期**，周期整除 ``L = lcm(各闭类周期)``；换算到全局相位后
  周期整除 ``k·L``。
- 对周期 ``d`` 的闭类 ``C``：``Pᵈ`` 的闭类恰为各循环类 ``C_q``，且 ``Pᵈ``
  在 ``C_q`` 上非周期，平稳分布为 ``d·μ``（``μ`` 为 ``P`` 在 ``C`` 上的平稳
  分布，``μ(C_q) = 1/d``）。用精确高斯消元解吸收方程
  ``u(i) = Σ_j Pᵈ[i][j]·u(j)``（边界：``u = 1`` 于 ``C_q``、``0`` 于其它循环
  类与其它闭类）得到暂态 ``i`` 最终按轮次对齐落入 ``C_q`` 的概率 ``a_q(i)``；
  于是轮次余数 ``s`` 下``C_q`` 上的极限质量为 ``A_{(q-s) mod d}``，其中
  ``A_q = a_q(π)`` 为初始分布的吸收系数，即

  ``λ⁽ˢ⁾ := lim_{r→∞, r≡s (mod L)} πPʳ = Σ_C Σ_q A^{(C)}_{(q-s) mod d}·d·μ|_{C_q}``。

  相位 ``p`` 的长期分布即 ``λ⁽ˢ⁾·H_p``，安全概率为其在安全态上的质量和；
  每个闭类对该相位的**安全贡献**（``λ⁽ˢ⁾`` 中来自该闭类的质量经 ``H_p``
  落在安全态的部分）与**吸收权重**（``Σ_q A_q``，与相位无关的尾事件概率）
  都可单独精确给出。
- 两侧在**共同重复周期** ``k·lcm(L_A, L_B)`` 上逐全局相位比较；发现差异时
  报告最早相位、两侧精确概率与上述闭类贡献权重。

全程不使用浮点数，也不以固定轮数模拟代替极限。状态数超过 ``MAX_STATES``
的规程不在本审计适用范围内（由接口层拒绝并定位巡航输入）。
"""

from __future__ import annotations

from fractions import Fraction
from math import gcd
from typing import Any

from .equivalence import Procedure

# 巡航审计的适用范围：每侧至多 8 个状态
MAX_STATES = 8
# 巡航周期长度上限
MAX_CYCLE_LEN = 6


# ---------------------------------------------------------------------------
# 精确线性代数小工具
# ---------------------------------------------------------------------------


def _mat_mul(
    x: list[list[Fraction]], y: list[list[Fraction]], n: int
) -> list[list[Fraction]]:
    """n×n 精确矩阵乘积。"""
    out = [[Fraction(0)] * n for _ in range(n)]
    for i in range(n):
        xi = x[i]
        oi = out[i]
        for kk in range(n):
            v = xi[kk]
            if v == 0:
                continue
            yk = y[kk]
            for j in range(n):
                if yk[j]:
                    oi[j] += v * yk[j]
    return out


def _solve_linear(
    rows: list[list[Fraction]], rhs: list[list[Fraction]]
) -> list[list[Fraction]]:
    """精确求解 ``A·X = B``（A 为 n×n 可逆矩阵，B 为 n×m），高斯-若尔当消元。"""
    n = len(rows)
    m = len(rhs[0]) if rhs else 0
    aug = [list(rows[i]) + list(rhs[i]) for i in range(n)]
    for col in range(n):
        pivot = next((r for r in range(col, n) if aug[r][col] != 0), None)
        assert pivot is not None, "矩阵奇异，无法精确求解"
        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]
        inv = Fraction(1) / aug[col][col]
        aug[col] = [v * inv for v in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0:
                factor = aug[r][col]
                aug[r] = [a - factor * b for a, b in zip(aug[r], aug[col])]
    return [row[n : n + m] for row in aug]


# ---------------------------------------------------------------------------
# 周期矩阵 P 的闭类分解与周期
# ---------------------------------------------------------------------------


def _closed_classes(
    adj: list[list[int]], n: int
) -> tuple[list[int], list[list[int]]]:
    """Kosaraju 强连通分解；返回 (暂态状态表, 闭类列表)。

    闭类 = 无出边指向分量之外的强连通分量（终态强连通分量）。
    """
    radj = [[] for _ in range(n)]
    for i in range(n):
        for j in adj[i]:
            radj[j].append(i)

    order: list[int] = []
    seen = [False] * n
    for s in range(n):
        if seen[s]:
            continue
        seen[s] = True
        stack = [(s, 0)]
        while stack:
            node, idx = stack[-1]
            if idx < len(adj[node]):
                stack[-1] = (node, idx + 1)
                nxt = adj[node][idx]
                if not seen[nxt]:
                    seen[nxt] = True
                    stack.append((nxt, 0))
            else:
                order.append(node)
                stack.pop()

    comp = [-1] * n
    comps: list[list[int]] = []
    for s in reversed(order):
        if comp[s] != -1:
            continue
        cid = len(comps)
        comp[s] = cid
        members = [s]
        stack = [s]
        while stack:
            node = stack.pop()
            for nxt in radj[node]:
                if comp[nxt] == -1:
                    comp[nxt] = cid
                    members.append(nxt)
                    stack.append(nxt)
        comps.append(sorted(members))

    transient: list[int] = []
    closed: list[list[int]] = []
    for members in comps:
        member_set = set(members)
        if all(j in member_set for i in members for j in adj[i]):
            closed.append(members)
        else:
            transient.extend(members)
    return sorted(transient), closed


def _period_and_cyclic_classes(
    adj: list[list[int]], members: list[int]
) -> tuple[int, list[list[int]]]:
    """不可约闭类的周期 d 与循环类划分（BFS 层次 mod d 同余）。"""
    member_set = set(members)
    start = members[0]
    level = {start: 0}
    d = 0
    stack = [start]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in member_set:
                continue
            if v not in level:
                level[v] = level[u] + 1
                stack.append(v)
            else:
                d = gcd(d, level[u] + 1 - level[v])
    if d == 0:  # 单状态闭类必带自环，此处仅为兜底
        d = 1
    cyclic: list[list[int]] = [[] for _ in range(d)]
    for s in members:
        cyclic[level[s] % d].append(s)
    return d, [sorted(c) for c in cyclic]


def _stationary_distribution(
    matrix: list[list[Fraction]], members: list[int]
) -> dict[int, Fraction]:
    """不可约闭类上的精确平稳分布 μ（μP = μ，Σμ = 1）。"""
    m = len(members)
    idx = {s: k for k, s in enumerate(members)}
    # 方程：对前 m-1 个状态 μ(j) - Σ_i μ(i)P[i][j] = 0，外加 Σμ = 1
    rows: list[list[Fraction]] = []
    rhs: list[list[Fraction]] = []
    for j in members[:-1]:
        row = [Fraction(0)] * m
        for i in members:
            row[idx[i]] = -matrix[i][j]
        row[idx[j]] += 1
        rows.append(row)
        rhs.append([Fraction(0)])
    rows.append([Fraction(1)] * m)
    rhs.append([Fraction(1)])
    sol = _solve_linear(rows, rhs)
    return {members[k]: sol[k][0] for k in range(m)}


# ---------------------------------------------------------------------------
# 单侧巡航审计
# ---------------------------------------------------------------------------


def _audit_side(proc: Procedure, cycle: list[str]) -> dict[str, Any]:
    """对一份规程做巡航审计，返回精确有理数结果（未序列化）。"""
    n = proc.n
    k = len(cycle)

    # 一轮周期压缩为精确随机矩阵 P = M(c₀)…M(c_{k-1})
    p_mat = [[Fraction(1 if i == j else 0) for j in range(n)] for i in range(n)]
    for symbol in cycle:
        p_mat = _mat_mul(p_mat, proc.matrices[symbol], n)

    adj = [[j for j in range(n) if p_mat[i][j] != 0] for i in range(n)]
    transient, closed = _closed_classes(adj, n)
    tidx = {s: t for t, s in enumerate(transient)}
    transient_set = set(transient)
    closed_of: dict[int, int] = {}
    for ci, members in enumerate(closed):
        for s in members:
            closed_of[s] = ci

    # 每个闭类：周期、循环类、平稳分布
    class_info: list[dict[str, Any]] = []
    for members in closed:
        d, cyclic = _period_and_cyclic_classes(adj, members)
        mu = _stationary_distribution(p_mat, members)
        class_info.append(
            {"states": members, "period": d, "cyclic": cyclic, "mu": mu}
        )

    # 每个闭类 C：解 P^d 下“最终落入循环类 C_q”的精确吸收概率 a_q(i)。
    # P^d 的闭类恰为各循环类；暂态子矩阵 I - Q 可逆，方程
    #   u(i) - Σ_{j暂态} P^d[i][j]·u(j) = Σ_{j∈C_q} P^d[i][j]
    # 的解唯一；边界：u = 1 于 C_q、0 于其它循环类与其它闭类。
    p_powers = [p_mat]
    for ci, info in enumerate(class_info):
        d = info["period"]
        while len(p_powers) < d:
            p_powers.append(_mat_mul(p_powers[-1], p_mat, n))
        pd = p_powers[d - 1]
        rows = []
        rhs = []
        for i in transient:
            row = [Fraction(0)] * len(transient)
            row[tidx[i]] = Fraction(1)
            target = [Fraction(0)] * d
            for j in range(n):
                v = pd[i][j]
                if v == 0:
                    continue
                if j in transient_set:
                    row[tidx[j]] -= v
                elif closed_of[j] == ci:
                    for q, cq in enumerate(info["cyclic"]):
                        if j in cq:
                            target[q] += v
                            break
                # 其它闭类：边界 u = 0，直接丢弃
            rows.append(row)
            rhs.append(target)
        if transient:
            sol = _solve_linear(rows, rhs)
            absorb = {i: [sol[ti][q] for q in range(d)] for ti, i in enumerate(transient)}
        else:
            absorb = {}
        # 初始分布 π 的吸收系数 A_q = a_q(π)：暂态质量按吸收概率计入，
        # 初始即落在 C_q 上的质量直接计入。
        coeffs = [Fraction(0)] * d
        for i in transient:
            if proc.initial[i] == 0:
                continue
            for q in range(d):
                coeffs[q] += proc.initial[i] * absorb[i][q]
        for q, cq in enumerate(info["cyclic"]):
            for s in cq:
                if proc.initial[s]:
                    coeffs[q] += proc.initial[s]
        info["coeffs"] = coeffs
        info["weight"] = sum(coeffs, Fraction(0))

    # 最终周期（轮次）：只计入吸收权重为正的闭类的周期
    big_l = 1
    for info in class_info:
        if info["weight"] > 0:
            big_l = big_l * info["period"] // gcd(big_l, info["period"])

    # 轮内前缀矩阵 H_p = M(c₀)…M(c_{p-1})（H_0 = I）
    prefixes = [[[Fraction(1 if i == j else 0) for j in range(n)] for i in range(n)]]
    for p in range(1, k):
        prefixes.append(_mat_mul(prefixes[-1], proc.matrices[cycle[p - 1]], n))

    # 轮次余数 s 下各闭类的极限分布（定义在闭类状态上的行向量）：
    # λ⁽ˢ⁾|_{C_q} = A_{(q-s) mod d} · d · μ|_{C_q}
    # 相位 p 的长期分布为 λ⁽ˢ⁾·H_p；闭类 C 的安全贡献为其中来自 C 的部分
    # 经 H_p 落在安全态上的质量。
    grid = k * big_l  # 单侧最终周期（全局相位数）
    phase_probs: list[Fraction] = []
    phase_contrib: list[list[Fraction]] = []
    for t in range(grid):
        p = t % k
        s = (t // k) % big_l
        h_p = prefixes[p]
        contribs: list[Fraction] = []
        total = Fraction(0)
        for info in class_info:
            d = info["period"]
            # 该闭类在轮次余数 s 下的极限分布（仅定义在 info["states"] 上）
            lam: dict[int, Fraction] = {}
            for q, cq in enumerate(info["cyclic"]):
                mass = info["coeffs"][(q - s) % d] * d
                if mass == 0:
                    continue
                for j in cq:
                    lam[j] = mass * info["mu"][j]
            # 经轮内前缀 H_p 推到相位 p，落在安全态上的质量即该闭类贡献
            c = Fraction(0)
            for i, w in lam.items():
                row = h_p[i]
                for j in proc.safe:
                    if row[j]:
                        c += w * row[j]
            contribs.append(c)
            total += c
        phase_probs.append(total)
        phase_contrib.append(contribs)

    return {
        "n": n,
        "transientStates": transient,
        "transientMass": sum((proc.initial[i] for i in transient), Fraction(0)),
        "classes": [
            {
                "states": info["states"],
                "period": info["period"],
                "cyclicClasses": info["cyclic"],
                "stationary": info["mu"],
                "weight": info["weight"],
            }
            for info in class_info
        ],
        "periodRounds": big_l,
        "grid": grid,
        "phaseProbs": phase_probs,
        "phaseContrib": phase_contrib,
    }


# ---------------------------------------------------------------------------
# 双侧比较
# ---------------------------------------------------------------------------


def audit_cruise(a: Procedure, b: Procedure, cycle: list[str]) -> dict[str, Any]:
    """巡航周期审计主入口（两侧规程须已通过校验且命令集一致）。

    返回精确有理数结果；``phases`` 为共同重复周期上的逐相位比较表，
    若存在差异则 ``firstDifference`` 给出最早相位、两侧精确概率与闭类贡献。
    """
    k = len(cycle)
    side_a = _audit_side(a, cycle)
    side_b = _audit_side(b, cycle)

    common_l = side_a["periodRounds"] * side_b["periodRounds"] // gcd(
        side_a["periodRounds"], side_b["periodRounds"]
    )
    grid = k * common_l

    phases: list[dict[str, Any]] = []
    first_difference: dict[str, Any] | None = None
    for t in range(grid):
        pa = side_a["phaseProbs"][t % side_a["grid"]]
        pb = side_b["phaseProbs"][t % side_b["grid"]]
        entry = {
            "t": t,
            "round": t // k,
            "phase": t % k,
            "A": pa,
            "B": pb,
            "equal": pa == pb,
        }
        phases.append(entry)
        if pa != pb and first_difference is None:
            ta = t % side_a["grid"]
            tb = t % side_b["grid"]
            first_difference = {
                "t": t,
                "round": t // k,
                "phase": t % k,
                "A": pa,
                "B": pb,
                "difference": pa - pb,
                "contributions": {
                    side: [
                        {
                            "states": cls["states"],
                            "period": cls["period"],
                            "weight": cls["weight"],
                            "safetyContribution": sd["phaseContrib"][tx][ci],
                        }
                        for ci, cls in enumerate(sd["classes"])
                    ]
                    for side, sd, tx in (("A", side_a, ta), ("B", side_b, tb))
                },
            }

    return {
        "cycle": cycle,
        "cycleLength": k,
        "commonGrid": grid,
        "commonPeriodRounds": common_l,
        "equivalent": first_difference is None,
        "firstDifference": first_difference,
        "phases": phases,
        "A": side_a,
        "B": side_b,
    }
