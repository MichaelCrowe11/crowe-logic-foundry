"""
Tool Registry — Auto-discovery and decorator-based tool registration.

Eliminates manual import maintenance. Tools self-register using the @tool decorator.
"""

import functools
import inspect
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any, Set, Union
import logging
import sys

logger = logging.getLogger(__name__)

# Python 3.9 compatibility
try:
    from types import UnionType
except ImportError:
    UnionType = type(Union[int, str])


@dataclass
class ToolMeta:
    """Metadata for a registered tool."""
    name: str
    fn: Callable
    category: str
    description: str
    parameters: Dict[str, Any]
    returns: str
    is_async: bool
    tags: Set[str] = field(default_factory=set)
    cache_ttl: Optional[int] = None  # seconds, None = no cache
    
    def to_openai_schema(self) -> Dict:
        """Convert to OpenAI function calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }
    
    def to_azure_schema(self) -> Dict:
        """Convert to Azure AI Agent Service schema."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def _normalize_type(annotation) -> Dict[str, Any]:
    """Normalize type annotation to JSON schema type."""
    if annotation == inspect.Parameter.empty:
        return {"type": "string"}
    
    # Handle Union types (Optional[T] is Union[T, None])
    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", None)
    
    if origin is Union or (sys.version_info >= (3, 10) and origin is UnionType):
        # Filter out NoneType
        non_none = [arg for arg in args if arg != type(None)]
        if len(non_none) == 1:
            return _normalize_type(non_none[0])
        # Multiple non-None types - use string as fallback
        return {"type": "string", "description": f"Union[{', '.join(str(a) for a in non_none)}]"}
    
    # Direct type mappings
    if annotation == int:
        return {"type": "integer"}
    elif annotation == float:
        return {"type": "number"}
    elif annotation == bool:
        return {"type": "boolean"}
    elif annotation == str:
        return {"type": "string"}
    elif annotation == dict:
        return {"type": "object"}
    elif annotation == list:
        return {"type": "array"}
    elif origin is dict:
        return {"type": "object"}
    elif origin is list:
        return {"type": "array"}
    
    return {"type": "string"}


def _extract_parameters(fn: Callable) -> Dict[str, Any]:
    """Extract parameter schema from function signature."""
    sig = inspect.signature(fn)
    properties = {}
    required = []
    
    for name, param in sig.parameters.items():
        if name == "self":
            continue
            
        param_schema = _normalize_type(param.annotation)
        
        # Extract default value
        if param.default != inspect.Parameter.empty:
            if param.default is not None:
                param_schema["default"] = param.default
        else:
            required.append(name)
            
        # Extract description from docstring
        doc = inspect.getdoc(fn) or ""
        for line in doc.split("\n"):
            stripped = line.strip()
            if stripped.startswith(f":param {name}:"):
                desc = stripped.split(":param", 1)[1].split(":", 1)
                if len(desc) > 1:
                    param_schema["description"] = desc[1].strip()
                break
            elif stripped.startswith(f":param {name} "):
                desc = stripped.split(f":param {name} ", 1)
                if len(desc) > 1:
                    param_schema["description"] = desc[1].strip()
                break
        
        properties[name] = param_schema
    
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _extract_description(fn: Callable) -> str:
    """Extract main description from docstring."""
    doc = inspect.getdoc(fn) or ""
    lines = []
    for line in doc.split("\n"):
        stripped = line.strip()
        if stripped.startswith(":"):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _extract_return_type(fn: Callable) -> str:
    """Extract return type annotation or rtype from docstring."""
    if fn.__annotations__.get("return"):
        return str(fn.__annotations__["return"])
    doc = inspect.getdoc(fn) or ""
    for line in doc.split("\n"):
        if ":rtype:" in line:
            return line.split(":rtype:", 1)[1].strip()
    return "Any"


