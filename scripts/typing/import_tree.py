"""Build the internal import graph of src/ and write bottom-up typing layers.

Layer 0 holds modules with no internal dependencies (leaves). Layer N holds modules whose
internal dependencies all sit in layers below N. Strongly connected components (import
cycles) are collapsed into one work unit, because they cannot be ordered.

Run from the repo root:
    python -m scripts.typing.import_tree                 # print summary
    python -m scripts.typing.import_tree --write         # write docs/typing/import_layers.md + JSON

TYPE_CHECKING-only imports and imports inside functions are included: both can cause
mypy to read a module, so both count as edges for planning.
"""

import argparse
import ast
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SRC: Final[Path] = Path("src")
OUT_DIR: Final[Path] = Path("docs") / "typing"


@dataclass(frozen=True, slots=True)
class Unit:
    """One work unit: a single module, or a strongly connected component (import cycle)."""

    level: int
    modules: tuple[str, ...]

    @property
    def is_cycle(self) -> bool:
        return len(self.modules) > 1


def module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    parts = rel.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def discover(src: Path) -> dict[str, Path]:
    return {module_name(p): p for p in src.rglob("*.py") if "__pycache__" not in p.parts}


def _package_of(name: str, path: Path) -> str:
    return name if path.name == "__init__.py" else name.rpartition(".")[0]


def _resolve_from(package: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    parts = package.split(".") if package else []
    keep = len(parts) - (node.level - 1)
    base = parts[:keep] if keep > 0 else []
    if node.module:
        base = [*base, node.module]
    return ".".join(base)


def _type_checking_nodes(tree: ast.Module) -> set[int]:
    excluded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            excluded.update(id(child) for child in ast.walk(node))
    return excluded


def _candidates(node: ast.AST, package: str) -> Iterator[str]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name
    elif isinstance(node, ast.ImportFrom):
        target = _resolve_from(package, node)
        yield target
        for alias in node.names:
            yield f"{target}.{alias.name}" if target else alias.name


def _longest_known(name: str, known: set[str]) -> str | None:
    current = name
    while current:
        if current in known:
            return current
        if "." not in current:
            return None
        current = current.rpartition(".")[0]
    return None


def build_graph(modules: dict[str, Path]) -> dict[str, set[str]]:
    known = set(modules)
    graph: dict[str, set[str]] = defaultdict(set)
    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        excluded = _type_checking_nodes(tree)
        package = _package_of(name, path)
        for node in ast.walk(tree):
            if id(node) in excluded:
                continue
            for candidate in _candidates(node, package):
                target = _longest_known(candidate, known)
                if target is not None and target != name:
                    graph[name].add(target)
    return graph


def strongly_connected(graph: dict[str, set[str]], nodes: Iterable[str]) -> list[list[str]]:
    """Tarjan's algorithm, iterative so deep import chains cannot exhaust the recursion limit."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work: list[tuple[str, Iterator[str]]] = [(root, iter(sorted(graph.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(sorted(component))
    return components


def layer_units(graph: dict[str, set[str]], modules: Iterable[str]) -> list[Unit]:
    nodes = sorted(set(modules))
    components = strongly_connected(graph, nodes)
    owner = {m: i for i, comp in enumerate(components) for m in comp}
    deps: dict[int, set[int]] = defaultdict(set)
    for src_mod, targets in graph.items():
        for tgt in targets:
            if owner[src_mod] != owner[tgt]:
                deps[owner[src_mod]].add(owner[tgt])
    level: dict[int, int] = {}

    def level_of(comp: int) -> int:
        # Iterative post-order: components form a DAG after collapsing cycles.
        order: list[int] = []
        todo = [comp]
        seen: set[int] = set()
        while todo:
            c = todo.pop()
            if c in level or c in seen:
                continue
            seen.add(c)
            order.append(c)
            todo.extend(d for d in deps[c] if d not in level)
        for c in reversed(order):
            level[c] = 1 + max((level[d] for d in deps[c]), default=-1)
        return level[comp]

    units = [Unit(level=level_of(i), modules=tuple(comp)) for i, comp in enumerate(components)]
    return sorted(units, key=lambda u: (u.level, u.modules[0]))


def summarise(units: list[Unit]) -> str:
    per_level = Counter(u.level for u in units)
    lines = [f"work units: {len(units)} ({sum(1 for u in units if u.is_cycle)} cycles)"]
    for lvl in sorted(per_level):
        members = sum(len(u.modules) for u in units if u.level == lvl)
        lines.append(f"  layer {lvl:>2}: {per_level[lvl]:>3} units, {members:>3} modules")
    return "\n".join(lines)


def render_markdown(units: list[Unit], fan_in: Counter[str]) -> str:
    out: list[str] = [
        "# Bottom-up typing layers (generated)",
        "",
        "Generated by `python -m scripts.typing.import_tree --write`. Do not edit by hand.",
        "",
        "Rule: finish every unit in layer N before starting layer N+1. Units inside one layer",
        "are independent and may run in parallel. A cycle unit is typed as one piece.",
        "",
        "```text",
        summarise(units),
        "```",
        "",
        "## Most imported modules (fan-in)",
        "",
    ]
    out += [f"- `{name}`: {count}" for name, count in fan_in.most_common(15)]
    out += ["", "## Units by layer", ""]
    for unit in units:
        tag = " (cycle)" if unit.is_cycle else ""
        mods = ", ".join(f"`{m}`" for m in unit.modules)
        out.append(f"- L{unit.level}{tag}: {mods}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bottom-up typing layers for src/.")
    parser.add_argument("--write", action="store_true", help="write markdown and JSON under docs/typing/")
    args = parser.parse_args(argv)

    modules = discover(SRC)
    graph = build_graph(modules)
    units = layer_units(graph, modules)
    fan_in: Counter[str] = Counter(t for targets in graph.values() for t in targets)
    print(summarise(units))
    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "import_layers.md").write_text(render_markdown(units, fan_in), encoding="utf-8")
        payload = [{"level": u.level, "modules": list(u.modules)} for u in units]
        (OUT_DIR / "import_layers.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
        sys.stdout.write(f"wrote {OUT_DIR / 'import_layers.md'} and .json\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
