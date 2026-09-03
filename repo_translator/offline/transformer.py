"""
Registry of offline rule-based code transformers.

Each entry maps "from_lang:to_lang" to a transform(code: str) -> str function.
"""
from __future__ import annotations

from collections.abc import Callable

from repo_translator.offline import cpp_to_py, ts_to_py

_REGISTRY: dict[str, Callable[[str], str]] = {
    "typescript:python": ts_to_py.transform,
    "javascript:python": ts_to_py.transform,   # JS is a TS subset
    "cpp:python": cpp_to_py.transform,
    "c:python": cpp_to_py.transform,           # C shares many patterns with C++
}


class OfflineTransformer:
    def transform(self, code: str, from_lang: str, to_lang: str) -> str:
        key = f"{from_lang}:{to_lang}"
        fn  = _REGISTRY.get(key)
        if fn is None:
            pairs = ", ".join(_REGISTRY)
            raise ValueError(
                f"No offline transformer for {from_lang} → {to_lang}.\n"
                f"  Supported pairs: {pairs}"
            )
        return fn(code)

    @staticmethod
    def supports(from_lang: str, to_lang: str) -> bool:
        return f"{from_lang}:{to_lang}" in _REGISTRY

    @staticmethod
    def supported_pairs() -> list[str]:
        return list(_REGISTRY)
