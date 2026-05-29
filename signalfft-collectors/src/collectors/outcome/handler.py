"""AWS Lambda handler for the deferred price outcome collector.

Infrastructure requirements (not deployed by this code):
------------------------------------------------------------------------
Lambda definition:
  - Runtime: Python 3.12
  - Handler: collectors.outcome.handler.lambda_handler
  - Timeout: 300 seconds (5 min)
  - Memory: 256 MB

EventBridge rule:
  - Schedule: rate(1 hour)
  - Target: this Lambda

Environment variables:
  - ENVIRONMENT          — e.g. "prod"
  - AWS_REGION           — e.g. "us-east-1"
  - OUTCOMES_TABLE       — e.g. "prod-signalfft-outcomes"
  - ALPACA_API_KEY       — Alpaca API key
  - ALPACA_SECRET_KEY    — Alpaca secret key
  - OUTCOME_SCAN_LIMIT   — Max items per invocation (default 100)

IAM permissions required:
  - dynamodb:Scan on the outcomes table
  - dynamodb:UpdateItem on the outcomes table
------------------------------------------------------------------------
"""

from collectors.outcome.collector import lambda_handler  # noqa: F401
