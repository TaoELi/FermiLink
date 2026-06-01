from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT_SECONDS = 20


def validate_algebra(obligation: dict[str, Any]) -> dict[str, Any]:
    try:
        import sympy as sp  # type: ignore[import-not-found]
    except Exception as exc:
        return _unknown("sympy_unavailable", str(exc))

    try:
        locals_map = _sympy_locals(sp, obligation)
        expression = str(obligation.get("expression") or "").strip()
        lhs = str(obligation.get("lhs") or "").strip()
        rhs = str(obligation.get("rhs") or "").strip()
        if not expression and not (lhs and rhs):
            claim = str(obligation.get("claim") or "").strip()
            if "=" in claim:
                lhs, rhs = claim.split("=", 1)
            else:
                return _open("missing_algebra_expression")
        if expression:
            residual = sp.sympify(expression, locals=locals_map)
        else:
            residual = sp.sympify(lhs, locals=locals_map) - sp.sympify(
                rhs, locals=locals_map
            )
        simplified = sp.simplify(residual)
    except Exception as exc:
        return _failed("sympy_parse_failed", str(exc))

    if simplified == 0:
        return _passed("sympy_simplify_zero", {"residual": str(simplified)})
    return _failed("sympy_residual_nonzero", {"residual": str(simplified)})


def validate_commutator(obligation: dict[str, Any]) -> dict[str, Any]:
    try:
        import sympy as sp  # type: ignore[import-not-found]
    except Exception as exc:
        return _unknown("sympy_unavailable", str(exc))

    lhs = str(obligation.get("lhs") or obligation.get("commutator") or "").strip()
    rhs = str(obligation.get("rhs") or obligation.get("expected") or "").strip()
    if not lhs or not rhs:
        return _open("missing_commutator_fields")
    try:
        locals_map = _sympy_locals(sp, obligation, noncommutative_default=True)
        relation_result = _validate_declared_commutator_relation(
            sp,
            obligation,
            lhs=lhs,
            rhs=rhs,
            locals_map=locals_map,
        )
        if relation_result is not None:
            return relation_result
        lhs_expr = _parse_operator_expression(sp, lhs, locals_map)
        rhs_expr = _parse_operator_expression(sp, rhs, locals_map)
        residual = sp.expand(lhs_expr - rhs_expr)
    except Exception as exc:
        return _failed("commutator_parse_failed", str(exc))
    if residual == 0:
        return _passed("commutator_identity_matches", {"residual": str(residual)})
    return _failed("commutator_residual_nonzero", {"residual": str(residual)})


def validate_limit(obligation: dict[str, Any]) -> dict[str, Any]:
    try:
        import sympy as sp  # type: ignore[import-not-found]
    except Exception as exc:
        return _unknown("sympy_unavailable", str(exc))

    expression = str(obligation.get("expression") or "").strip()
    variable = str(obligation.get("variable") or "").strip()
    point = str(obligation.get("point") or "").strip()
    expected = str(obligation.get("expected") or "").strip()
    if not all((expression, variable, point, expected)):
        return _open("missing_limit_fields")
    try:
        locals_map = _sympy_locals(sp, obligation)
        symbol = locals_map.get(variable) or sp.symbols(variable)
        actual = sp.limit(
            sp.sympify(expression, locals=locals_map),
            symbol,
            sp.sympify(point, locals=locals_map),
        )
        expected_value = sp.sympify(expected, locals=locals_map)
        residual = sp.simplify(actual - expected_value)
    except Exception as exc:
        return _failed("sympy_limit_failed", str(exc))
    if residual == 0:
        return _passed("sympy_limit_matches", {"actual": str(actual)})
    return _failed(
        "sympy_limit_mismatch",
        {"actual": str(actual), "expected": expected, "residual": str(residual)},
    )


def validate_trace_preservation(obligation: dict[str, Any]) -> dict[str, Any]:
    expression = str(obligation.get("expression") or obligation.get("trace") or "")
    if expression.strip():
        algebra_obligation = dict(obligation)
        algebra_obligation["expression"] = expression
        return validate_algebra(algebra_obligation)
    matrix = obligation.get("matrix")
    if matrix is None:
        return _open("missing_trace_expression")
    try:
        import sympy as sp  # type: ignore[import-not-found]

        mat = sp.Matrix(matrix)
        trace_value = sp.simplify(mat.trace())
    except Exception as exc:
        return _failed("trace_parse_failed", str(exc))
    if trace_value == 0:
        return _passed("trace_is_zero", {"trace": str(trace_value)})
    return _failed("trace_nonzero", {"trace": str(trace_value)})


