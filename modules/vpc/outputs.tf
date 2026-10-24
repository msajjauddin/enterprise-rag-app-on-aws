# =========================
# VPC Module Outputs
# =========================

output "vpc_id" {
  description = "The ID of the VPC"
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "List of IDs of private subnets"
  value       = aws_subnet.private[*].id
}

output "database_subnet_ids" {
  description = "List of IDs of database subnets"
  value       = aws_subnet.database[*].id
}

output "public_subnet_ids" {
  description = "List of IDs of public subnets"
  value       = aws_subnet.public[*].id
}

output "db_subnet_group_name" {
  description = "Name of the database subnet group"
  value       = aws_db_subnet_group.main.name
}

output "lambda_security_group_id" {
  description = "ID of the Lambda security group"
  value       = aws_security_group.lambda.id
}

output "database_security_group_id" {
  description = "ID of the database security group"
  value       = aws_security_group.database.id
}

output "bastion_security_group_id" {
  description = "ID of the bastion security group"
  value       = var.create_bastion_sg ? aws_security_group.bastion[0].id : ""
}

output "nat_gateway_ips" {
  description = "Elastic IP addresses of NAT Gateways"
  value       = aws_eip.nat[*].public_ip
}

output "vpc_cidr" {
  description = "CIDR block of the VPC"
  value       = aws_vpc.main.cidr_block
}
output "alb_security_group_id" {
  description = "ID of the internal ALB security group"
  value       = aws_security_group.alb.id
}

output "ecs_security_group_id" {
  description = "ID of the Fargate task security group"
  value       = aws_security_group.ecs.id
}

output "rds_proxy_security_group_id" {
  description = "ID of the RDS Proxy security group"
  value       = aws_security_group.rds_proxy.id
}

output "cache_security_group_id" {
  description = "ID of the ElastiCache security group"
  value       = aws_security_group.cache.id
}

output "cache_subnet_group_name" {
  description = "Name of the ElastiCache subnet group"
  value       = aws_elasticache_subnet_group.main.name
}
