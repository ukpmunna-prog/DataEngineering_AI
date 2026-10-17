terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Bucket, key and region are supplied by the pipeline with -backend-config, so nothing is hard-coded here.
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "sn-change-intel", env = var.env, managed_by = "terraform" }
  }
}

variable "env" {
  type    = string
  default = "prod"
}
variable "region" {
  type    = string
  default = "eu-north-1"
}
variable "sn_instance_url" { type = string }
variable "lambda_package" {
  type    = string
  default = "../../../build/extractor.zip"
}
variable "extractor_invocations_enabled" {
  description = "Whether scheduled and direct extractor Lambda invocations are allowed"
  type        = bool
  default     = true
}
variable "bedrock_model_id" {
  description = "Claude model or inference profile id enabled in Bedrock for this account. In eu-north-1 Claude is reached through an EU cross-region inference profile (an id starting with eu.)"
  type        = string
}
variable "vector_bucket" {
  type    = string
  default = "sn-change-intel-prod-vectors"
}
variable "vector_index" {
  type    = string
  default = "change-incident"
}

module "ingest" {
  source                        = "../../modules/ingest"
  env                           = var.env
  region                        = var.region
  sn_instance_url               = var.sn_instance_url
  lambda_package                = var.lambda_package
  extractor_invocations_enabled = var.extractor_invocations_enabled
}

module "ai" {
  source           = "../../modules/ai"
  env              = var.env
  raw_bucket       = module.ingest.raw_bucket
  raw_bucket_arn   = module.ingest.raw_bucket_arn
  kms_key_arn      = module.ingest.kms_key_arn
  dlq_arn          = module.ingest.dlq_arn
  lambda_package   = var.lambda_package
  vector_bucket    = var.vector_bucket
  vector_index     = var.vector_index
  bedrock_model_id = var.bedrock_model_id
}

output "raw_bucket" { value = module.ingest.raw_bucket }
output "servicenow_secret_name" { value = module.ingest.secret_name }
output "extractor_function" { value = module.ingest.extractor_function }
output "vector_bucket" { value = var.vector_bucket }
output "vector_index" { value = var.vector_index }
