"""Perspective registry: register and retrieve review perspectives."""
from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.review.perspective import BUILTIN_PERSPECTIVES

if TYPE_CHECKING:
    from chimera.review.perspective import ReviewPerspective


class PerspectiveRegistry:
    """Register and retrieve review perspectives.

    Initialised with the 8 built-in perspectives. Custom perspectives
    can be registered to override built-ins or add new review angles.
    """

    def __init__(self) -> None:
        self._perspectives: dict[str, ReviewPerspective] = dict(BUILTIN_PERSPECTIVES)

    def register(self, perspective: ReviewPerspective) -> None:
        """Add or override a perspective.

        Args:
            perspective: The perspective to register. If a perspective with
                the same name already exists, it is replaced.
        """
        self._perspectives[perspective.name] = perspective

    def get(self, name: str) -> ReviewPerspective:
        """Get a perspective by name.

        Args:
            name: The perspective name.

        Returns:
            The matching ReviewPerspective.

        Raises:
            KeyError: If no perspective with that name is registered.
        """
        return self._perspectives[name]

    def list(self) -> list[str]:
        """Return all registered perspective names.

        Returns:
            Sorted list of perspective name strings.
        """
        return sorted(self._perspectives.keys())

    def for_language(self, language: str) -> list[ReviewPerspective]:
        """Return perspectives applicable to a language.

        Perspectives with ``languages=None`` apply to all languages.
        Perspectives with a languages list only match if the given
        language is in that list (case-insensitive comparison).

        Args:
            language: The programming language to filter by.

        Returns:
            List of matching ReviewPerspective objects.
        """
        language_lower = language.lower()
        result: list[ReviewPerspective] = []
        for perspective in self._perspectives.values():
            if perspective.languages is None:
                result.append(perspective)
            elif language_lower in [lang.lower() for lang in perspective.languages]:
                result.append(perspective)
        return result
