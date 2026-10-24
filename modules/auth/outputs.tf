#Auth Module
output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_user_pool_arn" {
  value = aws_cognito_user_pool.main.arn
}

output "cognito_app_client_id" {
  value = aws_cognito_user_pool_client.streamlit_client.id
}

output "cognito_eval_client_id" {
  value = aws_cognito_user_pool_client.eval_client.id
}

output "eval_client_secret_arn" {
  value = aws_secretsmanager_secret.eval_client.arn
}

output "cognito_domain" {
  value = aws_cognito_user_pool_domain.main.domain
}

output "cognito_issuer" {
  value = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}
