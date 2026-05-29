output "table_names" {
  description = "Map of table logical names to actual table names"
  value = {
    entities            = aws_dynamodb_table.entities.name
    events              = aws_dynamodb_table.events.name
    features            = aws_dynamodb_table.features.name
    signals             = aws_dynamodb_table.signals.name
    waves               = aws_dynamodb_table.waves.name
    narratives          = aws_dynamodb_table.narratives.name
    attention_field     = aws_dynamodb_table.attention_field.name
    trade_candidates    = aws_dynamodb_table.trade_candidates.name
    execution_telemetry = aws_dynamodb_table.execution_telemetry.name
    outcomes            = aws_dynamodb_table.outcomes.name
    shadow_scores       = aws_dynamodb_table.shadow_scores.name
    semantic_deltas     = aws_dynamodb_table.semantic_deltas.name
    graph_edges         = aws_dynamodb_table.graph_edges.name
  }
}

output "table_arns" {
  description = "Map of table logical names to ARNs"
  value = {
    entities            = aws_dynamodb_table.entities.arn
    events              = aws_dynamodb_table.events.arn
    features            = aws_dynamodb_table.features.arn
    signals             = aws_dynamodb_table.signals.arn
    waves               = aws_dynamodb_table.waves.arn
    narratives          = aws_dynamodb_table.narratives.arn
    attention_field     = aws_dynamodb_table.attention_field.arn
    trade_candidates    = aws_dynamodb_table.trade_candidates.arn
    execution_telemetry = aws_dynamodb_table.execution_telemetry.arn
    outcomes            = aws_dynamodb_table.outcomes.arn
    shadow_scores       = aws_dynamodb_table.shadow_scores.arn
    semantic_deltas     = aws_dynamodb_table.semantic_deltas.arn
    graph_edges         = aws_dynamodb_table.graph_edges.arn
  }
}
