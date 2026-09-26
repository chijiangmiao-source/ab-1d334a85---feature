"""API 层测试：等价、反例回放、错误定位与精确分数序列化。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _rows(mat):
    n = len(mat)
    return [[{"target": j, "prob": mat[i][j]} for j in range(n)] for i in range(n)]


def _proc(n, initial, safe, matrices):
    return {
        "n": n,
        "initial": initial,
        "safe": safe,
        "commands": [{"symbol": c, "rows": _rows(m)} for c, m in matrices.items()],
    }


def test_healthz_and_index():
    assert client.get("/healthz").json() == {"status": "ok"}
    page = client.get("/")
    assert page.status_code == 200
    assert "声学" in page.text


def test_equivalent_pair():
    m = {"x": [["1/2", "1/2"], ["1/3", "2/3"]]}
    body = {"A": _proc(2, ["1", "0"], [1], m), "B": _proc(2, ["1", "0"], [1], m)}
    resp = client.post("/api/review", json=body)
    assert resp.status_code == 200
    assert resp.json()["result"] == {"equivalent": True}


def test_counterexample_payload_is_exact():
    ma = [["0", "1", "0"], ["0", "1", "0"], ["0", "0", "1"]]
    mb = [["0", "1", "0"], ["0", "0", "1"], ["0", "0", "1"]]
    body = {
        "A": _proc(3, ["1", "0", "0"], [2], {"a": ma}),
        "B": _proc(3, ["1", "0", "0"], [2], {"a": mb}),
    }
    resp = client.post("/api/review", json=body)
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["word"] == ["a", "a"]
    assert result["difference"] == {"num": -1, "den": 1, "text": "-1"}
    # 逐步分布的每个概率都是精确分数对象
    for side in ("A", "B"):
        assert len(result[side]["steps"]) == 3
        for step in result[side]["steps"]:
            for f in step["distribution"]:
                assert set(f) == {"num", "den", "text"}
                assert isinstance(f["num"], int) and isinstance(f["den"], int) and f["den"] > 0


def test_errors_are_localized_and_400():
    bad = _proc(2, ["1", "0"], [1], {"x": [["1", "0"], ["0", "1"]]})
    bad["initial"] = ["1/2", "1/3"]  # 和 5/6
    bad["safe"] = []
    bad["commands"][0]["rows"][0][0]["prob"] = "1/0"
    resp = client.post("/api/review", json={"A": bad, "B": bad})
    assert resp.status_code == 400
    locs = [tuple(e["loc"]) for e in resp.json()["errors"]]
    assert any(loc[:2] == ("A", "initial") for loc in locs)
    assert any(loc[:2] == ("A", "safe") for loc in locs)
    assert any(loc and loc[-1] == "prob" for loc in locs)


def test_dangling_state_error():
    bad = _proc(2, ["1", "0"], [1], {"x": [["1", "0"], ["0", "1"]]})
    bad["safe"] = [5]
    resp = client.post("/api/review", json={"A": bad, "B": bad})
    assert resp.status_code == 400
    assert any("悬空" in e["msg"] for e in resp.json()["errors"])


def test_malformed_json_body():
    resp = client.post("/api/review", content=b"{not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# 巡航周期长期安全占比审计
# ---------------------------------------------------------------------------


def test_cruise_equivalent_pair():
    m = {"x": [["0", "1"], ["1", "0"]]}
    body = {
        "A": _proc(2, ["1", "0"], [0], m),
        "B": _proc(2, ["1", "0"], [0], m),
        "cycle": "x",
    }
    resp = client.post("/api/cruise", json=body)
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["equivalent"] is True
    assert result["cycle"] == "x"
    assert result["firstDifference"] is None
    # 周期 2 闭类：两个全局相位上安全概率交替 1、0（精确分数）
    assert [p["A"]["text"] for p in result["phases"]] == ["1", "0"]
    cls = result["A"]["classes"][0]
    assert cls["period"] == 2 and cls["cyclicClasses"] == [[0], [1]]
    assert result["A"]["transientStates"] == []


def test_cruise_first_difference_payload_is_exact():
    body = {
        "A": _proc(2, ["1", "0"], [0], {"x": [["0", "1"], ["1", "0"]]}),
        "B": _proc(2, ["1", "0"], [0], {"x": [["0", "1"], ["0", "1"]]}),
        "cycle": "x",
    }
    resp = client.post("/api/cruise", json=body)
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["equivalent"] is False
    fd = result["firstDifference"]
    assert (fd["t"], fd["round"], fd["phase"]) == (0, 0, 0)
    assert fd["A"] == {"num": 1, "den": 1, "text": "1"}
    assert fd["B"] == {"num": 0, "den": 1, "text": "0"}
    assert fd["difference"] == {"num": 1, "den": 1, "text": "1"}
    # 闭类贡献权重均为精确分数对象，且贡献之和恰为该侧概率
    from fractions import Fraction

    for side in ("A", "B"):
        acc = Fraction(0)
        for c in fd["contributions"][side]:
            assert set(c["weight"]) == {"num", "den", "text"}
            assert set(c["safetyContribution"]) == {"num", "den", "text"}
            acc += Fraction(c["safetyContribution"]["num"], c["safetyContribution"]["den"])
        assert acc == Fraction(fd[side]["num"], fd[side]["den"])


def test_cruise_cycle_validation_locates_input():
    m = {"x": [["1", "0"], ["0", "1"]]}
    pair = {"A": _proc(2, ["1", "0"], [0], m), "B": _proc(2, ["1", "0"], [0], m)}

    resp = client.post("/api/cruise", json={**pair, "cycle": ""})
    assert resp.status_code == 400
    assert any(e["loc"] == ["cycle"] for e in resp.json()["errors"])

    resp = client.post("/api/cruise", json={**pair, "cycle": "xz"})
    assert resp.status_code == 400
    locs = [e["loc"] for e in resp.json()["errors"]]
    assert ["cycle", 1] in locs and ["cycle"] in locs

    resp = client.post("/api/cruise", json={**pair, "cycle": "xxxxxxx"})
    assert resp.status_code == 400
    assert any(e["loc"] == ["cycle"] for e in resp.json()["errors"])

    resp = client.post("/api/cruise", json={**pair})
    assert resp.status_code == 400
    assert any(e["loc"] == ["cycle"] for e in resp.json()["errors"])


def test_cruise_state_limit_locates_input_but_review_unaffected():
    ident = [["1" if i == j else "0" for j in range(9)] for i in range(9)]
    big = _proc(9, ["1"] + ["0"] * 8, [0], {"x": ident})
    resp = client.post("/api/cruise", json={"A": big, "B": big, "cycle": "x"})
    assert resp.status_code == 400
    assert any(e["loc"] == ["cycle"] for e in resp.json()["errors"])
    # 普通有限串复核不受 8 态上限影响
    resp = client.post("/api/review", json={"A": big, "B": big})
    assert resp.status_code == 200
    assert resp.json()["result"] == {"equivalent": True}


def test_cruise_procedure_errors_keep_original_locs():
    good = _proc(2, ["1", "0"], [0], {"x": [["1", "0"], ["0", "1"]]})
    bad = _proc(2, ["1/2", "1/3"], [0], {"x": [["1", "0"], ["0", "1"]]})
    resp = client.post("/api/cruise", json={"A": bad, "B": good, "cycle": "x"})
    assert resp.status_code == 400
    assert any(e["loc"][:2] == ["A", "initial"] for e in resp.json()["errors"])
