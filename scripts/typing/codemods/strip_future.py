"""libcst codemod that removes ``from __future__ import annotations`` (Phase 1).

Without the future import, annotations are evaluated when the ``def``/``class`` (or module-level
assignment) executes. An annotation is therefore quoted only when one of its names is not bound at
that moment:

* a name bound only under ``if TYPE_CHECKING:`` (never bound at runtime);
* a module or class name that is defined later in the file (sequential binding);
* the name of the class whose body contains the annotation (self-reference).

Annotations inside function bodies are deferred except for names bound only under
``TYPE_CHECKING``; local variable annotations are never evaluated and stay unquoted.

Run from the repository root::

    python -m scripts.typing.codemods.strip_future src scripts testplan tests build.py
    python -m scripts.typing.codemods.strip_future --check src      # exit 1 if anything would change
"""

import argparse
import ast
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

FUTURE_MODULE: Final[str] = "__future__"
FUTURE_NAME: Final[str] = "annotations"
TYPE_CHECKING_NAME: Final[str] = "TYPE_CHECKING"
SKIP_DIR_NAMES: Final[frozenset[str]] = frozenset({"__pycache__", "build", "dist", "rawviewer_env", ".mypy_cache"})
# threading.Lock and threading.RLock are factory functions on CPython 3.10-3.12, not classes, so
# ``threading.Lock | None`` raises TypeError when evaluated. Such annotations must be quoted too.
NON_TYPE_THREADING_ATTRS: Final[frozenset[str]] = frozenset({"Lock", "RLock"})

ScopeKind = Literal["module", "class", "function"]
Position = tuple[int, int]


@dataclass(slots=True)
class Scope:
    kind: ScopeKind
    all_names: frozenset[str]
    bound: set[str] = field(default_factory=set)


@dataclass(slots=True)
class Report:
    changed: bool = False
    quoted: int = 0
    unquotable: list[str] = field(default_factory=list)


def is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == TYPE_CHECKING_NAME
    return isinstance(test, ast.Attribute) and test.attr == TYPE_CHECKING_NAME


def _store_names(node: ast.AST) -> Iterator[str]:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
            yield sub.id


def _simple_bindings(stmt: ast.stmt) -> Iterator[str]:
    """Names bound by a statement that has no nested statement bodies."""
    if isinstance(stmt, ast.Import):
        for alias in stmt.names:
            yield alias.asname or alias.name.split(".")[0]
    elif isinstance(stmt, ast.ImportFrom):
        for alias in stmt.names:
            if alias.name != "*":
                yield alias.asname or alias.name
    elif isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            yield from _store_names(target)
    elif isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
        yield from _store_names(stmt.target)
    elif isinstance(stmt, ast.Delete):
        return
    else:
        # Expression statements and returns can contain walrus targets.
        yield from _store_names(stmt)


def _child_bodies(stmt: ast.stmt) -> list[tuple[list[ast.stmt], bool]]:
    """Nested statement lists of a compound statement, with whether each runs under TYPE_CHECKING."""
    if isinstance(stmt, ast.If):
        tc = is_type_checking_test(stmt.test)
        return [(stmt.body, tc), (stmt.orelse, False)]
    if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
        return [(stmt.body, False), (stmt.orelse, False)]
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return [(stmt.body, False)]
    if isinstance(stmt, ast.Try):
        bodies: list[tuple[list[ast.stmt], bool]] = [(stmt.body, False)]
        bodies.extend((handler.body, False) for handler in stmt.handlers)
        bodies.append((stmt.orelse, False))
        bodies.append((stmt.finalbody, False))
        return bodies
    return []


def iter_bindings(stmts: Sequence[ast.stmt], in_tc: bool = False) -> Iterator[tuple[str, bool]]:
    """Yield (name, under_type_checking) for every name bound directly in this scope."""
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield stmt.name, in_tc
            continue
        for name in _simple_bindings(stmt):
            yield name, in_tc
        if isinstance(stmt, (ast.For, ast.AsyncFor)):
            for name in _store_names(stmt.target):
                yield name, in_tc
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                if item.optional_vars is not None:
                    for name in _store_names(item.optional_vars):
                        yield name, in_tc
        for body, tc in _child_bodies(stmt):
            yield from iter_bindings(body, in_tc or tc)
        if isinstance(stmt, ast.Try):
            for handler in stmt.handlers:
                if handler.name:
                    yield handler.name, in_tc


