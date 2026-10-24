# ================================
# Database Module for RAG System
# ================================
# Aurora PostgreSQL + pgvector. Prod runs a provisioned writer and one reader that
# doubles as the failover target, fronted by RDS Proxy. Non-prod runs Serverless v2
# with min capacity 0 (auto-pause) and no reader.

locals {
  name       = "${var.project_name}-${var.stage}"
  serverless = var.instance_class == "db.serverless"

  # Writer + optional readers. Promotion tier 1 makes the reader the preferred failover target.
  instances = { for i in range(1 + var.reader_count) : (i == 0 ? "writer" : "reader-${i}") => i }

  common_tags = {
    Project     = var.project_name
    Environment = var.stage
    ManagedBy   = "Terraform"
  }
}

# ==================================================================
# Cluster parameter group
# ==================================================================

resource "aws_rds_cluster_parameter_group" "main" {
  name        = "${local.name}-aurora-pg-params"
  family      = "aurora-postgresql${split(".", var.engine_version)[0]}"
  description = "Cluster parameters for ${local.name}"

  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements,auto_explain"
    apply_method = "pending-reboot"
  }

  # Clients must connect with sslmode=require
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  tags = local.common_tags

  lifecycle {
    create_before_destroy = true
  }
}

# ==================================================================
# Aurora cluster
# ==================================================================

resource "aws_rds_cluster" "main" {
  cluster_identifier = "${local.name}-aurora"
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned"
  engine_version     = var.engine_version
  database_name      = var.db_name
  master_username    = var.db_username

  # RDS creates and rotates the master password in Secrets Manager
  manage_master_user_password = true

  db_subnet_group_name            = var.db_subnet_group_name
  vpc_security_group_ids          = [var.db_security_group_id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.main.name

  storage_encrypted         = true
  backup_retention_period   = var.backup_retention_days
  preferred_backup_window   = "18:00-19:00"
  copy_tags_to_snapshot     = true
  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = !var.deletion_protection
  final_snapshot_identifier = var.deletion_protection ? "${local.name}-aurora-final" : null

  enabled_cloudwatch_logs_exports = ["postgresql"]

  dynamic "serverlessv2_scaling_configuration" {
    for_each = local.serverless ? [1] : []
    content {
      min_capacity             = var.serverless_min_acu
      max_capacity             = var.serverless_max_acu
      seconds_until_auto_pause = var.serverless_min_acu == 0 ? var.auto_pause_seconds : null
    }
  }

  tags = merge(
    { Name = "${local.name}-aurora" },
    local.common_tags
  )
}

resource "aws_rds_cluster_instance" "main" {
  for_each = local.instances

  identifier         = "${local.name}-aurora-${each.key}"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = var.instance_class
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version

  db_subnet_group_name = var.db_subnet_group_name
  promotion_tier       = each.value == 0 ? 0 : 1

  # Spread writer and reader across AZs so the reader is a real standby
  availability_zone = length(var.availability_zones) > 0 ? var.availability_zones[each.value % length(var.availability_zones)] : null

  performance_insights_enabled = !local.serverless || var.serverless_min_acu > 0
  auto_minor_version_upgrade   = true

  tags = merge(
    { Name = "${local.name}-aurora-${each.key}" },
    local.common_tags
  )
}

# ==================================================================
# Application database user
# ==================================================================
# The query service and ingestion Lambda connect as a least-privileged role.
# db_init creates it with this password using the master credentials.

resource "random_password" "app" {
  length           = 32
  special          = true
  override_special = "!#%*()-_=+[]{}<>:?"
}

resource "aws_secretsmanager_secret" "app_credentials" {
  name        = "${local.name}-db-app-credentials"
  description = "Application database credentials for ${local.name}"

  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "app_credentials" {
  secret_id = aws_secretsmanager_secret.app_credentials.id
  secret_string = jsonencode({
    username = var.app_db_username
    password = random_password.app.result
  })
}

# ==================================================================
# RDS Proxy (prod)
# ==================================================================

resource "aws_iam_role" "proxy" {
  count = var.enable_rds_proxy ? 1 : 0
  name  = "${local.name}-rds-proxy-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "rds.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "proxy" {
  count = var.enable_rds_proxy ? 1 : 0
  name  = "read-db-secrets"
  role  = aws_iam_role.proxy[0].id

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect   = "Allow",
      Action   = ["secretsmanager:GetSecretValue"],
      Resource = [aws_secretsmanager_secret.app_credentials.arn]
    }]
  })
}

resource "aws_db_proxy" "main" {
  count = var.enable_rds_proxy ? 1 : 0

  name                   = "${local.name}-proxy"
  engine_family          = "POSTGRESQL"
  role_arn               = aws_iam_role.proxy[0].arn
  vpc_subnet_ids         = var.proxy_subnet_ids
  vpc_security_group_ids = [var.proxy_security_group_id]
  require_tls            = true
  idle_client_timeout    = 1800

  auth {
    auth_scheme = "SECRETS"
    iam_auth    = "DISABLED"
    secret_arn  = aws_secretsmanager_secret.app_credentials.arn
  }

  tags = local.common_tags
}

resource "aws_db_proxy_default_target_group" "main" {
  count         = var.enable_rds_proxy ? 1 : 0
  db_proxy_name = aws_db_proxy.main[0].name

  connection_pool_config {
    max_connections_percent      = 90
    max_idle_connections_percent = 30
    connection_borrow_timeout    = 30
  }
}

resource "aws_db_proxy_target" "main" {
  count                 = var.enable_rds_proxy ? 1 : 0
  db_proxy_name         = aws_db_proxy.main[0].name
  target_group_name     = aws_db_proxy_default_target_group.main[0].name
  db_cluster_identifier = aws_rds_cluster.main.cluster_identifier

  depends_on = [aws_rds_cluster_instance.main]
}

# Read-only endpoint routes to the reader; vector search uses it
resource "aws_db_proxy_endpoint" "read_only" {
  count                  = var.enable_rds_proxy && var.reader_count > 0 ? 1 : 0
  db_proxy_name          = aws_db_proxy.main[0].name
  db_proxy_endpoint_name = "${local.name}-proxy-ro"
  vpc_subnet_ids         = var.proxy_subnet_ids
  vpc_security_group_ids = [var.proxy_security_group_id]
  target_role            = "READ_ONLY"

  tags = local.common_tags
}

locals {
  write_host = var.enable_rds_proxy ? aws_db_proxy.main[0].endpoint : aws_rds_cluster.main.endpoint
  read_host = (
    var.enable_rds_proxy
    ? (var.reader_count > 0 ? aws_db_proxy_endpoint.read_only[0].endpoint : aws_db_proxy.main[0].endpoint)
    : (var.reader_count > 0 ? aws_rds_cluster.main.reader_endpoint : aws_rds_cluster.main.endpoint)
  )
}

# ==================================================================
# Pre-upgrade RDS instance
# ==================================================================
# The single-AZ RDS PostgreSQL instance is replaced by Aurora. Terraform stops
# managing it (and its credentials secret) without deleting it, so its data stays
# available until someone takes a final snapshot and deletes it by hand:
#   aws rds delete-db-instance --db-instance-identifier <project>-<stage>-postgres \
#     --final-db-snapshot-identifier <project>-<stage>-postgres-final
# Remove these blocks once the old instance is gone in every environment.

removed {
  from = aws_db_instance.postgres

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_secretsmanager_secret.db_credentials

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_db_parameter_group.postgres

  lifecycle {
    destroy = false
  }
}
