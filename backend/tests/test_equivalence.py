"""核心数学的单元测试：等价、最短反例、字典序、精确性。"""

from fractions import Fraction

from app.equivalence import (
    ProcedureValidationError,
    compare,
    parse_fraction,
    parse_procedure,
)


def make_proc(n, initial, safe, commands):
    """commands: dict[char] -> 稠密分数矩阵（str）。"""
    data = {
        "n": n,
        "initial": initial,
        "safe": safe,
        "commands": [
            {
                "symbol": c,
                "rows": [
                    [{"target": j, "prob": m[i][j]} for j in range(n)]
                    for i in range(n)
                ],
            }
            for c, m in commands.items()
        ],
    }
    return parse_procedure("X", data)


def test_parse_fraction():
    assert parse_fraction("2/4") == Fraction(1, 2)
    assert parse_fraction("3") == Fraction(3)
    assert parse_fraction("1/0") is None
    assert parse_fraction("0.5") is None
    assert parse_fraction("-1/2") is None
    assert parse_fraction("") is None


def test_identical_procs_equivalent():
    m = [["1/2", "1/2"], ["1/3", "2/3"]]
    a = make_proc(2, ["1", "0"], [1], {"x": m})
    b = make_proc(2, ["1", "0"], [1], {"x": m})
    assert compare(a, b)["equivalent"] is True


def test_simple_counterexample():
    # A: 命令 x 后留在 0 的概率 1；B: x 后 0->1。安全态 {1}。
    ma = [["1", "0"], ["0", "1"]]
    mb = [["0", "1"], ["0", "1"]]
    a = make_proc(2, ["1", "0"], [1], {"x": ma})
    b = make_proc(2, ["1", "0"], [1], {"x": mb})
    res = compare(a, b)
    assert res["equivalent"] is False
    assert res["word"] == ["x"]
    assert res["A"]["final"] == Fraction(0)
    assert res["B"]["final"] == Fraction(1)
    assert res["difference"] == Fraction(-1)
    # 逐步：两步（ε 与 x）
    assert len(res["A"]["steps"]) == 2


def test_shortest_and_lexicographic():
    # 差异只在第二条命令（'b'）暴露，'a' 两侧完全相同。
    ident = [["1", "0"], ["0", "1"]]
    flip = [["0", "1"], ["0", "1"]]
    a = make_proc(2, ["1", "0"], [1], {"a": ident, "b": ident})
    b = make_proc(2, ["1", "0"], [1], {"a": ident, "b": flip})
    res = compare(a, b)
    assert res["word"] == ["b"]  # 不是 "aa..." 也不是 "ab"

    # 两条命令都能在长度 1 暴露时取 ASCII 小者
    a2 = make_proc(2, ["1", "0"], [1], {"a": flip, "b": flip})
    b2 = make_proc(2, ["1", "0"], [1], {"a": ident, "b": ident})
    res2 = compare(a2, b2)
    assert res2["word"] == ["a"]


def test_difference_only_at_length_two():
    # 长度 0、1 全部相同；长度 2 的 "aa" 暴露差异：
    # A 与 B 的 a 矩阵对初始向量作用一次结果相同，
    # 但作用到可达状态之外的方向时不同。
    # 构造 3 态：初始 (1,0,0)，安全 {2}。
    # 第一步 0 -> 1（两侧相同）；第二步：
    # A: 1 -> 1，B: 1 -> 2。
    ma = [["0", "1", "0"], ["0", "1", "0"], ["0", "0", "1"]]
    mb = [["0", "1", "0"], ["0", "0", "1"], ["0", "0", "1"]]
    a = make_proc(3, ["1", "0", "0"], [2], {"a": ma})
    b = make_proc(3, ["1", "0", "0"], [2], {"a": mb})
    res = compare(a, b)
    assert res["word"] == ["a", "a"]
    assert res["wordLength"] == 2
    assert res["difference"] == Fraction(-1)
    assert len(res["A"]["steps"]) == 3


def test_counterexample_beyond_any_short_enumeration():
    # A 在第 6 步进入安全态，B 在第 7 步：长度 0..5 两侧安全概率都为 0，
    # 任何“长度 ≤5 枚举”都会误判等价；子空间判定必须给出长度 6 的反例。
    def chain(n, safe):
        m = [["0"] * n for _ in range(n)]
        for i in range(n - 1):
            m[i][i + 1] = "1"
        m[n - 1][n - 1] = "1"
        return make_proc(n, ["1"] + ["0"] * (n - 1), safe, {"a": m})

    res = compare(chain(7, [6]), chain(8, [7]))
    assert res["word"] == ["a"] * 6
    assert res["wordLength"] == 6
    assert res["difference"] == Fraction(1)


def test_exact_fractions_no_floating():
    # 经典浮点陷阱：1/3 + 1/3 + 1/3 必须精确为 1。
    m = [["1/3", "1/3", "1/3"], ["1", "0", "0"], ["0", "1", "0"]]
    a = make_proc(3, ["1", "0", "0"], [2], {"x": m})
    b = make_proc(3, ["1", "0", "0"], [2], {"x": m})
    assert compare(a, b)["equivalent"] is True


def test_validation_errors():
    def expect_error(data, needle):
        try:
            parse_procedure("A", data)
        except ProcedureValidationError as exc:
            joined = " ".join(e["msg"] for e in exc.errors)
            assert needle in joined, exc.errors
        else:
            raise AssertionError("应当校验失败")

    # 概率和不为一
    expect_error(
        {
            "n": 2,
            "initial": ["1/2", "1/3"],
            "safe": [1],
            "commands": [
                {"symbol": "x", "rows": [[{"target": 0, "prob": "1"}], [{"target": 1, "prob": "1"}]]}
            ],
        },
        "初始分布",
    )
    # 安全态缺失
    expect_error(
        {
            "n": 1,
            "initial": ["1"],
            "safe": [],
            "commands": [{"symbol": "x", "rows": [[{"target": 0, "prob": "1"}]]}],
        },
        "安全态缺失",
    )
    # 悬空状态
    expect_error(
        {
            "n": 1,
            "initial": ["1"],
            "safe": [3],
            "commands": [{"symbol": "x", "rows": [[{"target": 0, "prob": "1"}]]}],
        },
        "悬空",
    )
    # 行和不为一 + 非法分数
    expect_error(
        {
            "n": 2,
            "initial": ["1", "0"],
            "safe": [1],
            "commands": [
                {
                    "symbol": "x",
                    "rows": [
                        [{"target": 0, "prob": "1/0"}, {"target": 1, "prob": "0"}],
                        [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}],
                    ],
                }
            ],
        },
        "非法分数",
    )
    # 行和不为一
    expect_error(
        {
            "n": 2,
            "initial": ["1", "0"],
            "safe": [1],
            "commands": [
                {
                    "symbol": "x",
                    "rows": [
                        [{"target": 0, "prob": "1/2"}, {"target": 1, "prob": "1/3"}],
                        [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}],
                    ],
                }
            ],
        },
        "转出概率和必须为 1",
    )
