output "bus_name" {
  value = aws_cloudwatch_event_bus.main.name
}

output "bus_arn" {
  value = aws_cloudwatch_event_bus.main.arn
}

output "rule_arns" {
  value = {
    collector_schedule = aws_cloudwatch_event_rule.collector_schedule.arn
    finnhub_schedule   = aws_cloudwatch_event_rule.finnhub_schedule.arn
    bluesky_schedule   = aws_cloudwatch_event_rule.bluesky_schedule.arn
    outcome_schedule   = aws_cloudwatch_event_rule.outcome_schedule.arn
  }
}
