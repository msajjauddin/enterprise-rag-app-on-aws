# =========================
# VPC Module for RAG Application
# =========================
# Creates a VPC with public/private subnets, NAT gateways, and security groups

# =========================
# Locals
# =========================

locals {
  name = "${var.project_name}-${var.stage}"

  common_tags = {
    Project     = var.project_name
    Environment = var.stage
    ManagedBy   = "Terraform"
  }
}

# =========================
# VPC & Internet Gateway
# =========================

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(
    { Name = "${local.name}-vpc" },
    local.common_tags
  )

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name}-igw"
  }
}

# =========================
# Availability Zones
# =========================

data "aws_availability_zones" "available" {
  state = "available"
}

# =========================
# Subnets
# =========================

resource "aws_subnet" "public" {
  count             = var.az_count
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  map_public_ip_on_launch = true

  tags = {
    Name = "${local.name}-public-subnet-${count.index + 1}"
  }
}

resource "aws_subnet" "private" {
  count             = var.az_count
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + var.az_count)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "${local.name}-private-subnet-${count.index + 1}"
  }
}

resource "aws_subnet" "database" {
  count             = var.az_count
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 2 * var.az_count)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "${local.name}-db-subnet-${count.index + 1}"
  }
}

# =========================
# NAT Gateways & EIPs
# =========================

resource "aws_eip" "nat" {
  count  = var.az_count
  domain = "vpc"

  tags = {
    Name = "${local.name}-nat-eip-${count.index + 1}"
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  count         = var.single_nat_gateway ? 1 : var.az_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(
    { Name = "${local.name}-nat-${count.index + 1}" },
    local.common_tags
  )

  depends_on = [aws_internet_gateway.main]
}

# =========================
# Route Tables
# =========================

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${local.name}-public-route-table"
  }
}

resource "aws_route_table" "private" {
  count  = var.az_count
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = var.single_nat_gateway ? aws_nat_gateway.main[0].id : aws_nat_gateway.main[count.index].id
  }

  tags = merge(
    { Name = "${local.name}-private-route-table-${count.index + 1}" },
    local.common_tags
  )
}

resource "aws_route_table_association" "public" {
  count          = var.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = var.az_count
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# =========================
# VPC Flow Logs
# =========================

resource "aws_iam_role" "flow_logs" {
  name = "${var.project_name}-${var.stage}-flow-logs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action = "sts:AssumeRole",
        Effect = "Allow",
        Principal = {
          Service = "vpc-flow-logs.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "flow_logs" {
  role       = aws_iam_role.flow_logs.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

resource "aws_cloudwatch_log_group" "flow_log" {
  count             = var.enable_flow_logs ? 1 : 0
  name              = "/aws/vpc/flowlogs/${local.name}"
  retention_in_days = 7

  tags = local.common_tags
}

resource "aws_flow_log" "main" {
  count                = var.enable_flow_logs ? 1 : 0
  log_destination      = aws_cloudwatch_log_group.flow_log[0].arn
  log_destination_type = "cloud-watch-logs"
  iam_role_arn         = aws_iam_role.flow_logs.arn
  traffic_type         = "ALL"
  vpc_id               = aws_vpc.main.id

  tags = merge(
    { Name = "${local.name}-vpc-flow-log" },
    local.common_tags
  )
}

# =========================
# Security Groups
# =========================

resource "aws_security_group" "bastion" {
  count       = var.create_bastion_sg ? 1 : 0
  name        = "${local.name}-bastion-sg"
  description = "Security group for bastion hosts"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.bastion_allowed_cidr
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(
    { Name = "${local.name}-bastion-sg" },
    local.common_tags
  )
}

# Ingestion Lambdas (document processor, db init)
resource "aws_security_group" "lambda" {
  name        = "${local.name}-lambda-sg"
  description = "Security group for Lambda functions"
  vpc_id      = aws_vpc.main.id

  # Outbound to AWS endpoints, the database tier and the internet via NAT
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.name}-lambda-sg"
  }
}

# Internal ALB in front of the query service. Only CloudFront (VPC origin) may reach it.
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb-sg"
  description = "Internal ALB, reachable from CloudFront only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "HTTP from CloudFront VPC origin"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = merge(
    { Name = "${local.name}-alb-sg" },
    local.common_tags
  )
}

# Fargate tasks running the FastAPI query service
resource "aws_security_group" "ecs" {
  name        = "${local.name}-ecs-sg"
  description = "Fargate query service tasks"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "App port from the ALB"
    from_port       = var.app_port
    to_port         = var.app_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  # Outbound to AWS endpoints, the database tier and the internet via NAT (MCP web search, ECR, JWKS)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(
    { Name = "${local.name}-ecs-sg" },
    local.common_tags
  )
}

resource "aws_security_group" "rds_proxy" {
  name        = "${local.name}-rds-proxy-sg"
  description = "RDS Proxy, reachable from Fargate and Lambda only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id, aws_security_group.lambda.id]
  }

  egress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = merge(
    { Name = "${local.name}-rds-proxy-sg" },
    local.common_tags
  )
}

resource "aws_security_group" "database" {
  name        = "${local.name}-db-sg"
  description = "Security group for Aurora PostgreSQL"
  vpc_id      = aws_vpc.main.id

  # Prod connects only through RDS Proxy. Non-prod has no proxy, so the app
  # security groups connect directly (enable_rds_proxy = false).
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = var.enable_rds_proxy ? [aws_security_group.rds_proxy.id] : [aws_security_group.rds_proxy.id, aws_security_group.ecs.id, aws_security_group.lambda.id]
  }

  # db_init Lambda always connects directly to the writer to run migrations
  ingress {
    description     = "Schema migrations from Lambda"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.lambda.id]
  }

  tags = {
    Name = "${local.name}-db-sg"
  }
}

resource "aws_security_group" "cache" {
  name        = "${local.name}-cache-sg"
  description = "ElastiCache (Valkey), reachable from Fargate only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  tags = merge(
    { Name = "${local.name}-cache-sg" },
    local.common_tags
  )
}

resource "aws_security_group" "endpoints" {
  name        = "${local.name}-endpoints-sg"
  description = "Interface VPC endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from inside the VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = merge(
    { Name = "${local.name}-endpoints-sg" },
    local.common_tags
  )
}

# =========================
# DB Subnet Group
# =========================

resource "aws_db_subnet_group" "main" {
  name        = "${local.name}-db-subnet-group"
  description = "Database subnet group for ${local.name}"
  subnet_ids  = aws_subnet.database[*].id

  tags = {
    Name = "${local.name}-db-subnet-group"
  }
}

# =========================
# VPC Endpoints
# =========================

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [for rt in aws_route_table.private : rt.id]

  tags = {
    Name = "${local.name}-s3-endpoint"
  }
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [for rt in aws_route_table.private : rt.id]

  tags = {
    Name = "${local.name}-dynamodb-endpoint"
  }
}

# Interface endpoints keep Bedrock, Secrets Manager and CloudWatch Logs traffic on the AWS network
resource "aws_vpc_endpoint" "interface" {
  for_each = toset(var.interface_endpoints)

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(
    { Name = "${local.name}-${each.value}-endpoint" },
    local.common_tags
  )
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "${local.name}-cache-subnet-group"
  subnet_ids = aws_subnet.database[*].id
}
