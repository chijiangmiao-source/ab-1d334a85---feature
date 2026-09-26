"""巡航周期长期安全占比审计：核心精确数学与 API 行为。"""

from fractions import Fraction

from fastapi.testclient import TestClient

from app.cruise import MAX_CYCLE_LENGTH, MAX_STATES, audit, audit_pair
from app.equivalence import parse_procedure
from app.main import app

client = TestClient(app)


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


def _rows(mat):
    n = len(mat)
    return [[{"target": j, "prob": mat[i][j]} for j in range(n)] for i in range(n)]


def _proc_json(n, initial, safe, matrices):
    return {
        "n": n,
        "initial": initial,
        "safe": safe,
        "commands": [{"symbol": c, "rows": _rows(m)} for c, m in matrices.items()],
    }


# ---------------------------------------------------------------------------
# 核心：暂态 / 闭类 / 周期分解与最终周期性安全概率
# ---------------------------------------------------------------------------


def test_aperiodic_single_class_stationary_limit():
    # 非周期不可约链：长期安全占比 = 平稳分布安全概率 3/5
    m = [["1/2", "1/2"], ["1/3", "2/3"]]
    proc = make_proc(2, ["1", "0"], [1], {"x": m})
    report = audit(proc, ["x"])
    assert report["period"] == 1
    assert report["transientStates"] == []
    assert report["transientMass"] == Fraction(0)
    assert len(report["closedClasses"]) == 1
    cls = report["closedClasses"][0]
    assert cls["states"] == [0, 1]
    assert cls["period"] == 1
    assert cls["weight"] == Fraction(1)
    assert cls["safeShare"] == Fraction(3, 5)
    assert report["phases"] == [{"phase": 0, "safety": [Fraction(3, 5)]}]


def test_periodic_flip_chain_phase_sequence():
    # 周期 2 翻转链：安全概率沿子相位交替 0, 1（精确，非模拟）
    flip = [["0", "1"], ["1", "0"]]
    proc = make_proc(2, ["1", "0"], [1], {"x": flip})
    report = audit(proc, ["x"])
    assert report["period"] == 2
    cls = report["closedClasses"][0]
    assert cls["period"] == 2
    assert cls["cyclic"] == [
        {"states": [0], "weight": Fraction(1)},
        {"states": [1], "weight": Fraction(0)},
    ]
    assert report["phases"][0]["safety"] == [Fraction(0), Fraction(1)]


def test_transient_mass_and_closed_class_weights():
    # 暂态 0 以 1/2、1/2 流向两个吸收态；安全态为 2
    m = [["0", "1/2", "1/2"], ["0", "1", "0"], ["0", "0", "1"]]
    proc = make_proc(3, ["1", "0", "0"], [2], {"x": m})
    report = audit(proc, ["x"])
    assert report["transientStates"] == [0]
    assert report["transientMass"] == Fraction(1)
    weights = {tuple(c["states"]): c["weight"] for c in report["closedClasses"]}
    assert weights == {(1,): Fraction(1, 2), (2,): Fraction(1, 2)}
    assert sum(weights.values(), Fraction(0)) == 1
    assert report["period"] == 1
    assert report["phases"][0]["safety"] == [Fraction(1, 2)]


def test_mixed_periods_lcm_and_cyclic_weights():
    # 0 → 1(1/2) / 2(1/2)；1↔3 构成周期 2 闭类；2 吸收
    m = [
        ["0", "1/2", "1/2", "0"],
        ["0", "0", "0", "1"],
        ["0", "0", "1", "0"],
        ["0", "1", "0", "0"],
    ]
    proc = make_proc(4, ["1", "0", "0", "0"], [1], {"x": m})
    report = audit(proc, ["x"])
    assert report["period"] == 2  # lcm(2, 1)
    by_states = {tuple(c["states"]): c for c in report["closedClasses"]}
    assert by_states[(1, 3)]["period"] == 2
    assert by_states[(1, 3)]["weight"] == Fraction(1, 2)
    assert by_states[(1, 3)]["safeShare"] == Fraction(1, 2)
    # P^2 下从 0 出发 1/2 进 {3}、1/2 进 {2}；循环子类 {1} 权重为 0
    assert by_states[(1, 3)]["cyclic"] == [
        {"states": [1], "weight": Fraction(0)},
        {"states": [3], "weight": Fraction(1, 2)},
    ]
    assert by_states[(2,)]["cyclic"] == [{"states": [2], "weight": Fraction(1, 2)}]
    # 偶数子相位安全概率 0、奇数子相位 1/2
    assert report["phases"][0]["safety"] == [Fraction(0), Fraction(1, 2)]


