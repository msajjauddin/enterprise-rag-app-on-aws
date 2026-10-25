# =========================
# Root terraform.tfvars (prod)
# =========================
# Sized for ~2,000 daily users / 200-500 concurrent. See docs/architecture/production-upgrade.md.

# -------------------------
# Project Settings
# -------------------------
project_name = "enterprise-rag-app-on-aws"
stage        = "prod"
aws_region   = "us-east-1"

# -------------------------
# Monitoring
# -------------------------
alert_email = "msajjauddin@gmail.com"

# -------------------------
# VPC Settings (2 AZs, one NAT per AZ)
# -------------------------
vpc_cidr             = "10.0.0.0/16"
az_count             = 2
single_nat_gateway   = false
enable_flow_logs     = true
create_bastion_sg    = false
bastion_allowed_cidr = ["10.0.0.0/16"]

# -------------------------
# Storage Settings
# -------------------------
enable_lifecycle_rules      = true
standard_ia_transition_days = 90

# -------------------------
# Database: r6g.large writer + reader (standby), RDS Proxy
# -------------------------
db_instance_class        = "db.r6g.large"
db_reader_count          = 1
db_name                  = "ragapp"
db_username              = "ragadmin"
db_deletion_protection   = true
db_backup_retention_days = 14
enable_rds_proxy         = true

# -------------------------
# Cache: primary + replica
# -------------------------
cache_node_type  = "cache.t4g.small"
cache_node_count = 2

# -------------------------
# Ingestion Lambda
# -------------------------
lambda_memory_size = 2048

# -------------------------
# Query service: 1 vCPU / 2 GB tasks, scheduled bounds + target tracking
# -------------------------
task_cpu          = 1024
task_memory       = 2048
default_min_tasks = 2
default_max_tasks = 4
schedule_timezone = "Asia/Kolkata"
scaling_schedules = [
  { name = "weekday-office-hours", schedule = "cron(30 8 ? * MON-FRI *)", min_tasks = 4, max_tasks = 12 },
  # Also covers weekends: bounds stay low from Friday 19:00 until Monday 08:30
  { name = "after-hours", schedule = "cron(0 19 ? * MON-FRI *)", min_tasks = 2, max_tasks = 4 },
]

# -------------------------
# Auth: corporate IdP (fill in before the first prod apply)
# -------------------------
identity_provider_type = "SAML"
identity_provider_name = "CorporateSSO"
saml_metadata_url      = "https://login.microsoftonline.com/REPLACE_TENANT_ID/federationmetadata/2007-06/federationmetadata.xml?appid=REPLACE_APP_ID"
idp_email_claim        = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
idp_groups_claim       = "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups"
ui_callback_urls       = ["http://localhost:8501/"]

# -------------------------
# Query behaviour
# -------------------------
mcp_server_url        = ""
rate_limit_per_minute = 20
eval_sample_rate      = 0.05
