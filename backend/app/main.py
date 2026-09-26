"""FastAPI 服务：声学应急控制器规程等价性复核。"""

from __future__ import annotations

import os
from fractions import Fraction
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .cruise import MAX_CYCLE_LENGTH, MAX_STATES, audit_pair
from .equivalence import ProcedureValidationError, compare, parse_procedure


def _static_dir() -> str:
    configured = os.environ.get("STATIC_DIR")
    candidates = [
        configured,
        "/app/static",
        str(Path(__file__).resolve().parents[2] / "frontend"),
    ]
    for path in candidates:
        if path and Path(path).is_dir():
            return path
    return "/app/static"  # 保留默认，交由 StaticFiles 报出明确错误


STATIC_DIR = _static_dir()

app = FastAPI(title="声学规程等价性复核", version="1.0.0")


def _fraction(value: Fraction) -> dict[str, Any]:
    """有理数序列化：同时给出精确分子/分母与分数串，绝不使用浮点。"""
    return {"num": value.numerator, "den": value.denominator, "text": str(value)}


def _serialize(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("equivalent"):
        return {"equivalent": True}
    out: dict[str, Any] = {
        "equivalent": False,
        "word": result["word"],
        "wordLength": result["wordLength"],
        "difference": _fraction(result["difference"]),
    }
    for side in ("A", "B"):
        trace = result[side]
        out[side] = {
            "final": _fraction(trace["final"]),
            "steps": [
                {
                    "distribution": [_fraction(x) for x in step["distribution"]],
                    "safeProb": _fraction(step["safeProb"]),
                }
                for step in trace["steps"]
            ],
        }
    return out


@app.post("/api/review")
async def review(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "errors": [{"loc": [], "msg": "请求体不是合法 JSON"}]},
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("A"), dict) or not isinstance(
        payload.get("B"), dict
    ):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{"loc": [], "msg": "请求体必须是包含 A、B 两份规程的对象"}],
            },
        )

    errors: list[dict[str, Any]] = []
    proc_a = proc_b = None
    try:
        proc_a = parse_procedure("A", payload["A"])
    except ProcedureValidationError as exc:
        errors.extend(exc.errors)
    try:
        proc_b = parse_procedure("B", payload["B"])
    except ProcedureValidationError as exc:
        errors.extend(exc.errors)

    if proc_a is None or proc_b is None:
        return JSONResponse(status_code=400, content={"ok": False, "errors": errors})

    try:
        result = compare(proc_a, proc_b)
    except ProcedureValidationError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "errors": exc.errors})

    return JSONResponse(status_code=200, content={"ok": True, "result": _serialize(result)})


# ---------------------------------------------------------------------------
# 巡航周期长期安全占比审计
# ---------------------------------------------------------------------------


def _serialize_cruise_side(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "period": report["period"],
        "transientStates": report["transientStates"],
        "transientMass": _fraction(report["transientMass"]),
        "closedClasses": [
            {
                "states": cls["states"],
                "period": cls["period"],
                "weight": _fraction(cls["weight"]),
                "safeShare": _fraction(cls["safeShare"]),
                "cyclic": [
                    {"states": cyc["states"], "weight": _fraction(cyc["weight"])}
                    for cyc in cls["cyclic"]
                ],
            }
            for cls in report["closedClasses"]
        ],
        "phases": [
            {"phase": ph["phase"], "safety": [_fraction(x) for x in ph["safety"]]}
            for ph in report["phases"]
        ],
    }


def _serialize_cruise(result: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "cycle": result["cycle"],
        "cycleLength": result["cycleLength"],
        "consistent": result["consistent"],
        "firstDifference": None,
        "A": _serialize_cruise_side(result["A"]),
        "B": _serialize_cruise_side(result["B"]),
    }
    if result["firstDifference"] is not None:
        diff = result["firstDifference"]
        out["firstDifference"] = {
            "phase": diff["phase"],
            "subPhase": diff["subPhase"],
            "A": _fraction(diff["A"]),
            "B": _fraction(diff["B"]),
            "difference": _fraction(diff["difference"]),
        }
    return out


@app.post("/api/cruise")
async def cruise(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "errors": [{"loc": [], "msg": "请求体不是合法 JSON"}]},
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("A"), dict) or not isinstance(
        payload.get("B"), dict
    ):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{"loc": [], "msg": "请求体必须是包含 A、B 两份规程与巡航周期 cycle 的对象"}],
            },
        )

    errors: list[dict[str, Any]] = []
    proc_a = proc_b = None
    try:
        proc_a = parse_procedure("A", payload["A"])
    except ProcedureValidationError as exc:
        errors.extend(exc.errors)
    try:
        proc_b = parse_procedure("B", payload["B"])
    except ProcedureValidationError as exc:
        errors.extend(exc.errors)

    # 巡航周期：非空、长度 ≤ 6、纯 ASCII，且每条命令均为两侧共同声明
    cycle_raw = payload.get("cycle")
    cycle: list[str] | None = None
    if not isinstance(cycle_raw, str) or not cycle_raw:
        errors.append(
            {
                "loc": ["cycle"],
                "msg": f"巡航周期不能为空：须为 1–{MAX_CYCLE_LENGTH} 个两侧共同声明的 ASCII 命令",
            }
        )
    elif len(cycle_raw) > MAX_CYCLE_LENGTH:
        errors.append(
            {
                "loc": ["cycle"],
                "msg": f"巡航周期长度不能超过 {MAX_CYCLE_LENGTH}，当前为 {len(cycle_raw)}",
            }
        )
    elif any(ord(ch) >= 128 for ch in cycle_raw):
        bad = next(i for i, ch in enumerate(cycle_raw) if ord(ch) >= 128)
        errors.append(
            {"loc": ["cycle", bad], "msg": f"第 {bad + 1} 个字符 {cycle_raw[bad]!r} 不是 ASCII 命令"}
        )
    else:
        cycle = list(cycle_raw)

    # 巡航审计的规模上限：每侧至多 8 个状态
    for label, proc in (("A", proc_a), ("B", proc_b)):
        if proc is not None and proc.n > MAX_STATES:
            errors.append(
                {
                    "loc": [label, "n"],
                    "msg": f"巡航审计每侧至多 {MAX_STATES} 个状态，{label} 当前为 {proc.n}",
                }
            )

    if cycle is not None and proc_a is not None and proc_b is not None:
        common = set(proc_a.symbols) & set(proc_b.symbols)
        for i, ch in enumerate(cycle):
            if ch not in common:
                errors.append(
                    {"loc": ["cycle", i], "msg": f"命令 {ch!r} 未在两侧规程中共同声明"}
                )

    if errors or proc_a is None or proc_b is None or cycle is None:
        return JSONResponse(status_code=400, content={"ok": False, "errors": errors})

    result = audit_pair(proc_a, proc_b, cycle)
    return JSONResponse(status_code=200, content={"ok": True, "result": _serialize_cruise(result)})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(Path(STATIC_DIR) / "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
