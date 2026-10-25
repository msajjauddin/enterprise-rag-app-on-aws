# =========================
# Root terraform.tfvars (staging)
# =========================

# -------------------------
# Project Settings
# -------------------------
project_name = "enterprise-rag-app-on-aws"
stage        = "staging"
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
enable_flow_logs     = false
create_bastion_sg    = false
bastion_allowed_cidr = ["10.0.0.0/16"]

# -------------------------
# Storage Settings
# -------------------------
enable_lifecycle_rules      = true
standard_ia_transition_days = 90

# -------------------------
# Database: Serverless v2, min 0 (auto-pause), no reader, no proxy
# -------------------------
db_instance_class     = "db.serverless"
db_reader_count       = 0
db_serverless_min_acu = 0
db_serverless_max_acu = 4
db_name               = "ragapp"
db_username           = "ragadmin"
enable_rds_proxy      = false

# -------------------------
# Cache: single small node
# -------------------------
cache_node_type  = "cache.t4g.micro"
cache_node_count = 1

# -------------------------
# Ingestion Lambda
# -------------------------
lambda_memory_size = 2048

# -------------------------
# Query service: scaled to zero outside working hours
# -------------------------
task_cpu          = 1024
task_memory       = 2048
default_min_tasks = 1
default_max_tasks = 2
schedule_timezone = "Asia/Kolkata"
scaling_schedules = [
  { name = "weekday-office-hours", schedule = "cron(30 8 ? * MON-FRI *)", min_tasks = 1, max_tasks = 2 },
  { name = "after-hours", schedule = "cron(0 19 ? * MON-FRI *)", min_tasks = 0, max_tasks = 0 },
]

# -------------------------
# Auth: Cognito users only until the IdP test app is registered
# -------------------------
identity_provider_type = "NONE"
ui_callback_urls       = ["http://localhost:8501/"]

# -------------------------
# Query behaviour
# -------------------------
mcp_server_url        = ""
rate_limit_per_minute = 30
eval_sample_rate      = 0.10