def module_tc_only_names(module: ast.Module) -> frozenset[str]:
    runtime: set[str] = set()
    type_only: set[str] = set()
    for name, tc in iter_bindings(module.body):
        (type_only if tc else runtime).add(name)
    return frozenset(type_only - runtime)


def annotation_nodes(args: ast.arguments) -> Iterator[ast.expr]:
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg is not None:
        every.append(args.vararg)
    if args.kwarg is not None:
        every.append(args.kwarg)
    for arg in every:
        if arg.annotation is not None:
            yield arg.annotation


class Analyzer:
    """Walks the module in execution order and collects annotations that must be quoted."""

    def __init__(self, module: ast.Module) -> None:
        self.tc_only: Final[frozenset[str]] = module_tc_only_names(module)
        self.module_all: Final[frozenset[str]] = frozenset(name for name, _ in iter_bindings(module.body))
        self.targets: set[Position] = set()
        self._stack: list[Scope] = [Scope("module", self.module_all)]

    @property
    def _current(self) -> Scope:
        return self._stack[-1]

    def run(self, body: Sequence[ast.stmt]) -> set[Position]:
        self._walk(body, in_tc=False)
        return self.targets

    def _needs_quote(self, expr: ast.expr) -> bool:
        scope = self._current
        module = self._stack[0]
        for node in ast.walk(expr):
            if not isinstance(node, (ast.Name, ast.Attribute)):
                continue
            if isinstance(node, ast.Attribute):
                if (
                    node.attr in NON_TYPE_THREADING_ATTRS
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "threading"
                ):
                    return True
                continue
            name = node.id
            if name in self.tc_only:
                return True
            if scope.kind == "class":
                if name in (scope.all_names | module.all_names) - (scope.bound | module.bound):
                    return True
            elif name in scope.all_names and name not in scope.bound:
                return True
        return False

    def _check(self, expr: ast.expr) -> None:
        if self._needs_quote(expr):
            self.targets.add((expr.lineno, expr.col_offset))

    def _check_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for ann in annotation_nodes(node.args):
            self._check(ann)
        if node.returns is not None:
            self._check(node.returns)

    def _bind(self, names: Iterable[str], in_tc: bool) -> None:
        if not in_tc:
            self._current.bound.update(names)

    def _walk(self, stmts: Sequence[ast.stmt], in_tc: bool) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._check_signature(stmt)
                body_scope = Scope("function", frozenset(name for name, _ in iter_bindings(stmt.body)))
                self._stack.append(body_scope)
                self._walk(stmt.body, in_tc=False)
                self._stack.pop()
                self._bind([stmt.name], in_tc)
            elif isinstance(stmt, ast.ClassDef):
                class_scope = Scope("class", frozenset(name for name, _ in iter_bindings(stmt.body)))
                self._stack.append(class_scope)
                self._walk(stmt.body, in_tc=False)
                self._stack.pop()
                self._bind([stmt.name], in_tc)
            elif isinstance(stmt, ast.AnnAssign):
                if self._current.kind != "function":
                    self._check(stmt.annotation)
                self._bind(_store_names(stmt.target), in_tc)
            elif isinstance(stmt, ast.If):
                self._walk(stmt.body, in_tc or is_type_checking_test(stmt.test))
                self._walk(stmt.orelse, in_tc)
            elif isinstance(stmt, ast.Try):
                self._walk(stmt.body, in_tc)
                for handler in stmt.handlers:
                    if handler.name:
                        self._bind([handler.name], in_tc)
                    self._walk(handler.body, in_tc)
                self._walk(stmt.orelse, in_tc)
                self._walk(stmt.finalbody, in_tc)
            elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
                if isinstance(stmt, (ast.For, ast.AsyncFor)):
                    self._bind(_store_names(stmt.target), in_tc)
                if isinstance(stmt, (ast.With, ast.AsyncWith)):
                    for item in stmt.items:
                        if item.optional_vars is not None:
                            self._bind(_store_names(item.optional_vars), in_tc)
                for body, _ in _child_bodies(stmt):
                    self._walk(body, in_tc)
            else:
                self._bind(_simple_bindings(stmt), in_tc)