def test_multi_command_cycle_phases():
    # 周期 "ab"：a 翻转、b 恒等 → 相位 0 序列 [0,1]，相位 1 序列 [1,0]
    flip = [["0", "1"], ["1", "0"]]
    ident = [["1", "0"], ["0", "1"]]
    proc = make_proc(2, ["1", "0"], [1], {"a": flip, "b": ident})
    report = audit(proc, ["a", "b"])
    assert report["period"] == 2
    assert report["phases"][0]["safety"] == [Fraction(0), Fraction(1)]
    assert report["phases"][1]["safety"] == [Fraction(1), Fraction(0)]


def test_audit_pair_consistent_and_weights_sum_to_one():
    m = {"x": [["1/2", "1/2"], ["1/3", "2/3"]], "y": [["1", "0"], ["0", "1"]]}
    a = make_proc(2, ["1", "0"], [1], m)
    b = make_proc(2, ["1", "0"], [1], m)
    result = audit_pair(a, b, ["x", "y"])
    assert result["consistent"] is True
    assert result["firstDifference"] is None
    assert result["cycle"] == ["x", "y"]
    assert result["cycleLength"] == 2
    for side in ("A", "B"):
        total = sum(
            (c["weight"] for c in result[side]["closedClasses"]), Fraction(0)
        )
        assert total == 1  # 闭类贡献权重完备
        for phase in result[side]["phases"]:
            for value in phase["safety"]:
                assert Fraction(0) <= value <= Fraction(1)


def test_audit_pair_first_difference_exact():
    # 两侧同为翻转链，安全态互补 → 每个相位都差 1，最早在相位 0 子相位 0
    flip = [["0", "1"], ["1", "0"]]
    a = make_proc(2, ["1", "0"], [1], {"x": flip})
    b = make_proc(2, ["1", "0"], [0], {"x": flip})
    result = audit_pair(a, b, ["x"])
    assert result["consistent"] is False
    diff = result["firstDifference"]
    assert diff["phase"] == 0
    assert diff["subPhase"] == 0
    assert diff["A"] == Fraction(0)
    assert diff["B"] == Fraction(1)
    assert diff["difference"] == Fraction(-1)


def test_audit_pair_earliest_phase_across_commands():
    # 周期 "ab"：两侧周期矩阵相同（相位 0 完全一致），
    # 差异只在相位 1（执行命令 a 之后）暴露 → 最早相位为 1
    flip = [["0", "1"], ["1", "0"]]
    ident = [["1", "0"], ["0", "1"]]
    a = make_proc(2, ["1", "0"], [1], {"a": ident, "b": flip})  # P = I·flip
    b = make_proc(2, ["1", "0"], [1], {"a": flip, "b": ident})  # P = flip·I（相同）
    result = audit_pair(a, b, ["a", "b"])
    assert result["consistent"] is False
    diff = result["firstDifference"]
    assert diff["phase"] == 1
    assert diff["subPhase"] == 0
    assert diff["A"] == Fraction(0)
    assert diff["B"] == Fraction(1)
    assert diff["difference"] == Fraction(-1)


# ---------------------------------------------------------------------------
# API：/api/cruise 精确序列化、校验定位；/api/review 行为不变
# ---------------------------------------------------------------------------


def test_api_cruise_consistent_pair_exact_fractions():
    m = {"x": [["1/2", "1/2"], ["1/3", "2/3"]]}
    body = {
        "A": _proc_json(2, ["1", "0"], [1], m),
        "B": _proc_json(2, ["1", "0"], [1], m),
        "cycle": "x",
    }
    resp = client.post("/api/cruise", json=body)
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["consistent"] is True
    assert result["cycle"] == ["x"]
    assert result["firstDifference"] is None
    phase0 = result["A"]["phases"][0]
    assert phase0["safety"][0] == {"num": 3, "den": 5, "text": "3/5"}
    cls = result["A"]["closedClasses"][0]
    assert cls["weight"] == {"num": 1, "den": 1, "text": "1"}
    assert cls["safeShare"] == {"num": 3, "den": 5, "text": "3/5"}


