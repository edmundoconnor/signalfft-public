"""AWS Lambda handler for the filing document fetcher.

Infrastructure requirements (not deployed by this code):
------------------------------------------------------------------------
Lambda definition:
  - Runtime: Python 3.12
  - Handler: collectors.filing_fetch.handler.lambda_handler
  - Timeout: 300 seconds (5 min)
  - Memory: 512 MB

SQS trigger:
  - Source queue: {env}-signalfft-filing-fetch
  - Batch size: 1

Environment variables:
  - ENVIRONMENT          — e.g. "prod"
  - AWS_REGION           — e.g. "us-east-1"
  - ARTIFACTS_BUCKET     — e.g. "prod-signalfft-artifacts"
  - EVENTS_TABLE         — e.g. "prod-signalfft-events"
  - FILING_READY_QUEUE_URL — SQS URL for FilingDocumentReady events
  - EDGAR_USER_AGENT     — SEC EDGAR required User-Agent header

IAM permissions required:
  - s3:PutObject on the artifacts bucket
  - dynamodb:Query on the events table
  - dynamodb:UpdateItem on the events table
  - sqs:SendMessage on the filing-ready queue
------------------------------------------------------------------------
"""

from collectors.filing_fetch.collector import lambda_handler  # noqa: F401
