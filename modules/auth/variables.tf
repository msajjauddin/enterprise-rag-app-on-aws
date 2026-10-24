#Auth Module
variable "project_name" {
  description = "Name of the project"
  type        = string
}

variable "stage" {
  description = "Deployment stage (dev, staging, prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "allowed_callback_urls" {
  description = "Allowed callback URLs for Cognito"
  type        = list(string)
  default     = ["http://localhost:8501/"]
}

variable "allowed_logout_urls" {
  description = "Allowed logout URLs for Cognito"
  type        = list(string)
  default     = ["http://localhost:8501/"]
}

variable "pre_token_lambda_arn" {
  description = "ARN of the pre-token-generation Lambda"
  type        = string
}

variable "pre_token_lambda_name" {
  description = "Name of the pre-token-generation Lambda"
  type        = string
}

# -------------------------
# Corporate IdP federation
# -------------------------
variable "identity_provider_type" {
  description = "SAML, OIDC, or NONE (Cognito users only, for dev without an IdP)"
  type        = string
  default     = "NONE"

  validation {
    condition     = contains(["SAML", "OIDC", "NONE"], var.identity_provider_type)
    error_message = "identity_provider_type must be SAML, OIDC or NONE."
  }
}

variable "identity_provider_name" {
  description = "Name of the IdP in Cognito (shown on the hosted UI)"
  type        = string
  default     = "CorporateSSO"
}

variable "saml_metadata_url" {
  description = "SAML metadata URL of the corporate IdP (Azure AD / Okta)"
  type        = string
  default     = ""
}

variable "oidc_issuer" {
  description = "OIDC issuer URL of the corporate IdP"
  type        = string
  default     = ""
}

variable "oidc_client_id" {
  description = "OIDC client ID registered at the corporate IdP"
  type        = string
  default     = ""
}

variable "oidc_client_secret" {
  description = "OIDC client secret registered at the corporate IdP"
  type        = string
  default     = ""
  sensitive   = true
}

variable "idp_email_claim" {
  description = "IdP claim / SAML attribute carrying the user's email"
  type        = string
  default     = "email"
}

variable "idp_groups_claim" {
  description = "IdP claim / SAML attribute carrying the user's groups"
  type        = string
  default     = "groups"
}
