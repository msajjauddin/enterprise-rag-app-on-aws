output "primary_endpoint" {
  description = "Primary endpoint address of the cache"
  value       = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "replication_group_id" {
  description = "ID of the replication group"
  value       = aws_elasticache_replication_group.main.id
}
