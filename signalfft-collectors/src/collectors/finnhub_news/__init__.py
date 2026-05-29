"""Finnhub financial news collector."""

from collectors.finnhub_news.collector import FinnhubNewsCollector, lambda_handler

__all__ = ["FinnhubNewsCollector", "lambda_handler"]
