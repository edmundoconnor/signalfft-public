"""Filing document fetcher for SEC EDGAR filings."""

from collectors.filing_fetch.collector import FilingFetcher, lambda_handler

__all__ = ["FilingFetcher", "lambda_handler"]
