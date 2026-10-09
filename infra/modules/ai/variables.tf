variable "env" { type = string }
variable "raw_bucket" { type = string }
variable "raw_bucket_arn" { type = string }
variable "kms_key_arn" { type = string }
variable "lambda_package" { type = string }
variable "vector_bucket" { type = string }
variable "vector_index" { type = string }
variable "bedrock_model_id" {
  description = "Claude model id or inference profile id enabled for your account in Bedrock"
  type        = string
}
variable "embed_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}
variable "embed_schedule" {
  type    = string
  default = "rate(1 hour)"
}
variable "score_schedule" {
  type    = string
  default = "cron(0 6 * * ? *)"
}
variable "dlq_arn" {
  description = "SQS queue that receives failed asynchronous invocations"
  type        = string
}
variable "tags" {
  type    = map(string)
  default = {}
}
