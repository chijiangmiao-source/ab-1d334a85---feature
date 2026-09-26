"""精确有理数等价判定核心。

判定两份随机规程在**所有有限命令串**上的“最终进入安全态”概率是否一致：

- 不枚举长度上限内的串（不做有界近似）；
- 不使用浮点数，全部概率以 :class:`fractions.Fraction` 精确表示；
- 数学依据：构造命令矩阵块对角作用与差分向量
  ``d(w) = (pi_A M_A(w)) ⊕ (-pi_B M_B(w))``，观测向量
  ``g = (1_S_A, 1_S_B)``，则两侧安全概率差为 ``g·d(w)``。
  求包含 ``d(ε)`` 且对各命令矩阵封闭的最小线性子空间 W（精确高斯消元），
  全部有限命令串等价 ⇔ W ⊥ g。
- 反例搜索采用子空间 BFS：只扩展能使子空间维数增大的命令串前缀，
  因而首个命中的反例长度最短；同一长度内按命令 ASCII 字典序展开，
  故得到字典序最小的最短反例。
"""

from __future__ import annotations

from collections import deque
from fractions import Fraction
from typing import Any

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


class Procedure:
    """一份已通过校验的规程。

    matrices[symbol][i][j] 为命令 symbol 下状态 i → j 的精确转移概率。
    """

    __slots__ = ("n", "initial", "safe", "symbols", "matrices")

    def __init__(
        self,
        n: int,
        initial: list[Fraction],
        safe: frozenset[int],
        symbols: list[str],
        matrices: dict[str, list[list[Fraction]]],
    ) -> None:
        self.n = n
        self.initial = initial
        self.safe = safe
        self.symbols = symbols
        self.matrices = matrices


# ---------------------------------------------------------------------------
# 分数解析
# ---------------------------------------------------------------------------

_DIGITS = set("0123456789")


def parse_fraction(text: Any) -> Fraction | None:
    """解析形如 ``p/q`` 或 ``p`` 的既约（或非既约）非负分数。

    非法时返回 None（由调用方记账）。
    """
    if not isinstance(text, str):
        return None
    token = text.strip()
    if not token:
        return None
    parts = token.split("/")
    if len(parts) > 2:
        return None
    for part in parts:
        if not part or any(ch not in _DIGITS for ch in part):
            return None
    numerator = int(parts[0])
    denominator = int(parts[1]) if len(parts) == 2 else 1
    if denominator == 0:
        return None
    return Fraction(numerator, denominator)


# ---------------------------------------------------------------------------
# 校验（尽量收集全部错误，loc 为可在前端定位的字段路径）
# ---------------------------------------------------------------------------


def _err(errors: list[dict], loc: list[Any], msg: str) -> None:
    errors.append({"loc": loc, "msg": msg})