def validate_hermiticity(obligation: dict[str, Any]) -> dict[str, Any]:
    matrix = obligation.get("matrix")
    if matrix is None:
        return _open("missing_matrix")
    try:
        import sympy as sp  # type: ignore[import-not-found]

        mat = sp.Matrix(matrix)
        residual = sp.simplify(mat - mat.conjugate().T)
    except Exception as exc:
        return _failed("hermiticity_parse_failed", str(exc))
    if residual == sp.zeros(*mat.shape):
        return _passed("matrix_is_hermitian", {"shape": list(mat.shape)})
    return _failed("matrix_not_hermitian", {"residual": str(residual)})


def validate_tensor_index(obligation: dict[str, Any]) -> dict[str, Any]:
    expression = str(obligation.get("expression") or "").strip()
    if not expression:
        return _open("missing_tensor_expression")
    repeated_limit = int(obligation.get("max_repetitions", 2) or 2)
    tokens = re.findall(r"(?:^|[^A-Za-z])([A-Za-z]+)_\{?([A-Za-z]+)\}?", expression)
    counts: dict[str, int] = {}
    for _symbol, raw_indices in tokens:
        for index in raw_indices:
            counts[index] = counts.get(index, 0) + 1
    overused = {key: value for key, value in counts.items() if value > repeated_limit}
    if overused:
        return _failed("index_repeated_too_many_times", {"indices": overused})
    required_free = [str(item) for item in obligation.get("free_indices") or []]
    if required_free:
        free = sorted(key for key, value in counts.items() if value == 1)
        if sorted(required_free) != free:
            return _failed(
                "free_indices_mismatch",
                {"expected": sorted(required_free), "actual": free},
            )
    return _passed("tensor_indices_consistent", {"index_counts": counts})


def validate_perturbation_order(obligation: dict[str, Any]) -> dict[str, Any]:
    try:
        max_order = int(obligation.get("max_order"))
    except (TypeError, ValueError) as exc:
        return _open(f"missing_max_order: {exc}")
    terms = obligation.get("terms")
    if not isinstance(terms, list):
        return _open("missing_terms")
    excess: list[dict[str, Any]] = []
    for item in terms:
        if not isinstance(item, dict):
            continue
        try:
            order = int(item.get("order"))
        except (TypeError, ValueError):
            excess.append({"term": item, "reason": "missing_order"})
            continue
        if order > max_order and not bool(item.get("dropped")):
            excess.append({"term": item, "reason": "undropped_high_order"})
    if excess:
        return _failed("perturbation_order_violation", {"excess_terms": excess})
    return _passed("perturbation_order_consistent", {"max_order": max_order})


def validate_dimension(obligation: dict[str, Any]) -> dict[str, Any]:
    lhs = obligation.get("lhs_dimension", obligation.get("observed_dimension"))
    rhs = obligation.get("rhs_dimension", obligation.get("expected_dimension"))
    if lhs is None or rhs is None:
        return _open("missing_dimension_fields")
    if _normalize_dimension(lhs) == _normalize_dimension(rhs):
        return _passed("dimensions_match", {"dimension": _normalize_dimension(lhs)})
    return _failed(
        "dimension_mismatch",
        {
            "lhs_dimension": _normalize_dimension(lhs),
            "rhs_dimension": _normalize_dimension(rhs),
        },
    )


