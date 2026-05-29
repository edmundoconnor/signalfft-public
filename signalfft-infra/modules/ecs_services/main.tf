variable "environment" {
  type = string
}

variable "account_id" {
  type = string
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "cluster_id" {
  type = string
}

variable "cluster_name" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "ecs_tasks_sg_id" {
  type = string
}

variable "intelligence_role_arn" {
  type = string
}

variable "decision_role_arn" {
  type = string
}

variable "execution_role_arn" {
  type = string
}

variable "ecr_repository_urls" {
  type = map(string)
}

variable "queue_urls" {
  type = map(string)
}

variable "table_names" {
  type = map(string)
}

variable "s3_bucket_name" {
  type = string
}

variable "dashboard_target_group_arn" {
  type = string
}

variable "dashboard_cognito_user_pool_id" {
  type    = string
  default = ""
}

variable "dashboard_cognito_client_id" {
  type    = string
  default = ""
}

variable "dashboard_allowed_origins" {
  type    = list(string)
  default = []
}

variable "dashboard_auth_required" {
  type    = bool
  default = true
}

# ECS Task Execution Role (for pulling images from ECR and writing logs)
data "aws_iam_policy_document" "ecs_execution_trust" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${var.environment}-signalfft-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_execution_trust.json

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ---------------------------------------------------------------------------
# Consolidated: 3 services instead of 8
# ---------------------------------------------------------------------------
locals {
  services = [
    "intelligence-pipeline",
    "decision-execution",
    "execution-router",
    "risk-gateway",
    "dashboard",
  ]

  # Map service name to task role ARN
  service_roles = {
    "intelligence-pipeline" = var.intelligence_role_arn
    "decision-execution"    = var.decision_role_arn
    "execution-router"      = var.execution_role_arn
    "risk-gateway"          = var.decision_role_arn
    "dashboard"             = var.intelligence_role_arn
  }

  # Map service name to container command
  service_commands = {
    "intelligence-pipeline" = ["engine.runner_main"]
    "decision-execution"    = ["risk_gateway.unified_main"]
    "execution-router"      = ["execution.main"]
    "risk-gateway"          = ["risk_gateway"]
    "dashboard"             = ["engine.dashboard.service"]
  }

  # Map service name to ECR image
  service_images = {
    "intelligence-pipeline" = "${var.ecr_repository_urls["intelligence-pipeline"]}:latest"
    "decision-execution"    = "${var.ecr_repository_urls["decision-execution"]}:latest"
    "execution-router"      = "${var.ecr_repository_urls["decision-execution"]}:latest"
    "risk-gateway"          = "${var.ecr_repository_urls["risk-gateway"]}:latest"
    "dashboard"             = "${var.ecr_repository_urls["intelligence-pipeline"]}:latest"
  }

  ssm_parameter_arns = {
    alpaca_api_key    = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/signalfft/${var.environment}/alpaca-api-key"
    alpaca_secret_key = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/signalfft/${var.environment}/alpaca-secret-key"
    anthropic_api_key = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/signalfft/${var.environment}/anthropic-api-key"
  }

  # Non-secret Alpaca trading settings
  alpaca_env = [
    { name = "ALPACA_NOTIONAL", value = "2000" },
  ]

  alpaca_secrets = [
    { name = "ALPACA_API_KEY", valueFrom = local.ssm_parameter_arns.alpaca_api_key },
    { name = "ALPACA_SECRET_KEY", valueFrom = local.ssm_parameter_arns.alpaca_secret_key },
  ]

  anthropic_secrets = [
    { name = "ANTHROPIC_API_KEY", valueFrom = local.ssm_parameter_arns.anthropic_api_key },
  ]

  service_secrets = {
    "intelligence-pipeline" = concat(local.alpaca_secrets, local.anthropic_secrets)
    "decision-execution"    = local.alpaca_secrets
    "execution-router"      = local.alpaca_secrets
    "risk-gateway"          = []
    "dashboard"             = []
  }

  # Common environment variables for all services
  common_env = [
    { name = "ENVIRONMENT", value = var.environment },
    { name = "AWS_REGION", value = var.aws_region },
    { name = "ARTIFACTS_BUCKET", value = var.s3_bucket_name },
  ]

  # Service-specific environment variables
  service_env = {
    "intelligence-pipeline" = concat([
      # Feature extraction
      { name = "RAW_EVENTS_QUEUE_URL", value = var.queue_urls["raw-events"] },
      { name = "FEATURES_QUEUE_URL", value = var.queue_urls["features"] },
      # Signal scoring
      { name = "SIGNALS_QUEUE_URL", value = var.queue_urls["signals"] },
      # Wave engine
      { name = "WAVES_QUEUE_URL", value = var.queue_urls["waves"] },
      # Outcome tracking
      { name = "OUTCOME_TRACKING_QUEUE_URL", value = var.queue_urls["outcome-tracking"] },
      { name = "OUTCOMES_TABLE", value = var.table_names["outcomes"] },
      # All table names
      { name = "ENTITIES_TABLE", value = var.table_names["entities"] },
      { name = "EVENTS_TABLE", value = var.table_names["events"] },
      { name = "FEATURES_TABLE", value = var.table_names["features"] },
      { name = "SIGNALS_TABLE", value = var.table_names["signals"] },
      { name = "WAVES_TABLE", value = var.table_names["waves"] },
      { name = "NARRATIVES_TABLE", value = var.table_names["narratives"] },
      { name = "ATTENTION_FIELD_TABLE", value = var.table_names["attention_field"] },
      { name = "GRAPH_EDGES_TABLE", value = var.table_names["graph_edges"] },
      { name = "RISK_INPUT_QUEUE_URL", value = var.queue_urls["risk-input"] },
      { name = "S3_BUCKET", value = var.s3_bucket_name },
      # Section extractor (Task 04)
      { name = "FILING_READY_QUEUE_URL", value = var.queue_urls["filing-ready"] },
      { name = "SECTIONS_READY_QUEUE_URL", value = var.queue_urls["sections-ready"] },
      # Filing indexer (F1.3-F1.5)
      { name = "FILING_INDEXER_QUEUE_URL", value = var.queue_urls["filing-indexer"] },
      { name = "FILING_INDEX_READY_QUEUE_URL", value = var.queue_urls["filing-index-ready"] },
      # Keyword triage (Tier 1)
      { name = "HIGH_PRIORITY_QUEUE_URL", value = var.queue_urls["high-priority"] },
      # Edge 1: Quiet Filing Triage
      { name = "TRIAGE_INPUT_QUEUE_URL", value = var.queue_urls["triage-input"] },
      { name = "SHADOW_SCORES_TABLE", value = var.table_names["shadow_scores"] },
      { name = "TRIAGE_PROMPT_PATH", value = "/app/prompts/quiet_filing_triage.yaml" },
      { name = "PROMPT_TEMPLATE_PATH", value = "/app/prompts/directional_interpretation.yaml" },
      # Edge 2: Semantic Delta Analysis
      { name = "DELTA_ANALYSIS_QUEUE_URL", value = var.queue_urls["delta-analysis"] },
      { name = "DELTA_COMPLETE_QUEUE_URL", value = var.queue_urls["delta-complete"] },
      { name = "SEMANTIC_DELTAS_TABLE", value = var.table_names["semantic_deltas"] },
      { name = "DELTA_PROMPT_PATH", value = "/app/prompts/semantic_delta_analysis.yaml" },
      { name = "DELTA_SCORING_CONFIG_PATH", value = "/app/config/delta_scoring.json" },
    ], local.alpaca_env)
    "decision-execution" = concat([
      { name = "RISK_INPUT_QUEUE_URL", value = var.queue_urls["risk-input"] },
      { name = "EXECUTION_INPUT_QUEUE_URL", value = var.queue_urls["candidates"] },
      { name = "CANDIDATES_QUEUE_URL", value = var.queue_urls["candidates"] },
      { name = "SIGNALS_TABLE", value = var.table_names["signals"] },
      { name = "TRADE_CANDIDATES_TABLE", value = var.table_names["trade_candidates"] },
      { name = "GRAPH_EDGES_TABLE", value = var.table_names["graph_edges"] },
      { name = "BROKER_MODE", value = "alpaca" },
    ], local.alpaca_env)
    "execution-router" = concat([
      { name = "INPUT_QUEUE_URL", value = var.queue_urls["candidates"] },
      { name = "BROKER_MODE", value = "alpaca" },
      { name = "EXECUTION_TELEMETRY_TABLE", value = var.table_names["execution_telemetry"] },
      { name = "GRAPH_EDGES_TABLE", value = var.table_names["graph_edges"] },
    ], local.alpaca_env)
    "risk-gateway" = [
      { name = "INPUT_QUEUE_URL", value = var.queue_urls["risk-input"] },
      { name = "OUTPUT_QUEUE_URL", value = var.queue_urls["candidates"] },
      { name = "SIGNALS_TABLE", value = var.table_names["signals"] },
      { name = "TRADE_CANDIDATES_TABLE", value = var.table_names["trade_candidates"] },
      { name = "MIN_SIGNAL_SCORE", value = "0.05" },
      { name = "MAX_CANDIDATES_PER_WINDOW", value = "10" },
    ]
    "dashboard" = [
      { name = "EVENTS_TABLE", value = var.table_names["events"] },
      { name = "FEATURES_TABLE", value = var.table_names["features"] },
      { name = "SIGNALS_TABLE", value = var.table_names["signals"] },
      { name = "WAVES_TABLE", value = var.table_names["waves"] },
      { name = "NARRATIVES_TABLE", value = var.table_names["narratives"] },
      { name = "ATTENTION_FIELD_TABLE", value = var.table_names["attention_field"] },
      { name = "TRADE_CANDIDATES_TABLE", value = var.table_names["trade_candidates"] },
      { name = "ENTITIES_TABLE", value = var.table_names["entities"] },
      { name = "GRAPH_EDGES_TABLE", value = var.table_names["graph_edges"] },
      { name = "COGNITO_USER_POOL_ID", value = var.dashboard_cognito_user_pool_id },
      { name = "COGNITO_CLIENT_ID", value = var.dashboard_cognito_client_id },
      { name = "DASHBOARD_ALLOWED_ORIGINS", value = join(",", var.dashboard_allowed_origins) },
      { name = "DASHBOARD_AUTH_REQUIRED", value = tostring(var.dashboard_auth_required) },
    ]
  }
}

data "aws_iam_policy_document" "ecs_execution_secrets" {
  statement {
    effect    = "Allow"
    actions   = ["ssm:GetParameters"]
    resources = values(local.ssm_parameter_arns)
  }

  statement {
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name   = "${var.environment}-signalfft-ecs-execution-secrets"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.ecs_execution_secrets.json
}

# CloudWatch log groups
resource "aws_cloudwatch_log_group" "services" {
  for_each = toset(local.services)

  name              = "/ecs/${var.environment}-signalfft/${each.value}"
  retention_in_days = 30

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

# Task definitions — 3 consolidated services
resource "aws_ecs_task_definition" "services" {
  for_each = toset(local.services)

  family                   = "${var.environment}-signalfft-${each.value}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = local.service_roles[each.value]

  container_definitions = jsonencode([{
    name      = each.value
    image     = local.service_images[each.value]
    essential = true
    command   = local.service_commands[each.value]

    portMappings = each.value == "dashboard" ? [{
      containerPort = 8080
      protocol      = "tcp"
    }] : []

    environment = concat(local.common_env, local.service_env[each.value])
    secrets     = lookup(local.service_secrets, each.value, [])

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.services[each.value].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

# Worker services (intelligence-pipeline, decision-execution) — no load balancer
resource "aws_ecs_service" "workers" {
  for_each = toset([for s in local.services : s if s != "dashboard"])

  name            = "${var.environment}-signalfft-${each.value}"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.services[each.value].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.public_subnet_ids
    security_groups  = [var.ecs_tasks_sg_id]
    assign_public_ip = true
  }

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }

  lifecycle {
    ignore_changes = [desired_count]
  }
}

# Dashboard service (with load balancer)
resource "aws_ecs_service" "dashboard" {
  name            = "${var.environment}-signalfft-dashboard"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.services["dashboard"].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.public_subnet_ids
    security_groups  = [var.ecs_tasks_sg_id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = var.dashboard_target_group_arn
    container_name   = "dashboard"
    container_port   = 8080
  }

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }

  lifecycle {
    ignore_changes = [desired_count]
  }
}

output "service_names" {
  value = { for s in local.services : s => "${var.environment}-signalfft-${s}" }
}

output "task_definition_arns" {
  value = { for k, v in aws_ecs_task_definition.services : k => v.arn }
}

output "ecs_execution_role_arn" {
  value = aws_iam_role.ecs_execution.arn
}
