variable "environment" {
  type = string
}

locals {
  queues = {
    "raw-events" = {}
    "features"   = {}
    "signals"    = {}
    "waves"      = {}
    "candidates"       = {}
    "risk-input"       = {}
    "outcome-tracking" = {}
    "filing-fetch"     = {}
    "filing-ready"     = {}
    "sections-ready"      = {}
    "filing-indexer"      = {}
    "filing-index-ready"  = {}
    "high-priority"       = {}
    "triage-input"        = {}
    "delta-analysis"      = {}
    "delta-complete"      = {}
  }
}

resource "aws_sqs_queue" "dlq" {
  for_each = local.queues

  name                      = "${var.environment}-signalfft-${each.key}-dlq"
  message_retention_seconds = 1209600 # 14 days

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_sqs_queue" "main" {
  for_each = local.queues

  name                       = "${var.environment}-signalfft-${each.key}"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 1209600 # 14 days

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[each.key].arn
    maxReceiveCount     = 3
  })

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}
