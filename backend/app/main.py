"""FastAPI 服务：声学应急控制器规程等价性复核。"""

from __future__ import annotations

import os
from fractions import Fraction
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(Path(STATIC_DIR) / "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
