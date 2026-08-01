"""Lesson generation back-ends."""

from .builder import build_lesson
from .service import GenerationError, generate_lesson, describe_sources

__all__ = [
    "build_lesson",
    "generate_lesson",
    "describe_sources",
    "GenerationError",
]
