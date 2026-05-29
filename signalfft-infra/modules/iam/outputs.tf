output "role_arns" {
  description = "Map of plane names to IAM role ARNs"
  value = {
    intelligence = aws_iam_role.intelligence.arn
    decision     = aws_iam_role.decision.arn
    execution    = aws_iam_role.execution.arn
  }
}

output "role_names" {
  description = "Map of plane names to IAM role names"
  value = {
    intelligence = aws_iam_role.intelligence.name
    decision     = aws_iam_role.decision.name
    execution    = aws_iam_role.execution.name
  }
}