def parse_procedure(label: str, data: Any) -> Procedure:
    """把一份原始 JSON 规程校验并转换为 :class:`Procedure`。

    校验失败时抛出 :class:`ProcedureValidationError`，携带带定位信息的错误表。
    """
    errors: list[dict] = []

    if not isinstance(data, dict):
        _err(errors, [label], "规程必须是对象")
        raise ProcedureValidationError(errors)

    # --- 状态数 -----------------------------------------------------------
    n_raw = data.get("n")
    if not isinstance(n_raw, int) or isinstance(n_raw, bool) or n_raw < 1:
        _err(errors, [label, "n"], "状态数必须是不小于 1 的整数")
        n = None
    else:
        n = n_raw

    # --- 初始分布 ---------------------------------------------------------
    initial: list[Fraction] | None = None
    raw_initial = data.get("initial")
    if n is not None:
        if not isinstance(raw_initial, list) or len(raw_initial) != n:
            _err(errors, [label, "initial"], f"初始分布必须恰好包含 {n} 个分数")
        else:
            initial = []
            ok = True
            for i, cell in enumerate(raw_initial):
                value = parse_fraction(cell)
                if value is None:
                    _err(errors, [label, "initial", i], f"非法分数 {cell!r}，应形如 p/q 且分母非零")
                    ok = False
                else:
                    initial.append(value)
            if ok:
                total = sum(initial, Fraction(0))
                if total != 1:
                    _err(
                        errors,
                        [label, "initial"],
                        f"初始分布概率和必须为 1，当前为 {total}（精确值）",
                    )

    # --- 安全态 -----------------------------------------------------------
    safe: frozenset[int] | None = None
    raw_safe = data.get("safe")
    if not isinstance(raw_safe, list) or len(raw_safe) == 0:
        _err(errors, [label, "safe"], "安全态缺失：至少指定一个安全状态")
    elif n is None:
        pass
    else:
        safe_set: set[int] = set()
        valid = True
        for i, s in enumerate(raw_safe):
            if not isinstance(s, int) or isinstance(s, bool):
                _err(errors, [label, "safe", i], "状态编号必须是整数")
                valid = False
            elif not 0 <= s < n:
                _err(errors, [label, "safe", i], f"悬空状态：编号 {s} 超出 [0, {n - 1}]")
                valid = False
            else:
                safe_set.add(s)
        if valid:
            safe = frozenset(safe_set)

    # --- 命令 -------------------------------------------------------------
    symbols: list[str] = []
    matrices: dict[str, list[list[Fraction]]] = {}
    raw_commands = data.get("commands")
    if not isinstance(raw_commands, list):
        _err(errors, [label, "commands"], "commands 必须是数组")
        raw_commands = []

    seen_symbols: set[str] = set()
    for ci, cmd in enumerate(raw_commands):
        cmd_loc = [label, "commands", ci]
        if not isinstance(cmd, dict):
            _err(errors, cmd_loc, "命令必须是对象")
            continue

        symbol = cmd.get("symbol")
        if not isinstance(symbol, str) or len(symbol) != 1 or ord(symbol) >= 128:
            _err(errors, cmd_loc + ["symbol"], "命令必须是单个 ASCII 字符")
            symbol = None
        elif symbol in seen_symbols:
            _err(errors, cmd_loc + ["symbol"], f"命令 {symbol!r} 重复")
        else:
            seen_symbols.add(symbol)

        if n is None:
            continue

        rows = cmd.get("rows")
        if not isinstance(rows, list) or len(rows) != n:
            _err(errors, cmd_loc + ["rows"], f"该命令必须恰好包含 {n} 行转移概率")
            continue

        dense: list[list[Fraction]] = [[Fraction(0)] * n for _ in range(n)]
        rows_ok = True
        for r, row in enumerate(rows):
            row_loc = cmd_loc + ["rows", r]
            if not isinstance(row, list) or len(row) != n:
                _err(errors, row_loc, f"该行必须恰好包含 {n} 个目标条目")
                rows_ok = False
                continue
            row_sum = Fraction(0)
            targets_seen: set[int] = set()
            for c, entry in enumerate(row):
                cell_loc = row_loc + [c]
                if not isinstance(entry, dict):
                    _err(errors, cell_loc, "转移条目必须是 {target, prob} 对象")
                    rows_ok = False
                    continue
                target = entry.get("target")
                if not isinstance(target, int) or isinstance(target, bool):
                    _err(errors, cell_loc + ["target"], "目标状态必须是整数")
                    rows_ok = False
                    continue
                if not 0 <= target < n:
                    _err(
                        errors,
                        cell_loc + ["target"],
                        f"悬空状态：目标 {target} 超出 [0, {n - 1}]",
                    )
                    rows_ok = False
                    continue
                if target in targets_seen:
                    _err(errors, cell_loc + ["target"], f"目标状态 {target} 在同一行重复")
                    rows_ok = False
                    continue
                targets_seen.add(target)

                prob = parse_fraction(entry.get("prob"))
                if prob is None or prob < 0:
                    _err(errors, cell_loc + ["prob"], "非法分数或负概率，应形如 p/q（q>0，p≥0）")
                    rows_ok = False
                    continue
                dense[r][target] = prob
                row_sum += prob

            if rows_ok and row_sum != 1:
                _err(
                    errors,
                    row_loc,
                    f"状态 {r} 在该命令下的转出概率和必须为 1，当前为 {row_sum}（精确值）",
                )
                rows_ok = False

        if symbol is not None and rows_ok:
            symbols.append(symbol)
            matrices[symbol] = dense

    if errors:
        raise ProcedureValidationError(errors)

    assert n is not None and initial is not None and safe is not None
    return Procedure(n, initial, safe, sorted(symbols), matrices)


class ProcedureValidationError(Exception):
    def __init__(self, errors: list[dict]) -> None:
        super().__init__("规程校验失败")
        self.errors = errors


# ---------------------------------------------------------------------------
# 精确线性代数：维护一个行向量子空间的简化行阶梯基（RREF）
# ---------------------------------------------------------------------------


def _reduce(v: list[Fraction], basis: list[tuple[int, list[Fraction]]]) -> list[Fraction]:
    """用已有基消去 v 中可被张成的分量，返回残余。"""
    out = list(v)
    for pivot, row in basis:
        factor = out[pivot]
        if factor:
            for j in range(len(out)):
                out[j] -= factor * row[j]
    return out


