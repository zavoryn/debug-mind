"""tree-sitter code parser — AST-level code understanding.

Optional dependency: pip install tree-sitter tree-sitter-python
Falls back gracefully if tree-sitter is not installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Language-specific grammar imports — attempted on demand, not at module level.
# tree-sitter language packages follow the pattern: tree-sitter-{lang}
_GRAMMAR_MAP: dict[str, str] = {
    ".py": "tree_sitter_python",
    ".js": "tree_sitter_javascript",
    ".ts": "tree_sitter_typescript",
    ".tsx": "tree_sitter_tsx",
    ".java": "tree_sitter_java",
    ".go": "tree_sitter_go",
}


def _get_language(path: Path):
    """Resolve tree-sitter Language for a file path, or None."""
    try:
        from tree_sitter import Language, Parser  # noqa: F401
    except ImportError:
        return None, None

    suffix = path.suffix.lower() if path.suffix else ""
    grammar_module = _GRAMMAR_MAP.get(suffix)
    if grammar_module is None:
        return None, None

    try:
        mod = __import__(grammar_module, fromlist=["language"])
        lang = Language(mod.language())
        parser = Parser(lang)
        return lang, parser
    except (ImportError, AttributeError):
        return None, None


def parse_symbols(project_path: str, file_path: str) -> dict[str, Any]:
    """Parse a source file and return its top-level symbols (functions, classes).

    Returns {"symbols": [...], "error": str} if parsing fails.
    """
    project = Path(project_path)
    file = (project / file_path).resolve()
    if not file.exists():
        return {"error": f"File not found: {file_path}"}

    try:
        source = file.read_text(encoding="utf-8")
    except Exception as e:
        return {"error": f"Cannot read file: {e}"}

    _, parser = _get_language(file)
    if parser is None:
        return {
            "symbols": [],
            "error": "tree-sitter not available for this file type. Install with: pip install tree-sitter tree-sitter-python",
        }

    tree = parser.parse(source.encode("utf-8"))
    root = tree.root_node

    symbols: list[dict[str, Any]] = []
    _collect_symbols(root, source, symbols)

    return {"file": file_path, "symbols": symbols, "language": file.suffix.lstrip(".")}


def _collect_symbols(node, source: str, symbols: list[dict[str, Any]], depth: int = 0):
    """Walk AST and collect function/class definitions."""
    if depth > 3:
        return

    if node.type in ("function_definition", "function_declaration", "method_definition"):
        name_node = node.child_by_field_name("name")
        if name_node:
            name = source[name_node.start_byte : name_node.end_byte]
            symbols.append(
                {
                    "kind": "function",
                    "name": name,
                    "line": node.start_point[0] + 1,
                    "signature": _extract_signature(node, source),
                }
            )
    elif node.type in ("class_definition", "class_declaration"):
        name_node = node.child_by_field_name("name")
        if name_node:
            name = source[name_node.start_byte : name_node.end_byte]
            symbols.append(
                {
                    "kind": "class",
                    "name": name,
                    "line": node.start_point[0] + 1,
                }
            )

    for child in node.children:
        _collect_symbols(child, source, symbols, depth + 1 if node.type == "block" else depth)


def _extract_signature(node, source: str) -> str:
    """Extract the function signature text."""
    params = node.child_by_field_name("parameters")
    if params:
        return source[node.start_byte : params.end_byte]
    return source[node.start_byte : node.end_byte].split("\n")[0].strip()
