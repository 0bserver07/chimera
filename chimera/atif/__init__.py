"""ATIF v1.7 (Agent Trajectory Interchange Format) emission and consumption.

Pier — Datacurve's Harbor-fork runner — emits "augmented ATIF v1.7"
trajectories: strict one step per API turn, strict reasoning vs message
separation, no fabricated assistant text, and aggregate context metrics.
This package lets Chimera speak the same format:

- :class:`~chimera.atif.emitter.ATIFEmitter` — subscribe to a run's
  :class:`~chimera.events.base.EventBus` and write an ATIF trajectory.
- :class:`~chimera.atif.reader.ATIFReader` — load (and optionally
  validate) an ATIF file; convert steps back into Chimera events.
- :class:`~chimera.atif.validator.ATIFValidator` — schema-shape plus
  structural rules, mirroring the upstream Pier model validators.

The frozen JSON schema lives at ``chimera/atif/schema.json`` (extracted
mechanically from the upstream Pier pydantic models; see
``docs/specs/atif-trajectory-emission.md``).
"""
from chimera.atif.emitter import ATIFEmitter
from chimera.atif.reader import ATIFReader
from chimera.atif.validator import ATIFValidator, ValidationResult

__all__ = ["ATIFEmitter", "ATIFReader", "ATIFValidator", "ValidationResult"]
