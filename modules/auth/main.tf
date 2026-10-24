# /modules/auth/main.tf
# Cognito user pool federated with the corporate IdP (SAML or OIDC). Users sign in
# through the hosted UI; their IdP groups are mapped to custom:groups and copied
# into cognito:groups by the pre-token-generation Lambda, so the access token
# carries user + groups for document-level access control.

locals {
  idp_name                     = var.identity_provider_name
  supported_identity_providers = var.identity_provider_type != "NONE" ? [local.idp_name] : ["COGNITO"]
}

resource "aws_cognito_user_pool" "main" {
  name = "${var.project_name}-${var.stage}-user-pool"

  # Pre-token-generation V2 (group claims in access tokens) needs the Essentials tier
  user_pool_tier = "ESSENTIALS"

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_numbers   = true
    require_symbols   = true
    require_uppercase = true
  }

  # No self sign-up: users come from the corporate IdP (or are created by an admin in dev)
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  schema {
    name                = "email"
    attribute_data_type = "String"
    mutable             = true
    required            = true
  }

  schema {
    name                     = "groups"
    attribute_data_type      = "String"
    mutable                  = true
    required                 = false
    developer_only_attribute = false

    string_attribute_constraints {
      min_length = 0
      max_length = 2048
    }
  }

  lambda_config {
    pre_token_generation_config {
      lambda_arn     = var.pre_token_lambda_arn
      lambda_version = "V2_0"
    }
  }

  mfa_configuration = "OFF"
}

resource "aws_lambda_permission" "cognito_pre_token" {
  statement_id  = "AllowCognitoPreTokenGeneration"
  action        = "lambda:InvokeFunction"
  function_name = var.pre_token_lambda_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.main.arn
}

locals {
  # Remove "aws" from the domain prefix since it's a reserved word in Cognito
  cognito_domain_prefix = replace("${var.project_name}-${var.stage}-auth", "aws", "")
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = local.cognito_domain_prefix
  user_pool_id = aws_cognito_user_pool.main.id
}

# =========================
# Corporate identity provider
# =========================

resource "aws_cognito_identity_provider" "saml" {
  count         = var.identity_provider_type == "SAML" ? 1 : 0
  user_pool_id  = aws_cognito_user_pool.main.id
  provider_name = local.idp_name
  provider_type = "SAML"

  provider_details = {
    MetadataURL = var.saml_metadata_url
    IDPSignout  = "true"
  }

  attribute_mapping = {
    email           = var.idp_email_claim
    "custom:groups" = var.idp_groups_claim
  }
}

resource "aws_cognito_identity_provider" "oidc" {
  count         = var.identity_provider_type == "OIDC" ? 1 : 0
  user_pool_id  = aws_cognito_user_pool.main.id
  provider_name = local.idp_name
  provider_type = "OIDC"

  provider_details = {
    client_id                 = var.oidc_client_id
    client_secret             = var.oidc_client_secret
    oidc_issuer               = var.oidc_issuer
    authorize_scopes          = "openid email profile"
    attributes_request_method = "GET"
  }

  attribute_mapping = {
    email           = var.idp_email_claim
    "custom:groups" = var.idp_groups_claim
  }
}

# =========================
# App clients
# =========================

# Streamlit UI: authorization-code flow through the hosted UI (public client)
resource "aws_cognito_user_pool_client" "streamlit_client" {
  name                   = "${var.project_name}-${var.stage}-streamlit-client"
  user_pool_id           = aws_cognito_user_pool.main.id
  generate_secret        = false
  refresh_token_validity = 12
  access_token_validity  = 1
  id_token_validity      = 1

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = local.supported_identity_providers

  explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"]

  callback_urls = var.allowed_callback_urls
  logout_urls   = var.allowed_logout_urls

  read_attributes = ["email", "custom:groups"]

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "hours"
  }

  depends_on = [aws_cognito_identity_provider.saml, aws_cognito_identity_provider.oidc]
}

# Machine-to-machine client used by the CI golden-set evaluation
resource "aws_cognito_resource_server" "api" {
  identifier   = "rag-api"
  name         = "${var.project_name}-${var.stage}-api"
  user_pool_id = aws_cognito_user_pool.main.id

  scope {
    scope_name        = "eval"
    scope_description = "Run golden-set evaluation queries"
  }
}

resource "aws_cognito_user_pool_client" "eval_client" {
  name                                 = "${var.project_name}-${var.stage}-eval-client"
  user_pool_id                         = aws_cognito_user_pool.main.id
  generate_secret                      = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["${aws_cognito_resource_server.api.identifier}/eval"]
  supported_identity_providers         = ["COGNITO"]
  access_token_validity                = 1

  token_validity_units {
    access_token = "hours"
  }
}

resource "aws_secretsmanager_secret" "eval_client" {
  name        = "${var.project_name}-${var.stage}-eval-client"
  description = "Cognito client credentials for CI golden-set evaluation"
}

resource "aws_secretsmanager_secret_version" "eval_client" {
  secret_id = aws_secretsmanager_secret.eval_client.id
  secret_string = jsonencode({
    client_id     = aws_cognito_user_pool_client.eval_client.id
    client_secret = aws_cognito_user_pool_client.eval_client.client_secret
    token_url     = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com/oauth2/token"
    scope         = "${aws_cognito_resource_server.api.identifier}/eval"
  })
}