class _Quoter(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, targets: set[Position], report: Report) -> None:
        super().__init__()
        self._targets = targets
        self._report = report

    def leave_Annotation(self, original: cst.Annotation, updated: cst.Annotation) -> cst.Annotation:
        start = self.get_metadata(PositionProvider, original.annotation).start
        if (start.line, start.column) not in self._targets:
            return updated
        code = cst.Module(body=[]).code_for_node(updated.annotation)
        if "\n" in code or "\r" in code:
            self._report.unquotable.append(f"{start.line}:{start.column}")
            return updated
        if '"' in code:
            if "'" in code:
                self._report.unquotable.append(f"{start.line}:{start.column}")
                return updated
            quoted = f"'{code}'"
        else:
            quoted = f'"{code}"'
        self._report.quoted += 1
        return updated.with_changes(annotation=cst.SimpleString(quoted))


def is_future_annotations(stmt: cst.BaseSmallStatement) -> bool:
    if not isinstance(stmt, cst.ImportFrom) or stmt.relative:
        return False
    if not isinstance(stmt.module, cst.Name) or stmt.module.value != FUTURE_MODULE:
        return False
    if isinstance(stmt.names, cst.ImportStar):
        return False
    return any(isinstance(alias.name, cst.Name) and alias.name.value == FUTURE_NAME for alias in stmt.names)


class _FutureRemover(cst.CSTTransformer):
    def __init__(self, report: Report) -> None:
        super().__init__()
        self._report = report

    def leave_SimpleStatementLine(
        self, original: cst.SimpleStatementLine, updated: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine | cst.RemovalSentinel:
        kept = [stmt for stmt in updated.body if not is_future_annotations(stmt)]
        if len(kept) == len(updated.body):
            return updated
        self._report.changed = True
        if not kept:
            return cst.RemovalSentinel.REMOVE
        return updated.with_changes(body=kept)


def rewrite_source(source: str) -> tuple[str, Report]:
    report = Report()
    module = cst.parse_module(source)
    if not any(
        isinstance(line, cst.SimpleStatementLine) and any(is_future_annotations(stmt) for stmt in line.body)
        for line in module.body
    ):
        return source, report
    tree = ast.parse(source)
    targets = Analyzer(tree).run(tree.body)
    wrapper = MetadataWrapper(module)
    quoted = wrapper.visit(_Quoter(targets, report))
    stripped = quoted.visit(_FutureRemover(report))
    if not isinstance(stripped, cst.Module):  # pragma: no cover - libcst returns Module for Module input
        raise TypeError("unexpected libcst result")
    return stripped.code, report


def iter_targets(paths: Sequence[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            yield path
        elif path.is_dir():
            for found in sorted(path.rglob("*.py")):
                if not SKIP_DIR_NAMES.intersection(found.relative_to(path).parts):
                    yield found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Remove the __future__ annotations import (libcst).")
    parser.add_argument("paths", nargs="+", help="files or directories to rewrite")
    parser.add_argument("--check", action="store_true", help="report only; exit 1 if any file would change")
    args = parser.parse_args(argv)

    changed_files = 0
    total_quoted = 0
    for path in iter_targets([Path(p) for p in args.paths]):
        raw = path.read_bytes().decode("utf-8")
        new_source, report = rewrite_source(raw)
        if not report.changed:
            continue
        changed_files += 1
        total_quoted += report.quoted
        if report.unquotable:
            sys.stderr.write(f"strip_future: {path}: needs manual quoting at {', '.join(report.unquotable)}\n")
        if not args.check:
            path.write_bytes(new_source.encode("utf-8"))
    mode = "would change" if args.check else "changed"
    sys.stdout.write(f"strip_future: {mode} {changed_files} file(s); quoted {total_quoted} annotation(s)\n")
    return 1 if args.check and changed_files else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