def test_api_cruise_difference_payload():
    flip = {"x": [["0", "1"], ["1", "0"]]}
    body = {
        "A": _proc_json(2, ["1", "0"], [1], flip),
        "B": _proc_json(2, ["1", "0"], [0], flip),
        "cycle": "x",
    }
    resp = client.post("/api/cruise", json=body)
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["consistent"] is False
    diff = result["firstDifference"]
    assert diff["phase"] == 0 and diff["subPhase"] == 0
    assert diff["A"] == {"num": 0, "den": 1, "text": "0"}
    assert diff["B"] == {"num": 1, "den": 1, "text": "1"}
    assert diff["difference"] == {"num": -1, "den": 1, "text": "-1"}
    # 闭类贡献权重随差异一并返回
    assert result["A"]["closedClasses"][0]["period"] == 2
    assert result["A"]["closedClasses"][0]["cyclic"][0]["weight"] == {
        "num": 1,
        "den": 1,
        "text": "1",
    }


def test_api_cruise_cycle_validation_errors_located():
    m = {"x": [["1", "0"], ["0", "1"]]}
    pair = lambda: {  # noqa: E731
        "A": _proc_json(2, ["1", "0"], [1], m),
        "B": _proc_json(2, ["1", "0"], [1], m),
    }

    body = pair() | {"cycle": ""}
    resp = client.post("/api/cruise", json=body)
    assert resp.status_code == 400
    assert any(e["loc"] == ["cycle"] for e in resp.json()["errors"])

    body = pair() | {"cycle": "x" * (MAX_CYCLE_LENGTH + 1)}
    resp = client.post("/api/cruise", json=body)
    assert resp.status_code == 400
    assert any(e["loc"] == ["cycle"] for e in resp.json()["errors"])

    body = pair() | {"cycle": "xq"}
    resp = client.post("/api/cruise", json=body)
    assert resp.status_code == 400
    assert any(e["loc"] == ["cycle", 1] for e in resp.json()["errors"])

    body = pair() | {"cycle": "xé"}
    resp = client.post("/api/cruise", json=body)
    assert resp.status_code == 400
    assert any(e["loc"] == ["cycle", 1] for e in resp.json()["errors"])

    body = pair()
    resp = client.post("/api/cruise", json=body)
    assert resp.status_code == 400
    assert any(e["loc"] == ["cycle"] for e in resp.json()["errors"])


def test_api_cruise_state_limit_and_procedure_errors():
    ident = {"x": [["1" if i == j else "0" for j in range(9)] for i in range(9)]}
    big = _proc_json(MAX_STATES + 1, ["1"] + ["0"] * 8, [0], ident)
    ok = _proc_json(2, ["1", "0"], [1], {"x": [["1", "0"], ["0", "1"]]})
    resp = client.post("/api/cruise", json={"A": big, "B": ok, "cycle": "x"})
    assert resp.status_code == 400
    assert any(e["loc"] == ["A", "n"] for e in resp.json()["errors"])

    # 规程本身的校验错误照常收集（与 /api/review 同一套 loc）
    bad = _proc_json(2, ["1/2", "1/3"], [1], {"x": [["1", "0"], ["0", "1"]]})
    resp = client.post("/api/cruise", json={"A": bad, "B": ok, "cycle": "x"})
    assert resp.status_code == 400
    assert any(e["loc"][:2] == ["A", "initial"] for e in resp.json()["errors"])


def test_api_review_untouched_by_cruise_addition():
    # 原有有限串复核接口响应保持不变
    m = {"x": [["1/2", "1/2"], ["1/3", "2/3"]]}
    body = {"A": _proc_json(2, ["1", "0"], [1], m), "B": _proc_json(2, ["1", "0"], [1], m)}
    resp = client.post("/api/review", json=body)
    assert resp.status_code == 200
    assert resp.json()["result"] == {"equivalent": True}
