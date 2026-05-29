"""SEC EDGAR filing collector."""

from collectors.edgar.collector import EdgarCollector, lambda_handler

__all__ = ["EdgarCollector", "lambda_handler"]
