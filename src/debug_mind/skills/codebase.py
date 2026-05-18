"""Codebase skill — real code search, file reading, and project understanding.

This is what makes DebugMind actually useful in production: the agent can
grep your real codebase, read source files, and understand project structure.

Uses subprocess to call ripgrep (rg) or falls back to grep.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def search_code(pattern: str, project_path: str, file_type: str = "", max_results: int = 30) -> dict:
    """Search codebase for a pattern using ripgrep or grep.

    Returns matched lines with file:line_number:content format.
    """
    cmd = _build_grep_cmd(pattern, project_path, file_type, max_results)
    if not cmd:
        return {"error": "No grep tool available (install ripgrep for best results)"}

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_path,
        )
    except subprocess.TimeoutExpired:
        return {"found": 0, "pattern": pattern, "matches": [], "error": "Search timed out"}
    except Exception as e:
        return {"found": 0, "pattern": pattern, "matches": [], "error": str(e)}

    output = result.stdout.strip()
    if not output:
        return {"found": 0, "pattern": pattern, "matches": []}

    lines = output.split("\n")[:max_results]
    matches = []
    for line in lines:
        # ripgrep format: file:line:content  or  file:line:col:content
        parts = line.split(":", 2)
        if len(parts) >= 3:
            matches.append({
                "file": parts[0],
                "line_number": parts[1],
                "content": parts[2].strip()[:200],
            })

    return {
        "found": len(matches),
        "pattern": pattern,
        "matches": matches,
    }


def read_file(file_path: str, project_path: str, start_line: int = 0, end_line: int = 100) -> dict:
    """Read a source file (or a range of lines) from the project."""
    # Security: only allow reading files within the project
    full_path = Path(project_path) / file_path
    try:
        full_path = full_path.resolve(strict=True)
        project_root = Path(project_path).resolve(strict=True)
        if not str(full_path).startswith(str(project_root)):
            return {"error": "Access denied: file is outside the project directory"}
    except FileNotFoundError:
        return {"error": f"File not found: {file_path}"}

    try:
        all_lines = full_path.read_text(encoding="utf-8", errors="replace").split("\n")
    except Exception as e:
        return {"error": str(e)}

    total = len(all_lines)
    start = max(0, start_line)
    end = min(total, end_line)

    selected = all_lines[start:end]
    numbered = [f"{start + i + 1:4d} | {line}" for i, line in enumerate(selected)]

    return {
        "file": file_path,
        "total_lines": total,
        "showing": f"{start + 1}-{end}",
        "content": "\n".join(numbered),
    }


def list_project_structure(project_path: str, depth: int = 3, max_entries: int = 80) -> dict:
    """Get a tree view of the project structure.

    Skips common non-essential directories (node_modules, .git, __pycache__, etc.)
    """
    skip_dirs = {
        ".git", "__pycache__", "node_modules", ".venv", "venv", ".idea",
        ".gradle", "target", "build", "dist", ".next", ".nuxt",
        "chroma", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    }
    skip_exts = {".pyc", ".class", ".jar", ".war", ".pyo"}

    entries = []
    project = Path(project_path)

    try:
        for root, dirs, files in os.walk(project):
            rel_root = Path(root).relative_to(project)
            level = len(rel_root.parts)

            if level > depth:
                dirs.clear()
                continue

            # Skip unwanted dirs
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
            dirs.sort()

            if rel_root == Path("."):
                prefix = ""
            else:
                prefix = "  " * (level - 1) + "├── "
                entries.append(f"{prefix}{rel_root.name}/")
                if len(entries) >= max_entries:
                    break

            for f in sorted(files):
                if Path(f).suffix in skip_exts or f.startswith("."):
                    continue
                prefix = "  " * level + "├── "
                entries.append(f"{prefix}{f}")
                if len(entries) >= max_entries:
                    break

            if len(entries) >= max_entries:
                entries.append("  ... (truncated)")
                break
    except Exception as e:
        return {"error": str(e)}

    return {
        "project": str(project),
        "structure": "\n".join(entries),
    }


def _build_grep_cmd(pattern: str, project_path: str, file_type: str, max_results: int) -> list[str] | None:
    """Build grep command — prefers ripgrep, falls back to GNU grep."""
    if _has_cmd("rg"):
        cmd = ["rg", "--no-heading", "--line-number", "--color=never", f"--max-count={max_results}"]
        if file_type:
            cmd.extend(["--type", file_type])
        cmd.extend([pattern, project_path])
        return cmd

    if _has_cmd("grep"):
        cmd = ["grep", "-rn", "--color=never"]
        if file_type:
            cmd.extend(["--include", f"*.{file_type}"])
        cmd.extend(["-m", str(max_results), pattern, project_path])
        return cmd

    if os.name == "nt" and _has_cmd("findstr"):
        # Windows: findstr /s = recursive, /n = line numbers
        cmd = ["findstr", "/n", "/s", pattern]
        if file_type:
            cmd.append(f"*.{file_type}")
        else:
            cmd.append("*.*")
        return cmd

    return None


def _has_cmd(cmd: str) -> bool:
    try:
        subprocess.run(
            ["where" if os.name == "nt" else "which", cmd],
            capture_output=True,
            timeout=5,
        )
        return True
    except Exception:
        return False
