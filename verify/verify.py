#!/usr/bin/env python3
"""Compose verify 服务入口。

验收三件事，全部通过才以退出码 0 结束：

1. 最短概率反例：直接在镜像内调用精确有理数核心，断言
   - 差异只在长度 2 暴露时给出最短反例 "aa"；
   - 同长度按 ASCII 字典序取最小；
   - 等价样例判定为等价；
   - 概率和不为一 / 非法分数被拒绝。
2. 检查构建：镜像内应用代码、静态页面与 uvicorn 均就位。
3. API 冒烟：对运行中的服务打 /healthz、/api/review（等价、反例、非法输入），
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
from app.cruise import audit_cruise  # noqa: E402

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
# 1b. 巡航周期长期安全占比审计（核心，精确有理数，无模拟/浮点）
# ---------------------------------------------------------------------------

# 周期 2 闭类：安全概率必须在 1、0 间交替，而非取长期平均 1/2
SWAP = [["0", "1"], ["1", "0"]]
STAY = [["0", "1"], ["0", "1"]]
cres = audit_cruise(
    proc(2, ["1", "0"], [0], {"x": SWAP}),
    proc(2, ["1", "0"], [0], {"x": SWAP}),
    ["x"],
)
check("巡航: 周期2闭类识别正确", cres["A"]["classes"][0]["period"] == 2, str(cres["A"]["classes"]))
check(
    "巡航: 相位概率精确交替 1,0（非平均 1/2）",
    cres["A"]["phaseProbs"] == [Fraction(1), Fraction(0)],
    str(cres["A"]["phaseProbs"]),
)

# 暂态质量分流：1/2→周期2闭类、1/2→吸收闭类，相位安全概率 [1/2, 1]
MIX = [
    ["0", "1", "0", "0"],
    ["1", "0", "0", "0"],
    ["0", "0", "1", "0"],
    ["1/2", "0", "1/2", "0"],
]
cres2 = audit_cruise(
    proc(4, ["0", "0", "0", "1"], [0, 2], {"x": MIX}),
    proc(4, ["0", "0", "0", "1"], [0, 2], {"x": MIX}),
    ["x"],
)
check(
    "巡航: 暂态/闭类分流与吸收权重精确",
    cres2["A"]["phaseProbs"] == [Fraction(1, 2), Fraction(1)]
    and sum((c["weight"] for c in cres2["A"]["classes"]), Fraction(0)) == 1,
    str(cres2["A"]["phaseProbs"]),
)

# 最早相位差异：A 交替（t=0 为 1），B 吸收到非安全态（恒 0）
cres3 = audit_cruise(
    proc(2, ["1", "0"], [0], {"x": SWAP}),
    proc(2, ["1", "0"], [0], {"x": STAY}),
    ["x"],
)
fd3 = cres3["firstDifference"]
check(
    "巡航: 最早差异相位为 t=0 且概率精确",
    cres3["equivalent"] is False
    and fd3["t"] == 0
    and fd3["A"] == Fraction(1)
    and fd3["B"] == Fraction(0),
    str(fd3),
)
check(
    "巡航: 闭类贡献之和恰为该侧精确概率",
    sum((c["safetyContribution"] for c in fd3["contributions"]["A"]), Fraction(0)) == Fraction(1)
    and sum((c["safetyContribution"] for c in fd3["contributions"]["B"]), Fraction(0)) == Fraction(0),
)

# k=2 周期：轮内前缀不可交换，两相位分别为 1/3、2/3
MX = [["0", "1"], ["1", "0"]]
MY = [["1/2", "1/2"], ["0", "1"]]
cres4 = audit_cruise(
    proc(2, ["1", "0"], [0], {"x": MX, "y": MY}),
    proc(2, ["1", "0"], [0], {"x": MX, "y": MY}),
    ["x", "y"],
)
check(
    "巡航: 长度2周期的逐相位概率 1/3, 2/3",
    cres4["A"]["phaseProbs"] == [Fraction(1, 3), Fraction(2, 3)],
    str(cres4["A"]["phaseProbs"]),
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

# --- 巡航审计 API 冒烟 -----------------------------------------------------

cruise_swap = {
    "n": 2,
    "initial": ["1", "0"],
    "safe": [0],
    "commands": [
        {
            "symbol": "x",
            "rows": [
                [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}],
                [{"target": 0, "prob": "1"}, {"target": 1, "prob": "0"}],
            ],
        }
    ],
}
status, body = http("POST", "/api/cruise", {"A": cruise_swap, "B": cruise_swap, "cycle": "x"})
cresult = body.get("result", {})
check("API 冒烟: 巡航等价对 200", status == 200 and cresult.get("equivalent") is True, f"{status} {body}")
check(
    "API 冒烟: 巡航周期由服务端确认回显",
    cresult.get("cycle") == "x" and cresult.get("cycleLength") == 1,
    str(cresult.get("cycle")),
)
check(
    "API 巡航: 逐相位精确分数交替 1,0",
    [p.get("A", {}).get("text") for p in cresult.get("phases", [])] == ["1", "0"],
    str(cresult.get("phases")),
)
check(
    "API 巡航: 闭类周期与循环类证据给出",
    cresult.get("A", {}).get("classes", [{}])[0].get("period") == 2
    and cresult["A"]["classes"][0].get("cyclicClasses") == [[0], [1]],
)

cruise_stay = json.loads(json.dumps(cruise_swap))
cruise_stay["commands"][0]["rows"] = [
    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}],
    [{"target": 0, "prob": "0"}, {"target": 1, "prob": "1"}],
]
status, body = http("POST", "/api/cruise", {"A": cruise_swap, "B": cruise_stay, "cycle": "x"})
fd = body.get("result", {}).get("firstDifference", {})
check(
    "API 巡航: 差异对给出最早相位与两侧精确概率",
    status == 200
    and fd.get("t") == 0
    and fd.get("A", {}).get("text") == "1"
    and fd.get("B", {}).get("text") == "0"
    and fd.get("difference", {}).get("text") == "1",
    str(fd),
)
check(
    "API 巡航: 差异含两侧闭类贡献权重（精确分数）",
    all(
        set(c.get("weight", {})) == {"num", "den", "text"}
        and set(c.get("safetyContribution", {})) == {"num", "den", "text"}
        for side in ("A", "B")
        for c in fd.get("contributions", {}).get(side, [])
    ),
)

# 巡航输入非法：空周期 / 未声明命令 / 超长，均 400 且 loc 定位到 cycle
for bad_cycle, needle in (("", "cycle"), ("xz", 1), ("xxxxxxx", "cycle")):
    status, body = http(
        "POST", "/api/cruise", {"A": cruise_swap, "B": cruise_swap, "cycle": bad_cycle}
    )
    locs = [tuple(e.get("loc", [])) for e in body.get("errors", [])]
    ok = status == 400 and any(loc and loc[0] == "cycle" for loc in locs)
    if needle != "cycle":
        ok = ok and any(loc == ("cycle", needle) for loc in locs)
    check(f"API 巡航: 非法周期 {bad_cycle!r} 400 且定位 cycle", ok, f"{status} {locs}")

# 状态数超限：巡航拒绝（loc=cycle），普通复核保持可用
big_ident = {
    "n": 9,
    "initial": ["1"] + ["0"] * 8,
    "safe": [0],
    "commands": [
        {
            "symbol": "x",
            "rows": [
                [
                    {"target": j, "prob": "1" if i == j else "0"}
                    for j in range(9)
                ]
                for i in range(9)
            ],
        }
    ],
}
status, body = http("POST", "/api/cruise", {"A": big_ident, "B": big_ident, "cycle": "x"})
check(
    "API 巡航: 状态数超 8 被拒且定位 cycle",
    status == 400 and any(tuple(e.get("loc", [])) == ("cycle",) for e in body.get("errors", [])),
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
