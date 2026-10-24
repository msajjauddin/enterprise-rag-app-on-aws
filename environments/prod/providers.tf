provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.stage
      ManagedBy   = "Terraform"
    }
  }
}

# Required provider versions for better stability
terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 6.4+ for ECS-native blue/green deployments
      version = ">= 6.4.0, < 7.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  required_version = ">= 1.5.0"
}
