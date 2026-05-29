output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN"
  value       = module.ecs_cluster.cluster_arn
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = module.ecs_cluster.cluster_name
}

output "s3_bucket_name" {
  description = "S3 artifacts bucket name"
  value       = module.s3.bucket_name
}

output "sqs_queue_urls" {
  description = "SQS queue URLs"
  value       = module.sqs.queue_urls
}

output "dynamodb_table_names" {
  description = "DynamoDB table names"
  value       = module.dynamodb.table_names
}

output "iam_role_arns" {
  value = module.iam.role_arns
}

output "role_arns" {
  description = "IAM role ARNs by plane"
  value       = module.iam.role_arns
}

output "ecr_repository_urls" {
  description = "ECR repository URLs"
  value       = module.ecr.repository_urls
}

output "alb_dns_name" {
  description = "Dashboard ALB DNS name"
  value       = module.alb.alb_dns_name
}

output "dashboard_ui_bucket" {
  description = "Dashboard UI S3 bucket"
  value       = module.dashboard_hosting.bucket_name
}

output "ecs_service_names" {
  description = "ECS service names"
  value       = module.ecs_services.service_names
}
