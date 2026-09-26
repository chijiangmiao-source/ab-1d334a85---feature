#!/usr/bin/env python3
"""Compose verify 服务入口。

验收三件事，全部通过才以退出码 0 结束：

1. 最短概率反例 + 巡航周期审计：直接在镜像内调用精确有理数核心，断言
   - 差异只在长度 2 暴露时给出最短反例 "aa"；
   - 同长度按 ASCII 字典序取最小；
   - 等价样例判定为等价；
   - 概率和不为一 / 非法分数被拒绝；
   - 巡航审计：暂态质量、全部闭类及其周期、循环子类权重、
     各命令相位最终周期性安全概率均为精确分数，差异时给出最早相位。
2. 检查构建：镜像内应用代码、静态页面与 uvicorn 均就位。
3. API 冒烟：对运行中的服务打 /healthz、/api/review（等价、反例、非法输入）
   与 /api/cruise（一致、差异、空周期、未声明命令、超长周期、状态数超限），
   校验状态码、定位信息与精确分数字段，全程无浮点数。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/app")

from fractions import Fraction  # noqa: E402

from app.equivalence import (  # noqa: E402
    ProcedureValidationError,
    compare,
    parse_procedure,
)
from app.cruise import audit, audit_pair  # noqa: E402

TARGET = os.environ.get("TARGET_URL", "http://acoustic-review:8000")

failures: list[str] = []


def _first_existing(*paths: str) -> str | None:
    for path in paths:
        if os.path.isfile(path):
            return path
    return None


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        failures.append(name)


# ---------------------------------------------------------------------------
# 1. 最短概率反例（核心，精确有理数）
# ---------------------------------------------------------------------------


def proc(n, initial, safe, commands):
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


# 长度 0/1 全同、长度 2 的 "aa" 才暴露
MA = [["0", "1", "0"], ["0", "1", "0"], ["0", "0", "1"]]
MB = [["0", "1", "0"], ["0", "0", "1"], ["0", "0", "1"]]
res = compare(proc(3, ["1", "0", "0"], [2], {"a": MA}), proc(3, ["1", "0", "0"], [2], {"a": MB}))
check("最短反例: word == ['a','a']", res["word"] == ["a", "a"], str(res.get("word")))
check("最短反例: 长度为 2", res["wordLength"] == 2)
check("最短反例: 精确概率差 -1", res["difference"] == Fraction(-1), str(res["difference"]))
check(
    "最短反例: 逐步回放共 3 个分布",
    len(res["A"]["steps"]) == 3 and len(res["B"]["steps"]) == 3,
)

# 同长度 ASCII 字典序最小：a 与 b 都在长度 1 暴露时必须取 'a'
IDENT = [["1", "0"], ["0", "1"]]
FLIP = [["0", "1"], ["0", "1"]]
res2 = compare(
    proc(2, ["1", "0"], [1], {"a": FLIP, "b": FLIP}),
    proc(2, ["1", "0"], [1], {"a": IDENT, "b": IDENT}),
)
check("字典序: 同长度取 ASCII 最小 'a'", res2["word"] == ["a"], str(res2.get("word")))

# 等价样例（1/3 精确求和，避免浮点漂移）
M = [["1/3", "1/3", "1/3"], ["1", "0", "0"], ["0", "1", "0"]]
check(
    "等价样例: 精确判定等价",
    compare(proc(3, ["1", "0", "0"], [2], {"x": M}), proc(3, ["1", "0", "0"], [2], {"x": M}))[
        "equivalent"
    ]
    is True,
)

# 非法输入在核心层即被拒绝
try:
    parse_procedure(
        "A",
        {
            "n": 2,
            "initial": ["1/2", "1/3"],  # 和不为一
            "safe": [1],
            "commands": [
                {"symbol": "x", "rows": [[{"target": 0, "prob": "1"}], [{"target": 1, "prob": "1"}]]}
            ],
        },
    )
    check("核心校验: 初始分布和不为一被拒", False, "未抛错")
except ProcedureValidationError:
    check("核心校验: 初始分布和不为一被拒", True)

# ---------------------------------------------------------------------------
# 1b. 巡航周期审计（核心，精确有理数：暂态 / 闭类 / 周期 / 相位序列）
# ---------------------------------------------------------------------------

# 周期 2 翻转链：两侧安全态互补 → 最早相位 0 子相位 0 差异，精确概率 0 对 1
FLIP2 = [["0", "1"], ["1", "0"]]
cruise_res = audit_pair(
    proc(2, ["1", "0"], [1], {"x": FLIP2}),
    proc(2, ["1", "0"], [0], {"x": FLIP2}),
    ["x"],
)
check("巡航核心: 周期链判定不一致", cruise_res["consistent"] is False)
diff = cruise_res["firstDifference"]
check(
    "巡航核心: 最早差异相位/子相位 0/0",
    diff["phase"] == 0 and diff["subPhase"] == 0,
    str(diff),
)
check(
    "巡航核心: 两侧精确概率 0 与 1、差 -1",
    diff["A"] == Fraction(0) and diff["B"] == Fraction(1) and diff["difference"] == Fraction(-1),
)
check("巡航核心: 整体周期 D=2", cruise_res["A"]["period"] == 2)
check(
    "巡航核心: 闭类周期 2 且循环子类权重精确",
    cruise_res["A"]["closedClasses"][0]["period"] == 2
    and cruise_res["A"]["closedClasses"][0]["cyclic"][0]["weight"] == Fraction(1),
)

# 暂态 + 双吸收闭类：暂态质量 1、权重各 1/2、相位 0 安全占比 1/2
MABS = [["0", "1/2", "1/2"], ["0", "1", "0"], ["0", "0", "1"]]
rep = audit(proc(3, ["1", "0", "0"], [2], {"x": MABS}), ["x"])
check(
    "巡航核心: 暂态质量与闭类贡献权重精确",
    rep["transientMass"] == Fraction(1)
    and sorted(c["weight"] for c in rep["closedClasses"]) == [Fraction(1, 2), Fraction(1, 2)]
    and rep["phases"][0]["safety"] == [Fraction(1, 2)],
)

# 等价样例巡航一致；闭类权重总和恒为 1
MEQ = [["1/2", "1/2"], ["1/3", "2/3"]]
same = proc(2, ["1", "0"], [1], {"x": MEQ})
cruise_eq = audit_pair(same, proc(2, ["1", "0"], [1], {"x": MEQ}), ["x"])
check("巡航核心: 等价样例逐相位一致", cruise_eq["consistent"] is True)
check(
    "巡航核心: 相位 0 安全占比精确为 3/5",
    cruise_eq["A"]["phases"][0]["safety"] == [Fraction(3, 5)],
)
check(
    "巡航核心: 闭类贡献权重总和为 1",
    sum((c["weight"] for c in cruise_eq["A"]["closedClasses"]), Fraction(0)) == 1,
)

# ---------------------------------------------------------------------------
# 2. 构建检查
# ---------------------------------------------------------------------------

# 容器内代码位于 /app；本地运行时回退到挂载的仓库路径。
_ROOT = os.environ.get("REPO_ROOT", "/workspace")
_backend = _first_existing("/app/app/main.py", os.path.join(_ROOT, "backend/app/main.py"))
_core = _first_existing("/app/app/equivalence.py", os.path.join(_ROOT, "backend/app/equivalence.py"))
_index = _first_existing("/app/static/index.html", os.path.join(_ROOT, "frontend/index.html"))
_appjs = _first_existing("/app/static/app.js", os.path.join(_ROOT, "frontend/app.js"))

check("构建: 后端入口存在", _backend is not None)
check("构建: 核心模块存在", _core is not None)
check("构建: 前端页面存在", _index is not None)
check("构建: 前端脚本存在", _appjs is not None)
import uvicorn  # noqa: E402,F401

check("构建: uvicorn 可导入", True)

# ---------------------------------------------------------------------------
# 3. API 冒烟
# ---------------------------------------------------------------------------


def http(method: str, path: str, payload=None):
    url = TARGET.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


status, body = http("GET", "/healthz")
check("API 冒烟: /healthz 200", status == 200 and body.get("status") == "ok", f"{status} {body}")

pair_eq = {
    "A": {
        "n": 2,
        "initial": ["1", "0"],
        "safe": [1],
        "commands": [
            {
                "symbol": "x",
                "rows": [
                    [{"target": 0, "prob": "1/2"}, {"target": 1, "prob": "1/2"}],
                    [{"target": 0, "prob": "1/3"}, {"target": 1, "prob": "2/3"}],
                ],
            }
        ],
    },
}
pair_eq["B"] = json.loads(json.dumps(pair_eq["A"]))
status, body = http("POST", "/api/review", pair_eq)
check(
    "API 冒烟: 等价对返回 equivalent=true",
    status == 200 and body.get("result", {}).get("equivalent") is True,
    f"{status} {body}",
)

pair_diff = {
    "A": {
        "n": 3,
        "initial": ["1", "0", "0"],
        "safe": [2],
        "commands": [
            {
                "symbol": "a",
                "rows": [
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}, {"target": 2, "prob": "0"}],
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}, {"target": 2, "prob": "0"}],
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "0"}, {"target": 2, "prob": "1"}],
                ],
            }
        ],
    },
    "B": {
        "n": 3,
        "initial": ["1", "0", "0"],
        "safe": [2],
        "commands": [
            {
                "symbol": "a",
                "rows": [
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}, {"target": 2, "prob": "0"}],
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "0"}, {"target": 2, "prob": "1"}],
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "0"}, {"target": 2, "prob": "1"}],
                ],
            }
        ],
    },
}
status, body = http("POST", "/api/review", pair_diff)
result = body.get("result", {})
check("API 冒烟: 反例 200 且 word=aa", status == 200 and result.get("word") == ["a", "a"], str(body))
check(
    "API 冒烟: 概率差为精确分数 {num,den,text}",
    result.get("difference", {}).get("num") == -1
    and result["difference"].get("den") == 1
    and result["difference"].get("text") == "-1",
    str(result.get("difference")),
)
check(
    "API 冒烟: 两侧逐步分布均为精确分数",
    all(
        set(step["distribution"][0].keys()) == {"num", "den", "text"}
        for step in result.get("A", {}).get("steps", [])
    ),
)

pair_bad = json.loads(json.dumps(pair_eq))
pair_bad["A"]["initial"] = ["1/2", "1/3"]  # 和为 5/6
pair_bad["A"]["safe"] = []  # 安全态缺失
pair_bad["B"]["commands"][0]["rows"][0][1]["prob"] = "1/0"  # 非法分数
status, body = http("POST", "/api/review", pair_bad)
errors = body.get("errors", [])
locs = [tuple(e.get("loc", [])) for e in errors]
check("API 冒烟: 非法输入返回 400", status == 400, str(status))
check("API 冒烟: 错误包含可定位 loc", any(len(loc) > 0 for loc in locs), str(locs))
check(
    "API 冒烟: 定位到 initial / safe / 非法分数三处",
    any(loc[:2] == ("A", "initial") for loc in locs)
    and any(loc[:2] == ("A", "safe") for loc in locs)
    and any("prob" in loc for loc in locs),
    str(locs),
)

# --- /api/cruise：巡航周期长期安全占比审计 -------------------------------

cruise_eq_body = dict(pair_eq)
cruise_eq_body["cycle"] = "x"
status, body = http("POST", "/api/cruise", cruise_eq_body)
result = body.get("result", {})
check(
    "巡航 API: 等价对逐相位一致",
    status == 200 and result.get("consistent") is True,
    f"{status} {body}",
)
check(
    "巡航 API: 服务端确认周期与精确相位概率 3/5",
    result.get("cycle") == ["x"]
    and result.get("A", {}).get("phases", [{}])[0].get("safety") == [
        {"num": 3, "den": 5, "text": "3/5"}
    ],
    str(result.get("A", {}).get("phases")),
)
check(
    "巡航 API: 闭类周期与贡献权重为精确分数",
    result.get("A", {}).get("closedClasses", [{}])[0].get("weight")
    == {"num": 1, "den": 1, "text": "1"},
)

cruise_diff_body = {
    "A": {
        "n": 2,
        "initial": ["1", "0"],
        "safe": [1],
        "commands": [
            {
                "symbol": "x",
                "rows": [
                    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}],
                    [{"target": 0, "prob": "1"}, {"target": 1, "prob": "0"}],
                ],
            }
        ],
    },
    "cycle": "x",
}
cruise_diff_body["B"] = json.loads(json.dumps(cruise_diff_body["A"]))
cruise_diff_body["B"]["safe"] = [0]
status, body = http("POST", "/api/cruise", cruise_diff_body)
result = body.get("result", {})
diff = result.get("firstDifference", {})
check(
    "巡航 API: 差异对返回最早相位与两侧精确概率",
    status == 200
    and result.get("consistent") is False
    and diff.get("phase") == 0
    and diff.get("subPhase") == 0
    and diff.get("A") == {"num": 0, "den": 1, "text": "0"}
    and diff.get("B") == {"num": 1, "den": 1, "text": "1"}
    and diff.get("difference") == {"num": -1, "den": 1, "text": "-1"},
    f"{status} {body}",
)

cruise_bad = json.loads(json.dumps(cruise_eq_body))
cruise_bad["cycle"] = ""
status, body = http("POST", "/api/cruise", cruise_bad)
check(
    "巡航 API: 空周期 400 且定位巡航输入",
    status == 400 and any(e.get("loc") == ["cycle"] for e in body.get("errors", [])),
    f"{status} {body}",
)

cruise_bad = json.loads(json.dumps(cruise_eq_body))
cruise_bad["cycle"] = "xz"
status, body = http("POST", "/api/cruise", cruise_bad)
check(
    "巡航 API: 未声明命令 400 且定位到具体相位",
    status == 400 and any(e.get("loc") == ["cycle", 1] for e in body.get("errors", [])),
    f"{status} {body}",
)

cruise_bad = json.loads(json.dumps(cruise_eq_body))
cruise_bad["cycle"] = "x" * 7
status, body = http("POST", "/api/cruise", cruise_bad)
check(
    "巡航 API: 周期超长 400 且定位巡航输入",
    status == 400 and any(e.get("loc") == ["cycle"] for e in body.get("errors", [])),
    f"{status} {body}",
)

cruise_bad = json.loads(json.dumps(cruise_eq_body))
cruise_bad["A"] = {
    "n": 9,
    "initial": ["1"] + ["0"] * 8,
    "safe": [0],
    "commands": [
        {
            "symbol": "x",
            "rows": [
                [{"target": j, "prob": "1" if i == j else "0"} for j in range(9)]
                for i in range(9)
            ],
        }
    ],
}
status, body = http("POST", "/api/cruise", cruise_bad)
check(
    "巡航 API: 状态数超限 400 且定位 A.n",
    status == 400 and any(e.get("loc") == ["A", "n"] for e in body.get("errors", [])),
    f"{status} {body}",
)

# 原有有限串复核接口响应保持不变
status, body = http("POST", "/api/review", pair_eq)
check(
    "巡航上线后 /api/review 响应不变",
    status == 200 and body.get("result") == {"equivalent": True},
    f"{status} {body}",
)

# ---------------------------------------------------------------------------
# 验收结论
# ---------------------------------------------------------------------------

if failures:
    print(f"\nVERIFY FAILED: {len(failures)} 项未通过 -> {failures}")
    sys.exit(1)
print("\nVERIFY PASSED: 最短反例、构建检查、API 冒烟全部通过")
sys.exit(0)
