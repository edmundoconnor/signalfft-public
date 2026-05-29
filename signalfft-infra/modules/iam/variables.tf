variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "table_arns" {
  description = "Map of DynamoDB table logical names to ARNs"
  type        = map(string)
}

variable "queue_arns" {
  description = "Map of SQS queue logical names to ARNs"
  type        = map(string)
}

variable "bucket_arn" {
  description = "S3 artifacts bucket ARN"
  type        = string
}

variable "account_id" {
  description = "AWS account ID"
  type        = string
}
