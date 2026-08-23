"""In-process CREngine XPointer and EPUB CFI locators."""

from bookshift.domain.locators.compiler import (
    audio_to_xpointer,
    build_chapters,
    compile_alignment_map,
    xpointer_to_collapsed_cp,
)
from bookshift.domain.locators.cfi import parse_cfi, point_to_cfi
from bookshift.domain.locators.crengine import (
    build_xpointer,
    normalize_for_search,
    parse_xpointer,
)

__all__ = [
    "audio_to_xpointer",
    "build_chapters",
    "build_xpointer",
    "compile_alignment_map",
    "normalize_for_search",
    "parse_cfi",
    "parse_xpointer",
    "point_to_cfi",
    "xpointer_to_collapsed_cp",
]
