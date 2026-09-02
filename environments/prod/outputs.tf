# environments/prod/outputs.tf
output "api_endpoint" {
  description = "API Gateway endpoint URL"
  value       = module.api.api_endpoint
}

output "auth_endpoint" {
  description = "Auth endpoint URL"  
  value       = module.api.auth_endpoint
}

output "cognito_app_client_id" {
  description = "Cognito App Client ID"
  value       = module.api.cognito_app_client_id
  sensitive   = true
}

output "cognito_domain" {
  description = "Cognito domain"
  value       = module.api.cognito_domain
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = module.api.cognito_user_pool_id
}

output "db_endpoint" {
  description = "Database endpoint"
  value       = module.api.db_endpoint
}

output "document_bucket" {
  description = "Document bucket name"
  value       = module.api.document_bucket
}

output "dynamodb_table" {
  description = "DynamoDB table name"
  value       = module.api.dynamodb_table
}

output "vpc_id" {
  description = "VPC ID"
  value       = module.api.vpc_id
}

output "db_credentials_secret_arn" {
  description = "DB credentials secret ARN"
  value       = module.api.db_credentials_secret_arn
  sensitive   = true
}