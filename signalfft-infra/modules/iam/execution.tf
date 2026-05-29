# Role: Execution Plane
# Used by: Execution router (ECS Fargate)
# Access: Candidates queue, execution-telemetry + graph-edges DynamoDB, CloudWatch Logs
# Denied: All intelligence-plane resources (events, features, signals, waves, narratives, attention-field tables)

data "aws_iam_policy_document" "execution_trust" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.environment}-signalfft-role-execution"
  assume_role_policy = data.aws_iam_policy_document.execution_trust.json

  tags = {
    Environment = var.environment
    Project     = "signalfft"
    Plane       = "execution"
  }
}

data "aws_iam_policy_document" "execution_allow" {
  # SQS: Receive from candidates queue
  statement {
    sid    = "SQSCandidates"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [var.queue_arns["candidates"]]
  }

  # CloudWatch Logs
  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:${var.account_id}:*"]
  }

  # DynamoDB: Write to execution-telemetry table
  statement {
    sid    = "DynamoDBExecutionTelemetry"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:DescribeTable",
    ]
    resources = [var.table_arns["execution_telemetry"]]
  }

  # DynamoDB: Write outcome feedback to graph-edges table
  statement {
    sid    = "DynamoDBGraphEdges"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:DescribeTable",
    ]
    resources = [var.table_arns["graph_edges"]]
  }
}

data "aws_iam_policy_document" "execution_deny" {
  # Deny: Intelligence-plane DynamoDB tables (enumerated, not blanket)
  statement {
    sid     = "DenyIntelligenceTables"
    effect  = "Deny"
    actions = ["dynamodb:*"]
    resources = [
      var.table_arns["events"],
      var.table_arns["features"],
      var.table_arns["signals"],
      var.table_arns["waves"],
      var.table_arns["narratives"],
      var.table_arns["attention_field"],
      var.table_arns["trade_candidates"],
    ]
  }

  # Deny: ALL S3
  statement {
    sid       = "DenyIntelligenceS3"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [var.bucket_arn, "${var.bucket_arn}/*"]
  }

  # Deny: ALL non-candidates SQS
  statement {
    sid     = "DenyIntelligenceQueues"
    effect  = "Deny"
    actions = ["sqs:*"]
    resources = [
      var.queue_arns["raw-events"],
      var.queue_arns["features"],
      var.queue_arns["signals"],
      var.queue_arns["waves"],
      var.queue_arns["risk-input"],
    ]
  }

  # Deny: Bedrock
  statement {
    sid       = "DenyBedrock"
    effect    = "Deny"
    actions   = ["bedrock:*"]
    resources = ["*"]
  }

  # Deny: Assume intelligence or decision roles
  statement {
    sid     = "DenyCrossPlaneRoles"
    effect  = "Deny"
    actions = ["sts:AssumeRole"]
    resources = [
      "arn:aws:iam::${var.account_id}:role/${var.environment}-signalfft-role-intelligence",
      "arn:aws:iam::${var.account_id}:role/${var.environment}-signalfft-role-decision",
    ]
  }
}

resource "aws_iam_role_policy" "execution_allow" {
  name   = "${var.environment}-signalfft-execution-allow"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_allow.json
}

resource "aws_iam_role_policy" "execution_deny" {
  name   = "${var.environment}-signalfft-execution-deny"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_deny.json
}
