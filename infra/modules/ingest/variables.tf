variable "env" { type = string }
variable "region" { type = string }
variable "tables" {
  type    = list(string)
  default = ["change_request", "incident"]
}
variable "sn_instance_url" { type = string }
variable "schedule_expression" {
  type    = string
  default = "rate(1 hour)"
}
variable "lambda_package" {
  description = "Path to the zipped extractor package"
  type        = string
}
variable "extractor_invocations_enabled" {
  description = "Whether scheduled and direct extractor Lambda invocations are allowed"
  type        = bool
}
variable "tags" {
  type    = map(string)
  default = {}
}