def validate_numeric(repo_dir: Path, obligation: dict[str, Any]) -> dict[str, Any]:
    if "observed" in obligation and "expected" in obligation:
        return _validate_numeric_values(obligation)

    script = str(obligation.get("script") or "").strip()
    if not script:
        return _open("missing_numeric_check")
    if not bool(obligation.get("trusted")):
        return _unknown(
            "numeric_script_not_trusted",
            "Set trusted: true to allow the controller to run a Python script under projects/.",
        )
    script_path = _safe_projects_path(repo_dir, script)
    if script_path is None:
        return _failed("numeric_script_outside_projects", script)
    if not script_path.is_file():
        return _failed("numeric_script_missing", script)
    timeout = _timeout_seconds(obligation)
    completed = _run_command(
        [sys.executable, str(script_path)],
        cwd=repo_dir,
        timeout=timeout,
    )
    if completed["status"] == "passed":
        return _passed(
            "numeric_script_passed",
            {
                "script": script,
                "stdout": completed.get("stdout", "")[-2000:],
            },
        )
    return _failed(
        "numeric_script_failed",
        {
            "script": script,
            "return_code": completed.get("return_code"),
            "stdout": completed.get("stdout", "")[-2000:],
            "stderr": completed.get("stderr", "")[-2000:],
        },
    )


def validate_latex(repo_dir: Path, obligation: dict[str, Any]) -> dict[str, Any]:
    rel = str(
        obligation.get("path")
        or obligation.get("file")
        or obligation.get("latex_path")
        or ""
    ).strip()
    if not rel:
        return _open("missing_latex_path")
    path = _safe_projects_path(repo_dir, rel)
    if path is None:
        return _failed("latex_path_outside_projects", rel)
    if not path.is_file():
        return _failed("latex_file_missing", rel)
    if not bool(obligation.get("compile", True)):
        return _passed("latex_file_exists", {"path": rel})

    latexmk = shutil.which("latexmk")
    pdflatex = shutil.which("pdflatex")
    timeout = _timeout_seconds(obligation)
    if latexmk:
        completed = _run_command(
            [latexmk, "-pdf", "-interaction=nonstopmode", "-halt-on-error", path.name],
            cwd=path.parent,
            timeout=timeout,
        )
    elif pdflatex:
        completed = _run_command(
            [pdflatex, "-interaction=nonstopmode", "-halt-on-error", path.name],
            cwd=path.parent,
            timeout=timeout,
        )
    else:
        return _unknown("latex_compiler_unavailable", "latexmk/pdflatex not found")
    if completed["status"] == "passed":
        return _passed("latex_compiled", {"path": rel})
    return _failed(
        "latex_compile_failed",
        {
            "path": rel,
            "return_code": completed.get("return_code"),
            "stdout": completed.get("stdout", "")[-2000:],
            "stderr": completed.get("stderr", "")[-2000:],
        },
    )


def validate_formal_optional(
    repo_dir: Path, obligation: dict[str, Any]
) -> dict[str, Any]:
    backend = str(obligation.get("backend") or "lean").strip().lower()
    rel = str(obligation.get("path") or obligation.get("file") or "").strip()
    if backend in {"mathematica", "wolfram", "wolframscript", "maple"}:
        return validate_external_cas(repo_dir, obligation, backend=backend)
    if backend != "lean":
        return _unknown("formal_backend_unsupported", backend)
    if not rel:
        return _open("missing_formal_path")
    path = _safe_projects_path(repo_dir, rel)
    if path is None:
        return _failed("formal_path_outside_projects", rel)
    if not path.is_file():
        return _failed("formal_file_missing", rel)
    lean = shutil.which("lean")
    if lean is None:
        return _unknown("lean_unavailable", "lean not found")
    completed = _run_command(
        [lean, str(path)],
        cwd=repo_dir,
        timeout=_timeout_seconds(obligation),
    )
    if completed["status"] == "passed":
        return _passed("lean_check_passed", {"path": rel})
    return _failed(
        "lean_check_failed",
        {
            "path": rel,
            "return_code": completed.get("return_code"),
            "stdout": completed.get("stdout", "")[-2000:],
            "stderr": completed.get("stderr", "")[-2000:],
        },
    )