def _add_row(v: list[Fraction], basis: list[tuple[int, list[Fraction]]]) -> bool:
    """尝试把 v 加入子空间基；返回它是否带来新的维数。

    基始终保持简化行阶梯形（RREF）：每个主元位置的值为 1，且该列在其他
    基向量中均为 0。
    """
    residual = _reduce(v, basis)
    pivot = next((j for j, x in enumerate(residual) if x != 0), None)
    if pivot is None:
        return False
    lead = residual[pivot]
    residual = [x / lead for x in residual]
    # 反向消元，保持每个主元列只在一行非零
    for i, (old_pivot, old_row) in enumerate(basis):
        factor = old_row[pivot]
        if factor:
            basis[i] = (old_pivot, [x - factor * y for x, y in zip(old_row, residual)])
    basis.append((pivot, residual))
    return True


# ---------------------------------------------------------------------------
# 块对角乘法与安全概率
# ---------------------------------------------------------------------------


def _row_mul(vec: list[Fraction], matrix: list[list[Fraction]], n: int) -> list[Fraction]:
    out = [Fraction(0)] * n
    for i, value in enumerate(vec):
        if value == 0:
            continue
        row = matrix[i]
        for j in range(n):
            if row[j]:
                out[j] += value * row[j]
    return out


def _safe_prob(dist: list[Fraction], safe: frozenset[int]) -> Fraction:
    return sum((dist[i] for i in safe), Fraction(0))


# ---------------------------------------------------------------------------
# 等价判定
# ---------------------------------------------------------------------------


def compare(a: Procedure, b: Procedure) -> dict[str, Any]:
    """比较两份规程，返回等价结论或最短字典序最小反例。"""
    if set(a.symbols) != set(b.symbols):
        missing_a = sorted(set(b.symbols) - set(a.symbols))
        missing_b = sorted(set(a.symbols) - set(b.symbols))
        raise ProcedureValidationError(
            [
                {
                    "loc": ["commands"],
                    "msg": (
                        "两份规程的命令字符集必须一致；"
                        f"A 缺少 {missing_a or '无'}，B 缺少 {missing_b or '无'}"
                    ),
                }
            ]
        )

    symbols = a.symbols  # 两边相同，已按 ASCII 排序

    # 观测向量 g = (1_S_A, 1_S_B)；与差分向量 d(w) = (p_A(w)) ⊕ (-p_B(w))
    # 的内积恰为两侧安全概率之差。
    g = [Fraction(1 if i in a.safe else 0) for i in range(a.n)] + [
        Fraction(1 if i in b.safe else 0) for i in range(b.n)
    ]
    d0 = list(a.initial) + [-x for x in b.initial]

    def dot(v: list[Fraction]) -> Fraction:
        return sum((x * y for x, y in zip(v, g)), Fraction(0))

    # 空串先判定
    if dot(d0) != 0:
        return _counterexample(a, b, [])

    basis: list[tuple[int, list[Fraction]]] = []
    _add_row(d0, basis)

    # BFS：队列元素 (差分向量, 命令串)；仅入队能增大子空间的前缀。
    queue: deque[tuple[list[Fraction], str]] = deque()
    if any(x != 0 for x in d0):
        queue.append((d0, ""))

    while queue:
        vec, word = queue.popleft()
        for c in symbols:  # ASCII 字典序展开
            nxt = (
                _row_mul(vec[: a.n], a.matrices[c], a.n)
                + _row_mul(vec[a.n :], b.matrices[c], b.n)
            )
            candidate = word + c
            if dot(nxt) != 0:
                return _counterexample(a, b, list(candidate))
            if _add_row(nxt, basis):
                queue.append((nxt, candidate))

    return {"equivalent": True}


def _counterexample(a: Procedure, b: Procedure, word: list[str]) -> dict[str, Any]:
    """回放反例命令串，给出两侧逐步分布与最终概率差。"""

    def trace(proc: Procedure) -> dict[str, Any]:
        dist = list(proc.initial)
        steps: list[dict[str, Any]] = [
            {"distribution": list(dist), "safeProb": _safe_prob(dist, proc.safe)}
        ]
        for c in word:
            dist = _row_mul(dist, proc.matrices[c], proc.n)
            steps.append({"distribution": list(dist), "safeProb": _safe_prob(dist, proc.safe)})
        return {"steps": steps, "final": steps[-1]["safeProb"]}

    trace_a = trace(a)
    trace_b = trace(b)
    return {
        "equivalent": False,
        "word": word,
        "wordLength": len(word),
        "A": trace_a,
        "B": trace_b,
        "difference": trace_a["final"] - trace_b["final"],
    }
