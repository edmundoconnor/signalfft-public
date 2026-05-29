"""Feature extraction service."""

from engine.feature_extraction.extractor import extract_features
from engine.feature_extraction.service import FeatureExtractionService

__all__ = ["extract_features", "FeatureExtractionService"]
