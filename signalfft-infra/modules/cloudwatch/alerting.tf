###############################################################################
# SNS Alerting + CloudWatch Alarms
###############################################################################

# ---------------------------------------------------------------------------
# SNS Topic
# ---------------------------------------------------------------------------
resource "aws_sns_topic" "pipeline_alerts" {
  name = "${local.prefix}-pipeline-alerts"

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.pipeline_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---------------------------------------------------------------------------
# CRITICAL: DLQ Depth >= 1 (one alarm per DLQ)
# Any message in a DLQ means processing failed — investigate immediately.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  for_each = toset(local.queues)

  alarm_name          = "${local.prefix}-dlq-depth-${each.value}"
  alarm_description   = "DLQ ${each.value}-dlq has messages — processing failures detected"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = "${local.prefix}-${each.value}-dlq"
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = {
    Environment = var.environment
    Project     = "signalfft"
    Severity    = "critical"
  }
}

# ---------------------------------------------------------------------------
# CRITICAL: Lambda Collector Errors >= 3 in 15 minutes (per collector)
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "collector_errors" {
  for_each = toset(local.lambda_collectors)

  alarm_name          = "${local.prefix}-collector-errors-${replace(each.value, "${local.prefix}-", "")}"
  alarm_description   = "${each.value} Lambda is throwing errors"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 3
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 3
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = each.value
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = {
    Environment = var.environment
    Project     = "signalfft"
    Severity    = "critical"
  }
}

# ---------------------------------------------------------------------------
# WARNING: Queue backlog age > 30 minutes
# Means a consumer is dead or stalled. One alarm per ingestion queue.
# ---------------------------------------------------------------------------
locals {
  ingestion_queues = ["raw-events", "features", "signals"]
}

resource "aws_cloudwatch_metric_alarm" "queue_age" {
  for_each = toset(local.ingestion_queues)

  alarm_name          = "${local.prefix}-queue-age-${each.value}"
  alarm_description   = "Queue ${each.value} oldest message is > 30 minutes — consumer may be down"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 1800
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = "${local.prefix}-${each.value}"
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = {
    Environment = var.environment
    Project     = "signalfft"
    Severity    = "warning"
  }
}

# ---------------------------------------------------------------------------
# WARNING: DynamoDB Throttling > 0
# On-demand billing shouldn't throttle, but if it does, something is wrong.
# ---------------------------------------------------------------------------
locals {
  throttle_tables = ["signals", "trade-candidates", "graph-edges"]
}

resource "aws_cloudwatch_metric_alarm" "dynamo_throttle" {
  for_each = toset(local.throttle_tables)

  alarm_name          = "${local.prefix}-dynamo-throttle-${each.value}"
  alarm_description   = "DynamoDB table ${each.value} is being throttled"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ThrottledRequests"
  namespace           = "AWS/DynamoDB"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = "${local.prefix}-${each.value}"
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = {
    Environment = var.environment
    Project     = "signalfft"
    Severity    = "warning"
  }
}
