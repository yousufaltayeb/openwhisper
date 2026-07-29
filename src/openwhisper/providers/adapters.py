"""Backward-compatible import location for application-core provider bridges."""

from .bridges import CoreCleanupAdapter, CoreTranscriptionAdapter

__all__ = ["CoreCleanupAdapter", "CoreTranscriptionAdapter"]
