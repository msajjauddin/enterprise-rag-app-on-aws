output "api_endpoint" {
  description = "Public HTTPS endpoint of the query API (CloudFront)"
  value       = length(var.domain_aliases) > 0 ? "https://${var.domain_aliases[0]}" : "https://${aws_cloudfront_distribution.api.domain_name}"
}

output "cloudfront_distribution_id" {
  description = "ID of the CloudFront distribution"
  value       = aws_cloudfront_distribution.api.id
}

output "cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "service_name" {
  description = "ECS service name"
  value       = aws_ecs_service.app.name
}

output "alb_arn_suffix" {
  description = "ALB ARN suffix for CloudWatch metrics"
  value       = aws_lb.main.arn_suffix
}

output "log_group_name" {
  description = "Log group of the query service"
  value       = aws_cloudwatch_log_group.service.name
}
