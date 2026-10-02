"""Optional review providers. Main editor only calls review_provider.analyze."""
from __future__ import annotations

from .local_analyzer import LocalReviewProvider

__all__ = ["LocalReviewProvider"]
