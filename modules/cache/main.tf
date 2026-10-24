# ================================================
# Cache Module for RAG System (ElastiCache Valkey)
# ================================================
# Semantic answer cache + per-user rate-limit counters for the query service.
# Prod: primary + replica across AZs with automatic failover. Non-prod: one small node.

locals {
  name = "${var.project_name}-${var.stage}"

  common_tags = {
    Project     = var.project_name
    Environment = var.stage
    ManagedBy   = "Terraform"
  }
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${local.name}-cache"
  description          = "Semantic cache and rate limits for ${local.name}"

  engine               = "valkey"
  engine_version       = var.engine_version
  node_type            = var.node_type
  num_cache_clusters   = var.node_count
  parameter_group_name = "default.valkey${split(".", var.engine_version)[0]}"
  port                 = 6379

  subnet_group_name  = var.subnet_group_name
  security_group_ids = [var.security_group_id]

  automatic_failover_enabled = var.node_count > 1
  multi_az_enabled           = var.node_count > 1

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  snapshot_retention_limit = 0
  apply_immediately        = true

  tags = merge(
    { Name = "${local.name}-cache" },
    local.common_tags
  )
}