def validate_external_cas(
    repo_dir: Path,
    obligation: dict[str, Any],
    *,
    backend: str,
) -> dict[str, Any]:
    rel = str(obligation.get("path") or obligation.get("file") or "").strip()
    if not rel:
        return _open("missing_cas_script_path")
    if not bool(obligation.get("trusted")):
        return _unknown(
            "cas_script_not_trusted",
            "Set trusted: true to run optional Mathematica/Maple scripts under projects/.",
        )
    path = _safe_projects_path(repo_dir, rel)
    if path is None:
        return _failed("cas_script_outside_projects", rel)
    if not path.is_file():
        return _failed("cas_script_missing", rel)
    if backend in {"mathematica", "wolfram", "wolframscript"}:
        binary = shutil.which("wolframscript")
        command = [binary, "-file", str(path)] if binary else []
    else:
        binary = shutil.which("maple")
        command = [binary, str(path)] if binary else []
    if not command:
        return _unknown("cas_backend_unavailable", backend)
    completed = _run_command(
        command,
        cwd=repo_dir,
        timeout=_timeout_seconds(obligation),
    )
    if completed["status"] == "passed":
        return _passed("cas_script_passed", {"backend": backend, "path": rel})
    return _failed(
        "cas_script_failed",
        {
            "backend": backend,
            "path": rel,
            "return_code": completed.get("return_code"),
            "stdout": completed.get("stdout", "")[-2000:],
            "stderr": completed.get("stderr", "")[-2000:],
        },
    )


def validate_final_artifact(
    repo_dir: Path, obligation: dict[str, Any]
) -> dict[str, Any]:
    project = str(obligation.get("project") or "").strip()
    patterns = obligation.get("globs")
    if not project or not isinstance(patterns, list):
        return _open("missing_final_artifact_fields")
    project_path = _safe_projects_dir(repo_dir, project)
    if project_path is None or not project_path.is_dir():
        return _failed("final_project_missing", project)
    matches: list[str] = []
    for pattern in patterns:
        for path in project_path.glob(str(pattern)):
            if path.is_file():
                matches.append(path.relative_to(repo_dir).as_posix())
    if matches:
        return _passed("final_artifact_found", {"matches": sorted(matches)})
    return _open("final_artifact_missing")


def validate_citation(obligation: dict[str, Any]) -> dict[str, Any]:
    if bool(obligation.get("derived_here")):
        return _passed("derived_in_artifact", {})
    citation = str(
        obligation.get("citation")
        or obligation.get("source")
        or obligation.get("reference")
        or ""
    ).strip()
    if not citation:
        return _open("missing_citation")
    lowered = citation.lower()
    placeholders = ("todo", "tbd", "citation needed", "?", "unknown")
    if any(item in lowered for item in placeholders):
        return _failed("citation_placeholder", citation)
    return _passed("citation_recorded", {"citation": citation})


def validate_assumption(
    obligation: dict[str, Any],
    *,
    spec_assumptions: list[str],
) -> dict[str, Any]:
    assumption = str(
        obligation.get("assumption") or obligation.get("claim") or ""
    ).strip()
    justification = str(obligation.get("justification") or "").strip()
    if not assumption:
        return _open("missing_assumption")
    normalized = _normalize_text(assumption)
    known = {_normalize_text(item) for item in spec_assumptions}
    if normalized in known or justification:
        return _passed(
            "assumption_accounted_for",
            {"assumption": assumption, "justification": justification},
        )
    return _unknown("assumption_needs_justification", assumption)


def _validate_numeric_values(obligation: dict[str, Any]) -> dict[str, Any]:
    try:
        observed = float(obligation.get("observed"))
        expected = float(obligation.get("expected"))
        atol = float(obligation.get("atol", obligation.get("absolute_tolerance", 1e-8)))
        rtol = float(obligation.get("rtol", obligation.get("relative_tolerance", 1e-8)))
    except (TypeError, ValueError) as exc:
        return _failed("numeric_value_parse_failed", str(exc))
    ok = math.isclose(
        observed, expected, rel_tol=max(0.0, rtol), abs_tol=max(0.0, atol)
    )
    evidence = {"observed": observed, "expected": expected, "atol": atol, "rtol": rtol}
    if ok:
        return _passed("numeric_values_match", evidence)
    return _failed("numeric_value_mismatch", evidence)


def _sympy_locals(
    sp: Any,
    obligation: dict[str, Any],
    *,
    noncommutative_default: bool = False,
) -> dict[str, Any]:
    symbols = obligation.get("symbols")
    names: list[str] = []
    if isinstance(symbols, list):
        names.extend(str(item) for item in symbols if str(item).strip())
    operators = obligation.get("operators")
    if isinstance(operators, list):
        names.extend(str(item) for item in operators if str(item).strip())
    for key in ("variable",):
        value = str(obligation.get(key) or "").strip()
        if value:
            names.append(value)
    noncommutative_names = {
        str(item).strip()
        for item in obligation.get("noncommutative_symbols") or []
        if str(item).strip()
    }
    locals_map = {
        name: sp.symbols(
            name,
            commutative=(
                False
                if noncommutative_default or name in noncommutative_names
                else True
            ),
        )
        for name in sorted(set(names))
    }
    locals_map.update({"I": sp.I, "pi": sp.pi, "E": sp.E})
    return locals_map


