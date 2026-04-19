"""Discriminated union base class for polymorphic config serialization."""
from __future__ import annotations

from typing import Any, ClassVar, TypeVar

T = TypeVar("T", bound="DiscriminatedUnion")


class DiscriminatedUnion:
    """Base class for polymorphic config serialization.

    Subclasses register themselves with a ``type_name`` class variable.
    The ``from_config()`` class method on the base class dispatches to the
    correct subclass based on the ``type`` field in the config dict.

    Example:
        ```python
        class Environment(DiscriminatedUnion):
            _registry: dict[str, type] = {}

        class LocalEnvironment(Environment):
            type_name = "local"
            def __init__(self, working_dir: str = "."):
                self.working_dir = working_dir

        env = Environment.from_config({"type": "local", "working_dir": "/tmp"})
        ```
    """

    type_name: ClassVar[str] = ""
    _registry: ClassVar[dict[str, type]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if cls.type_name:
            base = cls._find_union_base()
            if base is not None:
                base._registry[cls.type_name] = cls

    @classmethod
    def _find_union_base(cls) -> type[DiscriminatedUnion] | None:
        """Find the first DiscriminatedUnion subclass in MRO that has its own _registry."""
        for parent in cls.__mro__:
            if (
                parent is not cls
                and parent is not DiscriminatedUnion
                and issubclass(parent, DiscriminatedUnion)
                and "_registry" in parent.__dict__
            ):
                return parent
        return None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> DiscriminatedUnion:
        """Create an instance from a config dict with a ``type`` field.

        Args:
            config: Dictionary with a ``type`` key and constructor kwargs.

        Returns:
            An instance of the appropriate subclass.

        Raises:
            ValueError: If ``type`` is missing or unknown.
        """
        if isinstance(config, cls):
            return config

        config = dict(config)
        type_name = config.pop("type", None)

        if type_name is None:
            raise ValueError(
                f"Config must have a 'type' field. "
                f"Available types: {list(cls._registry.keys())}"
            )

        subcls = cls._registry.get(type_name)
        if subcls is None:
            raise ValueError(
                f"Unknown type '{type_name}' for {cls.__name__}. "
                f"Available: {list(cls._registry.keys())}"
            )

        return subcls(**config)  # type: ignore[no-any-return]  # dynamic subclass dispatch

    @classmethod
    def available_types(cls) -> list[str]:
        """Return all registered type names for this union hierarchy.

        Returns:
            List of registered type name strings.
        """
        return list(cls._registry.keys())

    def to_config(self) -> dict[str, Any]:
        """Serialize to a config dict.

        Returns:
            Dictionary with ``type`` and all public instance attributes.
        """
        config: dict[str, Any] = {"type": self.type_name}
        for key, value in self.__dict__.items():
            if not key.startswith("_"):
                if isinstance(value, DiscriminatedUnion):
                    config[key] = value.to_config()
                else:
                    config[key] = value
        return config
