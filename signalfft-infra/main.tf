# SignalFFT Infrastructure — Root Module
# Compose all sub-modules here

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "YOUR_TERRAFORM_STATE_BUCKET"
    key            = "signalfft/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "YOUR_TERRAFORM_LOCK_TABLE"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region
}

# -----------------------------------------------------------------------------
# VPC
# -----------------------------------------------------------------------------
module "vpc" {
  source = "./modules/vpc"

  environment       = var.environment
  aws_region        = var.aws_region
  nat_gateway_count = var.nat_gateway_count
}

# -----------------------------------------------------------------------------
# Security Groups
# -----------------------------------------------------------------------------
module "security_groups" {
  source = "./modules/security_groups"

  environment = var.environment
  vpc_id      = module.vpc.vpc_id
}

# -----------------------------------------------------------------------------
# ECS Cluster
# -----------------------------------------------------------------------------
module "ecs_cluster" {
  source = "./modules/ecs_cluster"

  environment = var.environment
}

# -----------------------------------------------------------------------------
# ECR Repositories
# -----------------------------------------------------------------------------
module "ecr" {
  source = "./modules/ecr"

  environment = var.environment
}

# -----------------------------------------------------------------------------
# S3 Artifacts Bucket
# -----------------------------------------------------------------------------
module "s3" {
  source = "./modules/s3"

  environment = var.environment
}

# -----------------------------------------------------------------------------
# SQS Queues
# -----------------------------------------------------------------------------
module "sqs" {
  source = "./modules/sqs"

  environment = var.environment
}

# -----------------------------------------------------------------------------
# EventBridge
# -----------------------------------------------------------------------------
module "eventbridge" {
  source = "./modules/eventbridge"

  environment             = var.environment
  collector_schedule_rate = var.collector_schedule_rate
  edgar_collector_arn     = module.lambda.edgar_collector_arn
  finnhub_collector_arn   = module.lambda.finnhub_collector_arn
  bluesky_collector_arn   = module.lambda.bluesky_collector_arn
  outcome_collector_arn   = module.lambda.outcome_collector_arn
}

# -----------------------------------------------------------------------------
# Lambda (EDGAR Collector)
# -----------------------------------------------------------------------------
module "lambda" {
  source = "./modules/lambda"

  environment            = var.environment
  intelligence_role_arn  = module.iam.role_arns["intelligence"]
  s3_bucket_name         = module.s3.bucket_name
  events_table_name      = module.dynamodb.table_names["events"]
  raw_events_queue_url   = module.sqs.queue_urls["raw-events"]
  outcomes_table_name    = module.dynamodb.table_names["outcomes"]
  filing_fetch_queue_url = module.sqs.queue_urls["filing-fetch"]
  filing_ready_queue_url = module.sqs.queue_urls["filing-ready"]
  filing_fetch_queue_arn = module.sqs.queue_arns["filing-fetch"]
  edgar_user_agent       = var.edgar_user_agent
  bluesky_handle         = var.bluesky_handle
}

# -----------------------------------------------------------------------------
# DynamoDB Tables
# -----------------------------------------------------------------------------
module "dynamodb" {
  source = "./modules/dynamodb"

  environment    = var.environment
  billing_mode   = var.billing_mode
  read_capacity  = var.read_capacity
  write_capacity = var.write_capacity
  ttl_enabled    = var.ttl_enabled

  tags = {
    project     = "signalfft"
    environment = var.environment
  }
}

# -----------------------------------------------------------------------------
# IAM Roles (Plane Isolation)
# -----------------------------------------------------------------------------
module "iam" {
  source = "./modules/iam"

  environment = var.environment
  table_arns  = module.dynamodb.table_arns
  queue_arns  = module.sqs.queue_arns
  bucket_arn  = module.s3.bucket_arn
  account_id  = var.account_id
}

# -----------------------------------------------------------------------------
# ALB (Dashboard)
# -----------------------------------------------------------------------------
module "alb" {
  source = "./modules/alb"

  environment       = var.environment
  vpc_id            = module.vpc.vpc_id
  public_subnet_ids = module.vpc.public_subnet_ids
  alb_sg_id         = module.security_groups.alb_sg_id
}

# -----------------------------------------------------------------------------
# Dashboard Hosting (S3 for frontend)
# -----------------------------------------------------------------------------
module "dashboard_hosting" {
  source = "./modules/dashboard_hosting"

  environment = var.environment
}

# -----------------------------------------------------------------------------
# ECS Services (Task Definitions + Services)
# -----------------------------------------------------------------------------
module "ecs_services" {
  source = "./modules/ecs_services"

  environment                    = var.environment
  account_id                     = var.account_id
  aws_region                     = var.aws_region
  cluster_id                     = module.ecs_cluster.cluster_id
  cluster_name                   = module.ecs_cluster.cluster_name
  public_subnet_ids              = module.vpc.public_subnet_ids
  ecs_tasks_sg_id                = module.security_groups.ecs_tasks_sg_id
  intelligence_role_arn          = module.iam.role_arns["intelligence"]
  decision_role_arn              = module.iam.role_arns["decision"]
  execution_role_arn             = module.iam.role_arns["execution"]
  ecr_repository_urls            = module.ecr.repository_urls
  queue_urls                     = module.sqs.queue_urls
  table_names                    = module.dynamodb.table_names
  s3_bucket_name                 = module.s3.bucket_name
  dashboard_target_group_arn     = module.alb.target_group_arn
  dashboard_cognito_user_pool_id = var.dashboard_cognito_user_pool_id
  dashboard_cognito_client_id    = var.dashboard_cognito_client_id
  dashboard_allowed_origins      = var.dashboard_allowed_origins
  dashboard_auth_required        = var.dashboard_auth_required
}

# -----------------------------------------------------------------------------
# CloudWatch Dashboard & Alerting
# -----------------------------------------------------------------------------
module "cloudwatch" {
  source = "./modules/cloudwatch"

  environment = var.environment
  alert_email = var.alert_email
}
