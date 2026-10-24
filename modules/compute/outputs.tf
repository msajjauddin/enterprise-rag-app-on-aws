#Compute Module
# =========================
# Lambda Function Outputs
# =========================

output "document_processor_arn" {
  description = "ARN of the document processor Lambda function"
  value       = aws_lambda_function.document_processor.arn
}

output "document_processor_name" {
  description = "Name of the document processor Lambda function"
  value       = aws_lambda_function.document_processor.function_name
}

output "db_init_name" {
  description = "Name of the db_init Lambda function"
  value       = aws_lambda_function.db_init.function_name
}

output "token_enricher_arn" {
  description = "ARN of the Cognito pre-token-generation Lambda"
  value       = aws_lambda_function.token_enricher.arn
}

output "token_enricher_name" {
  description = "Name of the Cognito pre-token-generation Lambda"
  value       = aws_lambda_function.token_enricher.function_name
}

output "ingestion_queue_url" {
  description = "URL of the ingestion queue (connectors publish here)"
  value       = aws_sqs_queue.ingestion.id
}

output "ingestion_queue_arn" {
  description = "ARN of the ingestion queue"
  value       = aws_sqs_queue.ingestion.arn
}

output "ingestion_queue_name" {
  description = "Name of the ingestion queue"
  value       = aws_sqs_queue.ingestion.name
}

output "ingestion_dlq_name" {
  description = "Name of the ingestion dead-letter queue"
  value       = aws_sqs_queue.ingestion_dlq.name
}

output "lambda_role_arn" {
  description = "ARN of the IAM role for ingestion Lambda functions"
  value       = aws_iam_role.lambda_role.arn
}
