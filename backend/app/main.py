"""FastAPI 服务：声学应急控制器规程等价性复核。"""

from __future__ import annotations

import os
from fractions import Fraction
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .cruise import MAX_CYCLE_LEN, MAX_STATES, audit_cruise
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


def _serialize_side_cruise(side: dict[str, Any]) -> dict[str, Any]:
    return {
        "nStates": side["n"],
        "grid": side["grid"],
        "periodRounds": side["periodRounds"],
        "transientStates": side["transientStates"],
        "transientMass": _fraction(side["transientMass"]),
        "classes": [
            {
                "states": cls["states"],
                "period": cls["period"],
                "cyclicClasses": cls["cyclicClasses"],
                "stationary": [_fraction(cls["stationary"][i]) for i in cls["states"]],
                "weight": _fraction(cls["weight"]),
            }
            for cls in side["classes"]
        ],
        "phaseProbs": [_fraction(x) for x in side["phaseProbs"]],
        "phaseContributions": [
            [_fraction(x) for x in row] for row in side["phaseContrib"]
        ],
    }


def _serialize_cruise(result: dict[str, Any]) -> dict[str, Any]:
    first = result["firstDifference"]
    first_out: dict[str, Any] | None = None
    if first is not None:
        first_out = {
            "t": first["t"],
            "round": first["round"],
            "phase": first["phase"],
            "A": _fraction(first["A"]),
            "B": _fraction(first["B"]),
            "difference": _fraction(first["difference"]),
            "contributions": {
                side: [
                    {
                        "states": c["states"],
                        "period": c["period"],
                        "weight": _fraction(c["weight"]),
                        "safetyContribution": _fraction(c["safetyContribution"]),
                    }
                    for c in first["contributions"][side]
                ]
                for side in ("A", "B")
            },
        }
    return {
        "cycle": "".join(result["cycle"]),
        "cycleLength": result["cycleLength"],
        "commonGrid": result["commonGrid"],
        "commonPeriodRounds": result["commonPeriodRounds"],
        "equivalent": result["equivalent"],
        "firstDifference": first_out,
        "phases": [
            {
                "t": ph["t"],
                "round": ph["round"],
                "phase": ph["phase"],
                "A": _fraction(ph["A"]),
                "B": _fraction(ph["B"]),
                "equal": ph["equal"],
            }
            for ph in result["phases"]
        ],
        "A": _serialize_side_cruise(result["A"]),
        "B": _serialize_side_cruise(result["B"]),
    }


def _parse_procedures(payload: dict[str, Any]) -> tuple[JSONResponse | None, Any, Any]:
    """校验两份规程；失败时返回 (400 响应, None, None)。"""
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
        return JSONResponse(status_code=400, content={"ok": False, "errors": errors}), None, None
    return None, proc_a, proc_b


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

    bad, proc_a, proc_b = _parse_procedures(payload)
    if bad is not None:
        return bad

    try:
        result = compare(proc_a, proc_b)
    except ProcedureValidationError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "errors": exc.errors})

    return JSONResponse(status_code=200, content={"ok": True, "result": _serialize(result)})


def _validate_cycle(proc_a: Any, proc_b: Any, raw_cycle: Any) -> list[dict[str, Any]]:
    """巡航周期专项校验：为空 / 超长 / 含未声明命令 / 状态数超限。"""
    errors: list[dict[str, Any]] = []

    if proc_a.n > MAX_STATES or proc_b.n > MAX_STATES:
        over = [s for s, p in (("A", proc_a), ("B", proc_b)) if p.n > MAX_STATES]
        errors.append(
            {
                "loc": ["cycle"],
                "msg": (
                    f"巡航审计要求每侧状态数不超过 {MAX_STATES}，"
                    f"规程 {'、'.join(over)} 超限；本次巡航未执行"
                ),
            }
        )

    if not isinstance(raw_cycle, str) or raw_cycle == "":
        errors.append({"loc": ["cycle"], "msg": "巡航周期不能为空：请输入 1–6 个共同 ASCII 命令字符"})
        return errors

    cycle = list(raw_cycle)
    if len(cycle) > MAX_CYCLE_LEN:
        errors.append(
            {
                "loc": ["cycle"],
                "msg": f"巡航周期长度不得超过 {MAX_CYCLE_LEN}，当前为 {len(cycle)}",
            }
        )

    undeclared = sorted(
        {c for c in cycle if c not in proc_a.matrices or c not in proc_b.matrices}
    )
    if undeclared:
        shown = " ".join(repr(c) for c in undeclared)
        for i, c in enumerate(cycle):
            if c in undeclared:
                errors.append(
                    {
                        "loc": ["cycle", i],
                        "msg": f"命令 {c!r} 未在两侧规程中共同声明",
                    }
                )
        errors.append(
            {
                "loc": ["cycle"],
                "msg": f"巡航周期含未在两侧共同声明的命令：{shown}",
            }
        )

    return errors


@app.post("/api/cruise")
async def cruise(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "errors": [{"loc": ["cycle"], "msg": "请求体不是合法 JSON"}]},
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("A"), dict) or not isinstance(
        payload.get("B"), dict
    ):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{"loc": ["cycle"], "msg": "请求体必须是包含 A、B 两份规程的对象"}],
            },
        )

    bad, proc_a, proc_b = _parse_procedures(payload)
    if bad is not None:
        return bad

    if set(proc_a.symbols) != set(proc_b.symbols):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [
                    {
                        "loc": ["commands"],
                        "msg": "两份规程的命令字符集必须一致，才能指定共同巡航周期",
                    }
                ],
            },
        )

    errors = _validate_cycle(proc_a, proc_b, payload.get("cycle"))
    if errors:
        return JSONResponse(status_code=400, content={"ok": False, "errors": errors})

    result = audit_cruise(proc_a, proc_b, list(payload["cycle"]))
    return JSONResponse(
        status_code=200,
        content={"ok": True, "result": _serialize_cruise(result)},
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(Path(STATIC_DIR) / "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
