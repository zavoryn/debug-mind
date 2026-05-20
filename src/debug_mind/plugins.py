"""Plugin system — allows third-party extensions to register custom tools, embeddings, and rerankers.

Plugins are Python packages or modules that expose a setup(app) function.
Set DEBUG_MIND_PLUGINS env var to comma-separated paths to load them.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any


class PluginRegistry:
    """Collects registrations from loaded plugins."""

    def __init__(self):
        self.tools: list[dict[str, Any]] = []
        self.tool_executors: dict[str, Any] = {}
        self.embedding_fns: dict[str, Any] = {}
        self.reranker_fns: dict[str, Any] = {}

    def register_tool(self, name: str, schema: dict[str, Any], executor):
        """Register a custom tool with Anthropic schema and executor function."""
        self.tools.append(schema)
        self.tool_executors[name] = executor

    def register_embedding(self, name: str, fn):
        """Register a custom embedding function."""
        self.embedding_fns[name] = fn

    def register_reranker(self, name: str, fn):
        """Register a custom reranker factory."""
        self.reranker_fns[name] = fn


def discover_plugins() -> tuple[list[str], PluginRegistry]:
    """Load plugins from DEBUG_MIND_PLUGINS env var and return (loaded_names, registry)."""
    registry = PluginRegistry()
    plugin_paths = os.environ.get("DEBUG_MIND_PLUGINS", "")
    if not plugin_paths.strip():
        return [], registry

    loaded = []
    for plugin_path in plugin_paths.split(","):
        plugin_path = plugin_path.strip()
        if not plugin_path:
            continue
        try:
            _load_plugin(Path(plugin_path), registry)
            loaded.append(plugin_path)
        except Exception as e:
            print(f"Warning: failed to load plugin {plugin_path}: {e}", file=sys.stderr)

    return loaded, registry


def _load_plugin(plugin_path: Path, registry: PluginRegistry) -> None:
    """Load a single plugin by file path."""
    if not plugin_path.exists():
        raise FileNotFoundError(f"Plugin not found: {plugin_path}")

    # Support both .py files and package directories
    if plugin_path.is_file() and plugin_path.suffix == ".py":
        spec = importlib.util.spec_from_file_location(
            f"debug_mind_plugin_{plugin_path.stem}", str(plugin_path)
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load plugin spec: {plugin_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    elif plugin_path.is_dir():
        init = plugin_path / "__init__.py"
        if not init.exists():
            raise FileNotFoundError(f"Plugin package missing __init__.py: {plugin_path}")
        spec = importlib.util.spec_from_file_location(
            f"debug_mind_plugin_{plugin_path.name}", str(init)
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load plugin package: {plugin_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        raise ValueError(f"Plugin must be .py file or package directory: {plugin_path}")

    if hasattr(module, "setup"):
        module.setup(registry)
    else:
        raise AttributeError(f"Plugin {plugin_path} has no setup(app) function")
