"""
The single entry-admission contract for QuantEdge AI execution (P2-6).

"NO CLEAN RECONCILIATION = NO ENTRY" was only ever enforced when something
happened to install `ExecutionStartupGate` on the manager. That is a wiring
accident, not a contract: nothing named the rule, nothing said which submission
paths were bound by it, and nothing failed if a new path appeared beside them.
This module is that missing name.

It is a CONTRACT, not a composition. Importing it wires nothing, constructs no
manager, opens no socket, contacts no exchange and places no order. It provides:

  * `EntryAdmissionContract` -- the one interface a production composition must
    install: a synchronous, side-effect-free `admission_reason()` returning
    `None` to admit and a human-readable string to refuse.
  * `install_entry_admission` -- the one installer. Refuses to replace an
    installed contract, and refuses an object that does not implement one.
  * `CANONICAL_ENTRY_SUBMISSION_PATHS` -- the explicit manifest of production
    paths that MUST consult the contract before submitting.
  * `QUARANTINED_SUBMISSION_PATHS` -- the explicit manifest of paths that can
    submit but are NOT under the contract, each with the reason it is kept out
    of production reach instead of being brought under it.
  * `assert_entry_admission_contract_intact` -- a structural, source-level check
    that the two manifests still describe reality: every canonical path consults
    the contract, no quarantined path is reachable from the package root, and no
    NEW submission path has appeared anywhere in `quantedge/`.

The last item is the point. A rule that is only true because of today's import
graph silently stops being true; a rule with a failing test attached announces
it. The check fails closed: a path it cannot classify is a violation, not an
exemption.

Nothing here relaxes any existing refusal. The manager's own fail-closed
behaviour (an exception from the hook is a refusal, not an admission) is
unchanged, and a manager with no contract installed keeps its long-standing
library behaviour -- which is exactly why the structural assertion exists to
prove that the production composition, when it is finally written, cannot skip
the installation step without breaking a test.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

#: The one method name that constitutes the contract.
CONTRACT_METHOD = "admission_reason"

#: The attribute on `TradeLifecycleManager` the contract is installed into. The
#: manager owns the fail-closed calling convention around it
#: (`_entry_admission_reason`); this module owns who may fill it and with what.
MANAGER_GATE_ATTRIBUTE = "_entry_admission_gate"

#: The manager-side helper every canonical submission path must consult. Named
#: here so the structural check and the manifest cannot drift apart.
MANAGER_ADMISSION_HELPER = "_entry_admission_reason"


class EntryAdmissionError(RuntimeError):
    """An admission contract could not be installed honestly.

    Raised for a non-conforming contract object and for an attempt to replace an
    already-installed one. Never used to report a refusal: a refusal is a string
    from `admission_reason()`, and the difference matters -- one is a verdict,
    the other is a broken boundary.
    """


class EntryAdmissionContract:
    """The interface a production composition must install on the manager.

    Deliberately a tiny ABC-shaped base rather than a `typing.Protocol` alone:
    `install_entry_admission` accepts ANY object exposing a callable
    `admission_reason`, so existing gates and test doubles satisfy it without
    inheriting anything, while this class gives the contract a name that can be
    imported, documented and subclassed.

    Implementations must be:

      * SYNCHRONOUS -- it is called on the entry path, inside the manager's
        admission critical section. No I/O, no exchange call, no await.
      * SIDE-EFFECT FREE -- it answers a question; it must not mutate execution
        state, and it must not itself submit, cancel or persist anything.
      * FAIL-CLOSED -- when it cannot prove that an entry is safe it must return
        a reason, not `None`. Raising is also treated as a refusal by the
        manager, but returning the reason is what puts the WHY in the record.
    """

    def admission_reason(self) -> Optional[str]:            # pragma: no cover
        raise NotImplementedError(
            "an EntryAdmissionContract must answer admission_reason()"
        )


@dataclass(frozen=True)
class SubmissionPath:
    """One place in this repository that can put an order on the exchange.

    `module` is relative to the `quantedge` package root so the manifests read
    the same way as the quarantine banners in `execution/__init__.py`. `qualname`
    is resolved against the imported module, so a rename breaks the assertion
    instead of silently emptying it.
    """

    module: str
    qualname: str
    why: str


#: Production paths that MUST consult the admission contract before submitting.
#:
#: `execute_trade_setup` is the whole list on purpose: it is the ONE admission
#: boundary, and `_execute_admitted_trade_setup` -- the half that actually
#: submits -- is reachable only from it (asserted below). `MarketScannerOrchestrator`
#: does not appear because it does not submit: it calls `execute_trade_setup` and
#: therefore inherits the contract rather than needing its own copy of it.
CANONICAL_ENTRY_SUBMISSION_PATHS: Tuple[SubmissionPath, ...] = (
    SubmissionPath(
        module="execution.trade_lifecycle",
        qualname="TradeLifecycleManager.execute_trade_setup",
        why=(
            "the canonical production entry path: deterministic client order "
            "ids, protective bracket, single-trade account lock, durable "
            "lifecycle record, and the admission contract"
        ),
    ),
)

#: Paths that CAN submit but are NOT under the contract, and why each one is
#: kept out of production reach rather than brought under it.
QUARANTINED_SUBMISSION_PATHS: Tuple[SubmissionPath, ...] = (
    SubmissionPath(
        module="execution.execution_engine",
        qualname="LiveOrderExecutionService",
        why=(
            "orphan: no protective bracket, no single-trade lock, no "
            "reconciliation-alert block, no durable lifecycle record. Not "
            "re-exported from the package root"
        ),
    ),
    SubmissionPath(
        module="execution.multi_user_orchestrator",
        qualname="UserExecutionSession.execute_trade",
        why=(
            "P1-6: submits three legs under RANDOM client order ids and never "
            "consults the admission contract. Bringing it under the contract "
            "would mean re-identifying a path that must not become production, "
            "so it is quarantined intact instead. Not re-exported from the "
            "package root"
        ),
    ),
    SubmissionPath(
        module="execution.multi_user_orchestrator",
        qualname="MultiUserExecutionOrchestrator",
        why="P1-6: the only driver of the path above. Not re-exported",
    ),
)


def install_entry_admission(manager: Any, contract: Any) -> Callable[[], Optional[str]]:
    """Install `contract` as the one admission authority for `manager`.

    This is the single call a production composition must make. It is the only
    supported way to fill `TradeLifecycleManager._entry_admission_gate`, so the
    two things that must never happen quietly cannot happen quietly here:

      * Replacing an installed contract. A live contract may currently be
        REFUSING; swapping it for a fresh one would replace a block with a
        default-open verdict. Refused (`EntryAdmissionError`), never overwritten.
      * Installing something that is not a contract. A non-callable or missing
        `admission_reason` would leave the attribute filled and the boundary
        absent -- the worst combination, because it looks installed.

    Returns the bound hook that was installed, so a caller can assert identity
    without reaching into the manager's private attribute.
    """
    hook = getattr(contract, CONTRACT_METHOD, None)
    if hook is None or not callable(hook):
        raise EntryAdmissionError(
            f"{type(contract).__name__} does not implement the entry admission "
            f"contract: a callable {CONTRACT_METHOD}() is required"
        )
    existing = getattr(manager, MANAGER_GATE_ATTRIBUTE, None)
    if existing is not None:
        raise EntryAdmissionError(
            "an entry admission contract is already installed on this "
            "TradeLifecycleManager; refusing to replace it, because the "
            "installed one may currently be refusing entries"
        )
    setattr(manager, MANAGER_GATE_ATTRIBUTE, hook)
    return hook


# ── Structural assertion ──────────────────────────────────────────────────────

#: Method names that put an order on the exchange. `create_order` is the real
#: client method and `place_order` its long-standing alias; both are listed
#: because `execution_engine` resolves whichever exists via `getattr`.
SUBMISSION_METHOD_NAMES = ("place_order", "create_order")

#: Modules allowed to contain a submission call site, and what each one is.
#: Anything else is a new submission path and fails the assertion.
_ALLOWED_SUBMISSION_MODULES: Dict[str, str] = {
    "execution/trade_lifecycle.py": "canonical",
    "execution/execution_engine.py": "quarantined",
    "execution/multi_user_orchestrator.py": "quarantined",
    "execution/delta_client.py": "defines the client method itself",
}


def _package_root() -> Path:
    import quantedge
    return Path(quantedge.__file__).resolve().parent


def _production_files() -> List[Path]:
    """Every production module under `quantedge/`. Tests and scripts excluded."""
    return sorted(p for p in _package_root().rglob("*.py") if p.name != "__pycache__")


def _relative(path: Path) -> str:
    return path.relative_to(_package_root()).as_posix()


def _submission_call_names(tree: ast.AST) -> Set[str]:
    """Submission method names CALLED (or `getattr`-resolved) in `tree`.

    AST-based rather than text-based so that prose mentioning `place_order` in a
    docstring or comment -- `models.py` and `private_websocket.py` both do -- is
    not mistaken for a submission path, while the dynamic
    `getattr(client, "place_order", ...)` lookup in the quarantined
    `execution_engine` still is.
    """
    found: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name)
            else None
        )
        if name in SUBMISSION_METHOD_NAMES:
            found.add(str(name))
        if name == "getattr":
            for arg in node.args[1:]:
                if isinstance(arg, ast.Constant) and arg.value in SUBMISSION_METHOD_NAMES:
                    found.add(str(arg.value))
    return found


def _resolve(path: SubmissionPath) -> Any:
    """Import and resolve one manifest entry, or fail closed saying which."""
    import importlib

    dotted = f"quantedge.{path.module}"
    try:
        obj: Any = importlib.import_module(dotted)
    except Exception as exc:                                    # noqa: BLE001
        raise AssertionError(
            f"submission-path manifest names {dotted}, which cannot be "
            f"imported: {exc}. A quarantined module must stay INTACT, and a "
            f"canonical one must exist"
        ) from exc
    for part in path.qualname.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            raise AssertionError(
                f"submission-path manifest names {dotted}.{path.qualname}, which "
                f"no longer exists. If it was renamed, update the manifest -- an "
                f"empty manifest entry silently asserts nothing"
            )
    return obj


def _function_ast(func: Any) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(func)))


def _called_names(tree: ast.AST) -> Set[str]:
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def assert_entry_admission_contract_intact() -> Dict[str, Any]:
    """Prove the admission contract still binds every canonical submission path.

    Structural and source-level on purpose: it holds for a repository, not for
    one constructed object, so it keeps holding for a production composition that
    does not exist yet. Raises `AssertionError` with the offending detail; returns
    a small evidence dict on success so a test can show what it actually checked.

    Four things are proved:

      1. Every canonical path consults `_entry_admission_reason`.
      2. The submitting half of the canonical path is reachable only through the
         admitting half -- otherwise the boundary is one `await` away from being
         bypassed from inside the same class.
      3. Every quarantined path still EXISTS (quarantine is not deletion) and is
         NOT reachable from the `quantedge.execution` package root.
      4. No production module outside the two manifests contains a submission
         call site at all, and every module the manifests claim can submit really
         can. A new third path is a failure, not a surprise.
    """
    evidence: Dict[str, Any] = {}

    # 1. Canonical paths consult the contract.
    canonical_checked = []
    for path in CANONICAL_ENTRY_SUBMISSION_PATHS:
        func = _resolve(path)
        called = _called_names(_function_ast(func))
        assert MANAGER_ADMISSION_HELPER in called, (
            f"{path.module}.{path.qualname} is a canonical production submission "
            f"path but does not call {MANAGER_ADMISSION_HELPER}(): it can submit "
            f"an order without the admission contract being consulted"
        )
        canonical_checked.append(f"{path.module}.{path.qualname}")
    evidence["canonical_paths_consulting_contract"] = canonical_checked

    # 2. The submitting half is reachable only from the admitting half.
    from quantedge.execution import trade_lifecycle as _tl

    submitting_half = "_execute_admitted_trade_setup"
    tl_tree = ast.parse(Path(_tl.__file__).read_text(encoding="utf-8"))
    callers: List[str] = []
    for node in ast.walk(tl_tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == submitting_half:
            continue
        if submitting_half in _called_names(node):
            callers.append(node.name)
    assert callers == ["execute_trade_setup"], (
        f"{submitting_half}() -- the half that actually submits -- is called from "
        f"{sorted(callers)}. It must be reachable ONLY from execute_trade_setup(), "
        f"which is where the admission contract is consulted"
    )
    evidence["submitting_half_callers"] = callers

    # 3. Quarantined paths exist and are unreachable from the package root.
    from quantedge import execution as _pkg

    quarantined_checked = []
    for path in QUARANTINED_SUBMISSION_PATHS:
        _resolve(path)                              # intact, by its full path
        top = path.qualname.split(".")[0]
        assert top not in (getattr(_pkg, "__all__", ()) or ()), (
            f"{top} is quarantined but listed in quantedge.execution.__all__; a "
            f"package-root export is how an orphan becomes production wiring by "
            f"accident"
        )
        assert not hasattr(_pkg, top), (
            f"{top} is quarantined but reachable as quantedge.execution.{top}"
        )
        quarantined_checked.append(f"{path.module}.{path.qualname}")
    evidence["quarantined_paths_unreachable_from_root"] = quarantined_checked

    # 4. No unmanifested submission path anywhere in `quantedge/`.
    submitting_files: Dict[str, List[str]] = {}
    for file_path in _production_files():
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"))
        except SyntaxError as exc:                               # pragma: no cover
            raise AssertionError(f"{_relative(file_path)} does not parse: {exc}") from exc
        names = _submission_call_names(tree)
        if names:
            submitting_files[_relative(file_path)] = sorted(names)

    unmanifested = sorted(set(submitting_files) - set(_ALLOWED_SUBMISSION_MODULES))
    assert not unmanifested, (
        f"new submission call site(s) in {unmanifested}: every path that can put "
        f"an order on the exchange must be either canonical (and consult the "
        f"admission contract) or explicitly quarantined"
    )
    must_submit = {
        "execution/trade_lifecycle.py",
        "execution/execution_engine.py",
        "execution/multi_user_orchestrator.py",
    }
    missing = sorted(must_submit - set(submitting_files))
    assert not missing, (
        f"{missing} no longer contain a submission call site. The manifests "
        f"describe reality or they describe nothing -- update them"
    )
    evidence["submission_call_sites"] = submitting_files
    return evidence


__all__ = [
    "CONTRACT_METHOD",
    "MANAGER_GATE_ATTRIBUTE",
    "MANAGER_ADMISSION_HELPER",
    "SUBMISSION_METHOD_NAMES",
    "EntryAdmissionError",
    "EntryAdmissionContract",
    "SubmissionPath",
    "CANONICAL_ENTRY_SUBMISSION_PATHS",
    "QUARANTINED_SUBMISSION_PATHS",
    "install_entry_admission",
    "assert_entry_admission_contract_intact",
]
