output "dashboard_url" {
  description = "CloudWatch dashboard URL"
  value       = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${local.prefix}-operations"
}

output "sns_topic_arn" {
  description = "SNS topic ARN for pipeline alerts"
  value       = aws_sns_topic.pipeline_alerts.arn
}