class ToolRegistry:
    """Central registry for all tools."""
    
    def __init__(self):
        self._tools: Dict[str, ToolMeta] = {}
        self._by_category: Dict[str, Set[str]] = {}
        self._by_tag: Dict[str, Set[str]] = {}
        
    def register(
        self,
        fn: Callable,
        category: str = "general",
        cache_ttl: Optional[int] = None,
        tags: Optional[Set[str]] = None,
    ) -> ToolMeta:
        """Register a tool function."""
        name = fn.__name__
        
        if name in self._tools:
            logger.warning(f"Tool {name} already registered, overwriting")
            
        meta = ToolMeta(
            name=name,
            fn=fn,
            category=category,
            description=_extract_description(fn),
            parameters=_extract_parameters(fn),
            returns=_extract_return_type(fn),
            is_async=inspect.iscoroutinefunction(fn),
            tags=tags or set(),
            cache_ttl=cache_ttl,
        )
        
        self._tools[name] = meta
        
        # Index by category
        if category not in self._by_category:
            self._by_category[category] = set()
        self._by_category[category].add(name)
        
        # Index by tags
        for tag in (tags or set()):
            if tag not in self._by_tag:
                self._by_tag[tag] = set()
            self._by_tag[tag].add(name)
            
        logger.debug(f"Registered tool: {name} ({category})")
        return meta
    
    def get(self, name: str) -> Optional[ToolMeta]:
        """Get tool metadata by name."""
        return self._tools.get(name)
    
    def list_all(self) -> List[ToolMeta]:
        """List all registered tools."""
        return list(self._tools.values())
    
    def by_category(self, category: str) -> List[ToolMeta]:
        """Get tools by category."""
        names = self._by_category.get(category, set())
        return [self._tools[n] for n in names if n in self._tools]
    
    def by_tag(self, tag: str) -> List[ToolMeta]:
        """Get tools by tag."""
        names = self._by_tag.get(tag, set())
        return [self._tools[n] for n in names if n in self._tools]
    
    def search(self, query: str) -> List[ToolMeta]:
        """Search tools by name or description."""
        query_lower = query.lower()
        results = []
        for meta in self._tools.values():
            if (query_lower in meta.name.lower() or 
                query_lower in meta.description.lower()):
                results.append(meta)
        return results
    
    def call(self, name: str, **kwargs) -> Any:
        """Execute a tool by name."""
        meta = self.get(name)
        if not meta:
            raise ValueError(f"Tool not found: {name}")
        return meta.fn(**kwargs)
    
    @property
    def user_functions(self) -> Set[Callable]:
        """Get all registered functions (for backward compatibility)."""
        return {meta.fn for meta in self._tools.values()}
    
    def get_stats(self) -> Dict:
        """Get registry statistics."""
        return {
            "total_tools": len(self._tools),
            "categories": {
                cat: len(tools) 
                for cat, tools in self._by_category.items()
            },
            "tags": {
                tag: len(tools) 
                for tag, tools in self._by_tag.items()
            },
            "async_tools": sum(1 for t in self._tools.values() if t.is_async),
            "cached_tools": sum(1 for t in self._tools.values() if t.cache_ttl),
        }


# Global registry instance
_registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    """Get the global tool registry."""
    return _registry


def tool(
    category: str = "general",
    cache_ttl: Optional[int] = None,
    tags: Optional[Set[str]] = None,
):
    """
    Decorator to register a function as a tool.
    
    Usage:
        @tool(category="filesystem")
        def read_file(file_path: str) -> str: ...
        
        @tool(category="quantum", cache_ttl=3600, tags={"compute-intensive"})
        def run_quantum_circuit(code: str) -> str: ...
    """
    def decorator(fn: Callable) -> Callable:
        _registry.register(fn, category=category, cache_ttl=cache_ttl, tags=tags)
        
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)
        
        # Attach metadata to function for introspection
        wrapper._tool_meta = _registry.get(fn.__name__)
        return wrapper
    return decorator


def auto_discover_tools(package_path: str = None) -> int:
    """
    Auto-discover and register all tools from the tools directory.
    
    Scans for modules and registers functions decorated with @tool.
    Returns the number of tools discovered.
    """
    import os
    import importlib.util
    
    if package_path is None:
        package_path = os.path.dirname(os.path.abspath(__file__))
    
    discovered = 0
    
    for filename in os.listdir(package_path):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
            
        module_name = f"tools.{filename[:-3]}"
        filepath = os.path.join(package_path, filename)
        
        try:
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            
            # Count tools registered from this module
            count_before = len(_registry._tools)
            # The module import triggers @tool decorators
            count_after = len(_registry._tools)
            discovered += (count_after - count_before)
            
        except Exception as e:
            logger.warning(f"Failed to load module {module_name}: {e}")
    
    return discovered


