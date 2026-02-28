"""Loop and repetition detection for agent tool-call histories."""
from chimera.detection.actions import LoopDetector, OnDetect
from chimera.detection.base import DetectionResult, DetectionStrategy
from chimera.detection.composite import CompositeDetector
from chimera.detection.exact import ExactRepeatDetector
from chimera.detection.pattern import PatternCycleDetector

__all__ = [
    "CompositeDetector",
    "DetectionResult",
    "DetectionStrategy",
    "ExactRepeatDetector",
    "LoopDetector",
    "OnDetect",
    "PatternCycleDetector",
]
