"""巡航周期长期安全占比审计的核心数学测试（精确有理数）。"""

from fractions import Fraction

from app.cruise import audit_cruise
from app.equivalence import parse_procedure


def make_proc(n, initial, safe, commands):
    """commands: dict[char] -> 稠密分数矩阵（str）。"""
    return parse_procedure(
        "X",
        {
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
        },
    )


def test_period_two_swap_alternates():
    # 周期 2 闭类：安全概率在 1、0 之间交替，绝不取平均
    swap = [["0", "1"], ["1", "0"]]
    a = make_proc(2, ["1", "0"], [0], {"x": swap})
    res = audit_cruise(a, a, ["x"])
    side = res["A"]
    assert side["classes"][0]["period"] == 2
    assert side["classes"][0]["cyclicClasses"] == [[0], [1]]
    assert side["periodRounds"] == 2
    assert side["phaseProbs"] == [Fraction(1), Fraction(0)]
    assert res["equivalent"] is True
    assert res["commonGrid"] == 2


def test_transient_mass_and_absorption_weights():
    # 0 暂态：1/2 落入 {1}、1/2 落入 {2}；安全态 {2}
    m = [["0", "1/2", "1/2"], ["0", "1", "0"], ["0", "0", "1"]]
    a = make_proc(3, ["1", "0", "0"], [2], {"x": m})
    res = audit_cruise(a, a, ["x"])
    side = res["A"]
    assert side["transientStates"] == [0]
    assert side["transientMass"] == Fraction(1)
    weights = {tuple(c["states"]): c["weight"] for c in side["classes"]}
    assert weights == {(1,): Fraction(1, 2), (2,): Fraction(1, 2)}
    assert sum(weights.values()) == 1
    assert side["phaseProbs"] == [Fraction(1, 2)]


def test_mixed_periods_lcm():
    # 闭类 {0,1} 周期 2、闭类 {2} 周期 1，暂态 3 各半进入；安全态 {0,2}
    m = [
        ["0", "1", "0", "0"],
        ["1", "0", "0", "0"],
        ["0", "0", "1", "0"],
        ["1/2", "0", "1/2", "0"],
    ]
    a = make_proc(4, ["0", "0", "0", "1"], [0, 2], {"x": m})
    res = audit_cruise(a, a, ["x"])
    side = res["A"]
    assert side["periodRounds"] == 2
    assert side["phaseProbs"] == [Fraction(1, 2), Fraction(1)]
    weights = {tuple(c["states"]): c["weight"] for c in side["classes"]}
    assert weights == {(0, 1): Fraction(1, 2), (2,): Fraction(1, 2)}


def test_phase_prefix_matters_for_len2_cycle():
    # 轮内前缀不可交换：x 交换、y 混合；相位 0 → 1/3，相位 1 → 2/3
    mx = [["0", "1"], ["1", "0"]]
    my = [["1/2", "1/2"], ["0", "1"]]
    a = make_proc(2, ["1", "0"], [0], {"x": mx, "y": my})
    res = audit_cruise(a, a, ["x", "y"])
    assert res["A"]["phaseProbs"] == [Fraction(1, 3), Fraction(2, 3)]
    assert res["cycleLength"] == 2
    assert res["commonGrid"] == 2


def test_first_difference_earliest_phase_and_contributions():
    # A：周期 2 交换（安全 {0}），B：0→1 后吸收（安全 {0}）
    swap = [["0", "1"], ["1", "0"]]
    stay = [["0", "1"], ["0", "1"]]
    a = make_proc(2, ["1", "0"], [0], {"x": swap})
    b = make_proc(2, ["1", "0"], [0], {"x": stay})
    res = audit_cruise(a, b, ["x"])
    assert res["equivalent"] is False
    fd = res["firstDifference"]
    assert fd["t"] == 0 and fd["round"] == 0 and fd["phase"] == 0
    assert fd["A"] == Fraction(1) and fd["B"] == Fraction(0)
    assert fd["difference"] == Fraction(1)
    # 闭类贡献权重：A 全部质量在周期 2 闭类，B 全部在闭类 {1}
    ca = fd["contributions"]["A"]
    cb = fd["contributions"]["B"]
    assert len(ca) == 1 and ca[0]["period"] == 2 and ca[0]["weight"] == 1
    assert ca[0]["safetyContribution"] == Fraction(1)
    assert sum(c["safetyContribution"] for c in ca) == fd["A"]
    assert sum(c["safetyContribution"] for c in cb) == fd["B"]
    # 差异在相位 1 消失（A 在该相位也为 0）
    assert res["phases"][1]["equal"] is True


def test_common_grid_compares_across_different_periods():
    # A 周期 2（安全 {0}），B 周期 3（安全 {0}）：共同周期 6 相位
    ma = [["0", "1"], ["1", "0"]]
    mb = [["0", "1", "0"], ["0", "0", "1"], ["1", "0", "0"]]
    a = make_proc(2, ["1", "0"], [0], {"x": ma})
    b = make_proc(3, ["1", "0", "0"], [0], {"x": mb})
    res = audit_cruise(a, b, ["x"])
    assert res["commonPeriodRounds"] == 6
    assert res["commonGrid"] == 6
    assert res["A"]["phaseProbs"] == [Fraction(1), Fraction(0)]
    assert res["B"]["phaseProbs"] == [Fraction(1), Fraction(0), Fraction(0)]
    # A 在偶数相位为 1，B 在 3 的倍数相位为 1：t=0、1 一致，t=2 最早分歧
    assert res["equivalent"] is False
    fd = res["firstDifference"]
    assert fd["t"] == 2
    assert fd["A"] == Fraction(1) and fd["B"] == Fraction(0)
    # 逐相位表与最早差异一致
    ts = [p["t"] for p in res["phases"] if not p["equal"]]
    assert min(ts) == fd["t"]
    assert [p["equal"] for p in res["phases"]] == [True, True, False, False, False, True]

    # B 改安全态为 {1}（t≡1 mod 3 时为 1）：t=0 即分歧
    b2 = make_proc(3, ["1", "0", "0"], [1], {"x": mb})
    res2 = audit_cruise(a, b2, ["x"])
    assert res2["equivalent"] is False
    assert res2["firstDifference"]["t"] == 0


def test_cyclic_class_stationary_mass_exact():
    # 1/3 型分数：每个循环类质量恰为 1/d（精确）
    m = [
        ["0", "1/3", "2/3"],
        ["1", "0", "0"],
        ["1", "0", "0"],
    ]
    a = make_proc(3, ["1", "0", "0"], [1], {"x": m})
    res = audit_cruise(a, a, ["x"])
    cls = res["A"]["classes"][0]
    assert cls["period"] == 2
    for cq in cls["cyclicClasses"]:
        assert sum(cls["stationary"][j] for j in cq) == Fraction(1, 2)