# QC Test helper for Stage 1
def run_stage1_qc() -> Dict:
    """Quality control checks for Stage 1."""
    checks = {}
    
    # QC1: Registry functions correctly
    try:
        reg = ToolRegistry()
        checks["registry_creation"] = "PASS"
    except Exception as e:
        checks["registry_creation"] = f"FAIL: {e}"
    
    # QC2: Manual registration works
    try:
        def _test_fn(x: int) -> str:
            """Test function."""
            return str(x)
        
        reg.register(_test_fn, category="test")
        meta = reg.get("_test_fn")
        checks["manual_registration"] = "PASS" if meta and meta.category == "test" else "FAIL"
    except Exception as e:
        checks["manual_registration"] = f"FAIL: {e}"
    
    # QC3: Schema extraction works
    try:
        def _schema_test(a: int, b: str = "default", c: Optional[float] = None) -> dict:
            """Test with params.
            
            :param a: An integer
            :param b: A string
            :param c: Optional float
            :return: A dict
            """
            return {"a": a, "b": b}
        
        reg.register(_schema_test, category="test", tags={"demo"})
        meta = reg.get("_schema_test")
        schema_valid = (
            meta.parameters["type"] == "object" and
            "a" in meta.parameters["properties"] and
            "b" in meta.parameters["properties"] and
            "a" in meta.parameters["required"] and
            "b" not in meta.parameters["required"] and
            meta.parameters["properties"]["a"]["type"] == "integer"
        )
        checks["schema_extraction"] = "PASS" if schema_valid else "FAIL"
    except Exception as e:
        checks["schema_extraction"] = f"FAIL: {e}"
    
    # QC4: Tool execution works
    try:
        def _call_test(x: int, y: int) -> int:
            """Add two numbers."""
            return x + y
        
        reg.register(_call_test, category="test", cache_ttl=3600)
        result = reg.call("_call_test", x=2, y=3)
        checks["tool_execution"] = "PASS" if result == 5 else f"FAIL: got {result}"
    except Exception as e:
        checks["tool_execution"] = f"FAIL: {e}"
    
    # QC5: Stats tracking
    try:
        stats = reg.get_stats()
        stats_valid = (
            stats["total_tools"] >= 3 and
            "test" in stats["categories"] and
            stats["categories"]["test"] >= 3 and
            stats["cached_tools"] >= 1
        )
        checks["stats_tracking"] = "PASS" if stats_valid else "FAIL"
    except Exception as e:
        checks["stats_tracking"] = f"FAIL: {e}"
    
    # QC6: OpenAI schema format
    try:
        meta = reg.get("_schema_test")
        schema = meta.to_openai_schema()
        schema_valid = (
            schema["type"] == "function" and
            "function" in schema and
            schema["function"]["name"] == "_schema_test"
        )
        checks["openai_schema"] = "PASS" if schema_valid else "FAIL"
    except Exception as e:
        checks["openai_schema"] = f"FAIL: {e}"
    
    # QC7: Azure schema format
    try:
        schema = meta.to_azure_schema()
        schema_valid = (
            "name" in schema and
            "description" in schema and
            "parameters" in schema
        )
        checks["azure_schema"] = "PASS" if schema_valid else "FAIL"
    except Exception as e:
        checks["azure_schema"] = f"FAIL: {e}"
    
    # QC8: Async detection
    try:
        async def _async_test(): pass
        def _sync_test(): pass
        
        reg.register(_async_test, category="test")
        reg.register(_sync_test, category="test")
        
        async_meta = reg.get("_async_test")
        sync_meta = reg.get("_sync_test")
        
        checks["async_detection"] = "PASS" if (async_meta.is_async and not sync_meta.is_async) else "FAIL"
    except Exception as e:
        checks["async_detection"] = f"FAIL: {e}"
    
    return checks


# Run QC on import for self-validation
if __name__ == "__main__":
    qc = run_stage1_qc()
    print(json.dumps(qc, indent=2))
    all_pass = all("PASS" in str(v) for v in qc.values())
    sys.exit(0 if all_pass else 1)
