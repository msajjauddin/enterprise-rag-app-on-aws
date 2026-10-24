output "cluster_id" {
  description = "Identifier of the Aurora cluster"
  value       = aws_rds_cluster.main.cluster_identifier
}

output "writer_instance_id" {
  description = "Identifier of the writer instance"
  value       = aws_rds_cluster_instance.main["writer"].identifier
}

output "cluster_endpoint" {
  description = "Aurora writer endpoint (used directly by db_init for migrations)"
  value       = aws_rds_cluster.main.endpoint
}

output "write_host" {
  description = "Host the application uses for writes (proxy or cluster writer endpoint)"
  value       = local.write_host
}

output "read_host" {
  description = "Host the application uses for reads (proxy read-only endpoint or cluster reader endpoint)"
  value       = local.read_host
}

output "db_name" {
  description = "Database name"
  value       = var.db_name
}

output "master_secret_arn" {
  description = "ARN of the RDS-managed master credentials secret"
  value       = aws_rds_cluster.main.master_user_secret[0].secret_arn
}

output "app_secret_arn" {
  description = "ARN of the application database credentials secret"
  value       = aws_secretsmanager_secret.app_credentials.arn
}

output "proxy_name" {
  description = "Name of the RDS Proxy, empty when disabled"
  value       = var.enable_rds_proxy ? aws_db_proxy.main[0].name : ""
}
