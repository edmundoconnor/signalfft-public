# Role: Intelligence Plane
# Used by: Collectors (Lambda) + Engine services (ECS Fargate)
# Access: Full CRUD on intelligence tables, S3 raw artifacts, intelligence SQS queues, Bedrock

data "aws_iam_policy_document" "intelligence_trust" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com", "ecs-tasks.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "intelligence" {
  name               = "${var.environment}-signalfft-role-intelligence"
  assume_role_policy = data.aws_iam_policy_document.intelligence_trust.json

  tags = {
    Environment = var.environment
    Project     = "signalfft"
    Plane       = "intelligence"
  }
}

data "aws_iam_policy_document" "intelligence_allow" {
  # DynamoDB: Full CRUD on intelligence tables
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
      var.table_arns["outcomes"],
      var.table_arns["graph_edges"],
      "${var.table_arns["graph_edges"]}/index/*",
      var.table_arns["shadow_scores"],
      var.table_arns["semantic_deltas"],
      "${var.table_arns["semantic_deltas"]}/index/*",
    ]
  }

  # S3: Read/Write raw artifacts
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [
      "${var.bucket_arn}/*",
    ]
  }

  # S3: List bucket
  statement {
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]
  }

  # SQS: Intelligence queues
  statement {
    effect = "Allow"
    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [
      var.queue_arns["raw-events"],
      var.queue_arns["features"],
      var.queue_arns["signals"],
      var.queue_arns["waves"],
      var.queue_arns["risk-input"],
      var.queue_arns["outcome-tracking"],
      var.queue_arns["filing-fetch"],
      var.queue_arns["filing-ready"],
      var.queue_arns["sections-ready"],
      var.queue_arns["filing-indexer"],
      var.queue_arns["filing-index-ready"],
      var.queue_arns["high-priority"],
      var.queue_arns["triage-input"],
      var.queue_arns["delta-analysis"],
      var.queue_arns["delta-complete"],
    ]
  }

  # Bedrock: Invoke model for semantic interpretation
  statement {
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel"]
    resources = ["*"]
  }

  # SSM: read runtime collector/API secrets by parameter name.
  statement {
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
    ]
    resources = [
      "arn:aws:ssm:*:${var.account_id}:parameter/signalfft/${var.environment}/*",
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
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

data "aws_iam_policy_document" "intelligence_deny" {
  # Explicit deny: trade_candidates and execution_telemetry tables
  statement {
    effect  = "Deny"
    actions = ["dynamodb:*"]
    resources = [
      var.table_arns["trade_candidates"],
      var.table_arns["execution_telemetry"],
    ]
  }

  # Explicit deny: candidates queue
  statement {
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [var.queue_arns["candidates"]]
  }

  # Explicit deny: assume execution role
  statement {
    effect    = "Deny"
    actions   = ["sts:AssumeRole"]
    resources = ["arn:aws:iam::${var.account_id}:role/${var.environment}-signalfft-role-execution"]
  }
}

resource "aws_iam_role_policy" "intelligence_allow" {
  name   = "${var.environment}-signalfft-intelligence-allow"
  role   = aws_iam_role.intelligence.id
  policy = data.aws_iam_policy_document.intelligence_allow.json
}

resource "aws_iam_role_policy" "intelligence_deny" {
  name   = "${var.environment}-signalfft-intelligence-deny"
  role   = aws_iam_role.intelligence.id
  policy = data.aws_iam_policy_document.intelligence_deny.json
}
