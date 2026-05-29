variable "environment" {
  type = string
}

variable "collector_schedule_rate" {
  type    = string
  default = "rate(5 minutes)"
}

variable "lambda_function_arns" {
  description = "Map of collector name to Lambda ARN (can be empty for initial deploy)"
  type        = map(string)
  default     = {}
}

variable "edgar_collector_arn" {
  description = "ARN of the EDGAR collector Lambda function"
  type        = string
  default     = ""
}

variable "finnhub_collector_arn" {
  description = "ARN of the Finnhub news collector Lambda function"
  type        = string
  default     = ""
}

variable "bluesky_collector_arn" {
  description = "ARN of the Bluesky social collector Lambda function"
  type        = string
  default     = ""
}

variable "outcome_collector_arn" {
  description = "ARN of the outcome price collector Lambda function"
  type        = string
  default     = ""
}

resource "aws_cloudwatch_event_bus" "main" {
  name = "${var.environment}-signalfft-bus"

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

# ---------------------------------------------------------------------------
# EDGAR schedule (existing)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "collector_schedule" {
  name                = "${var.environment}-signalfft-collector-schedule"
  description         = "Triggers data collectors on schedule"
  schedule_expression = var.collector_schedule_rate
  event_bus_name      = "default"

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_cloudwatch_event_target" "edgar_collector" {
  count = var.edgar_collector_arn != "" ? 1 : 0

  rule      = aws_cloudwatch_event_rule.collector_schedule.name
  target_id = "edgar-collector"
  arn       = var.edgar_collector_arn
}

# ---------------------------------------------------------------------------
# Finnhub News schedule (every 5 minutes)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "finnhub_schedule" {
  name                = "${var.environment}-signalfft-finnhub-news-schedule"
  description         = "Triggers Finnhub news collector every 5 minutes"
  schedule_expression = "rate(5 minutes)"
  event_bus_name      = "default"

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_cloudwatch_event_target" "finnhub_collector" {
  rule      = aws_cloudwatch_event_rule.finnhub_schedule.name
  target_id = "finnhub-news-collector"
  arn       = var.finnhub_collector_arn
}

# ---------------------------------------------------------------------------
# Bluesky Social schedule (every 10 minutes)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "bluesky_schedule" {
  name                = "${var.environment}-signalfft-bluesky-schedule"
  description         = "Triggers Bluesky social collector every 10 minutes"
  schedule_expression = "rate(10 minutes)"
  event_bus_name      = "default"

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_cloudwatch_event_target" "bluesky_collector" {
  rule      = aws_cloudwatch_event_rule.bluesky_schedule.name
  target_id = "bluesky-collector"
  arn       = var.bluesky_collector_arn
}

# ---------------------------------------------------------------------------
# Outcome Price Collector schedule (every 1 hour)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "outcome_schedule" {
  name                = "${var.environment}-signalfft-outcome-schedule"
  description         = "Triggers outcome price collector hourly"
  schedule_expression = "rate(1 hour)"
  event_bus_name      = "default"

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_cloudwatch_event_target" "outcome_collector" {
  rule      = aws_cloudwatch_event_rule.outcome_schedule.name
  target_id = "outcome-collector"
  arn       = var.outcome_collector_arn
}