def _parse_operator_expression(sp: Any, text: str, locals_map: dict[str, Any]) -> Any:
    rendered = re.sub(
        r"\bcomm\s*\(\s*([A-Za-z]\w*)\s*,\s*([A-Za-z]\w*)\s*\)",
        r"(\1*\2-\2*\1)",
        text,
    )
    return sp.sympify(rendered, locals=locals_map)


def _validate_declared_commutator_relation(
    sp: Any,
    obligation: dict[str, Any],
    *,
    lhs: str,
    rhs: str,
    locals_map: dict[str, Any],
) -> dict[str, Any] | None:
    relations = obligation.get("relations")
    if not isinstance(relations, dict):
        return None
    normalized_lhs = _normalize_relation_key(lhs)
    if normalized_lhs not in {_normalize_relation_key(key) for key in relations}:
        return None
    matched_value = None
    for key, value in relations.items():
        if _normalize_relation_key(key) == normalized_lhs:
            matched_value = str(value)
            break
    if matched_value is None:
        return None
    residual = sp.simplify(
        sp.sympify(matched_value, locals=locals_map)
        - sp.sympify(rhs, locals=locals_map)
    )
    if residual == 0:
        return _passed("declared_commutator_relation_matches", {"relation": lhs})
    return _failed(
        "declared_commutator_relation_mismatch",
        {"relation": lhs, "declared": matched_value, "rhs": rhs},
    )


def _normalize_relation_key(value: str) -> str:
    return re.sub(r"\s+", "", value.replace("[", "comm(").replace("]", ")"))


def _safe_projects_path(repo_dir: Path, rel: str) -> Path | None:
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    if not candidate.parts or candidate.parts[0] != "projects":
        return None
    resolved = (repo_dir / candidate).resolve()
    projects_root = (repo_dir / "projects").resolve()
    try:
        resolved.relative_to(projects_root)
    except ValueError:
        return None
    return resolved


def _safe_projects_dir(repo_dir: Path, rel: str) -> Path | None:
    candidate = _safe_projects_path(repo_dir, rel)
    if candidate is not None:
        return candidate
    raw = Path(rel)
    if raw.is_absolute() or ".." in raw.parts:
        return None
    if not raw.parts or raw.parts[0] != "projects":
        return None
    resolved = (repo_dir / raw).resolve()
    projects_root = (repo_dir / "projects").resolve()
    try:
        resolved.relative_to(projects_root)
    except ValueError:
        return None
    return resolved


def _run_command(command: list[str], *, cwd: Path, timeout: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "failed",
            "reason": "timeout",
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
        }
    except OSError as exc:
        return {"status": "failed", "reason": "os_error", "stderr": str(exc)}
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "return_code": int(completed.returncode),
        "stdout": str(completed.stdout or ""),
        "stderr": str(completed.stderr or ""),
    }


def _timeout_seconds(obligation: dict[str, Any]) -> int:
    try:
        value = int(obligation.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        value = DEFAULT_TIMEOUT_SECONDS
    return max(1, min(value, 120))


def _normalize_dimension(value: Any) -> str:
    if isinstance(value, dict):
        return ",".join(f"{key}:{value[key]}" for key in sorted(value))
    return str(value).strip().replace(" ", "")


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _passed(reason: str, evidence: Any) -> dict[str, Any]:
    return {"status": "passed", "reason": reason, "evidence": evidence}


def _failed(reason: str, evidence: Any) -> dict[str, Any]:
    return {"status": "failed", "reason": reason, "evidence": evidence}


def _open(reason: str) -> dict[str, Any]:
    return {"status": "open", "reason": reason, "evidence": {}}


def _unknown(reason: str, evidence: Any) -> dict[str, Any]:
    return {"status": "unknown", "reason": reason, "evidence": evidence}
