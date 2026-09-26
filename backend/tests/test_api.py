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
