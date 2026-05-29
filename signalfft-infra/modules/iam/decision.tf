# Role: Decision Plane
# Used by: Wave engine, Risk gateway (ECS Fargate)
# Access: READ ONLY on intelligence tables, full CRUD on trade_candidates, signals->candidates SQS

data "aws_iam_policy_document" "decision_trust" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "decision" {
  name               = "${var.environment}-signalfft-role-decision"
  assume_role_policy = data.aws_iam_policy_document.decision_trust.json

  tags = {
    Environment = var.environment
    Project     = "signalfft"
    Plane       = "decision"
  }
}

data "aws_iam_policy_document" "decision_allow" {
  # DynamoDB: READ ONLY on intelligence tables
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:BatchGetItem",
    ]
    resources = [
      var.table_arns["entities"],
      "${var.table_arns["entities"]}/index/*",
      var.table_arns["events"],
      "${var.table_arns["events"]}/index/*",
      var.table_arns["features"],
      "${var.table_arns["features"]}/index/*",
      var.table_arns["signals"],
      "${var.table_arns["signals"]}/index/*",
      var.table_arns["waves"],
      var.table_arns["narratives"],
      "${var.table_arns["narratives"]}/index/*",
      var.table_arns["attention_field"],
      var.table_arns["graph_edges"],
      "${var.table_arns["graph_edges"]}/index/*",
    ]
  }

  # DynamoDB: Full CRUD on trade_candidates
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:BatchWriteItem",
      "dynamodb:BatchGetItem",
    ]
    resources = [
      var.table_arns["trade_candidates"],
    ]
  }

  # DynamoDB: Write on graph_edges (execution outcome feedback loop)
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:BatchWriteItem",
    ]
    resources = [
      var.table_arns["graph_edges"],
    ]
  }

  # SQS: Receive from signals and risk-input, send to candidates
  statement {
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [
      var.queue_arns["signals"],
      var.queue_arns["risk-input"],
    ]
  }

  statement {
    effect = "Allow"
    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [var.queue_arns["candidates"]]
  }

  # CloudWatch Logs
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:${var.account_id}:*"]
  }
}

data "aws_iam_policy_document" "decision_deny" {
  # Deny: Write to intelligence tables (graph_edges excluded — needed for outcome feedback)
  statement {
    effect = "Deny"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem",
    ]
    resources = [
      var.table_arns["entities"],
      var.table_arns["events"],
      var.table_arns["features"],
      var.table_arns["signals"],
      var.table_arns["waves"],
      var.table_arns["narratives"],
      var.table_arns["attention_field"],
    ]
  }

  # Deny: execution_telemetry table (execution-plane only)
  statement {
    effect    = "Deny"
    actions   = ["dynamodb:*"]
    resources = [var.table_arns["execution_telemetry"]]
  }

  # Deny: S3 access
  statement {
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [var.bucket_arn, "${var.bucket_arn}/*"]
  }

  # Deny: Bedrock
  statement {
    effect    = "Deny"
    actions   = ["bedrock:*"]
    resources = ["*"]
  }

  # Deny: Assume execution role
  statement {
    effect    = "Deny"
    actions   = ["sts:AssumeRole"]
    resources = ["arn:aws:iam::${var.account_id}:role/${var.environment}-signalfft-role-execution"]
  }
}

resource "aws_iam_role_policy" "decision_allow" {
  name   = "${var.environment}-signalfft-decision-allow"
  role   = aws_iam_role.decision.id
  policy = data.aws_iam_policy_document.decision_allow.json
}

resource "aws_iam_role_policy" "decision_deny" {
  name   = "${var.environment}-signalfft-decision-deny"
  role   = aws_iam_role.decision.id
  policy = data.aws_iam_policy_document.decision_deny.json
}
