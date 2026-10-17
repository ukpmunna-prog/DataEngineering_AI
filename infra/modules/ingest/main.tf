locals {
  name = "sn-change-intel-${var.env}"
}

data "aws_caller_identity" "current" {}

resource "aws_kms_key" "data" {
  description         = "${local.name} data key"
  enable_key_rotation = true
  tags                = var.tags
}

resource "aws_s3_bucket" "raw" {
  bucket = "${local.name}-raw-${data.aws_caller_identity.current.account_id}"
  tags   = var.tags
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    id     = "tier-old-raw"
    status = "Enabled"
    filter {}
    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
}

resource "aws_dynamodb_table" "watermark" {
  name         = "${local.name}-watermark"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "table_name"
  attribute {
    name = "table_name"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
  server_side_encryption { enabled = true }
  tags = var.tags
}

resource "aws_secretsmanager_secret" "servicenow" {
  name       = "${local.name}/servicenow"
  kms_key_id = aws_kms_key.data.arn
  tags       = var.tags
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${local.name}-extract-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = aws_kms_key.data.id
  tags                      = var.tags
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "extractor" {
  name               = "${local.name}-extractor"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "extractor" {
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.raw.arn}/raw/*"]
  }
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.watermark.arn]
  }
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.servicenow.arn]
  }
  statement {
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [aws_kms_key.data.arn]
  }
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.dlq.arn]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.extractor.arn}:*"]
  }
}

resource "aws_iam_role_policy" "extractor" {
  role   = aws_iam_role.extractor.id
  policy = data.aws_iam_policy_document.extractor.json
}

resource "aws_cloudwatch_log_group" "extractor" {
  name              = "/aws/lambda/${local.name}-extractor"
  retention_in_days = 30
  tags              = var.tags
}

resource "aws_lambda_function" "extractor" {
  function_name                  = "${local.name}-extractor"
  role                           = aws_iam_role.extractor.arn
  handler                        = "src.extractor.handler.handler"
  runtime                        = "python3.12"
  timeout                        = 840
  memory_size                    = 512
  filename                       = var.lambda_package
  source_code_hash               = filebase64sha256(var.lambda_package)
  reserved_concurrent_executions = var.extractor_invocations_enabled ? -1 : 0

  environment {
    variables = {
      SN_INSTANCE_URL     = var.sn_instance_url
      SN_SECRET_ARN       = aws_secretsmanager_secret.servicenow.arn
      RAW_BUCKET          = aws_s3_bucket.raw.bucket
      WATERMARK_TABLE     = aws_dynamodb_table.watermark.name
      MAX_RUNTIME_SECONDS = "780"
    }
  }

  dead_letter_config { target_arn = aws_sqs_queue.dlq.arn }
  depends_on = [aws_cloudwatch_log_group.extractor]
  tags       = var.tags
}

resource "aws_cloudwatch_event_rule" "schedule" {
  for_each            = toset(var.tables)
  name                = "${local.name}-extract-${each.key}"
  schedule_expression = var.schedule_expression
  is_enabled          = var.extractor_invocations_enabled
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "schedule" {
  for_each = toset(var.tables)
  rule     = aws_cloudwatch_event_rule.schedule[each.key].name
  arn      = aws_lambda_function.extractor.arn
  input    = jsonencode({ table = each.key })
}

resource "aws_lambda_permission" "events" {
  for_each      = toset(var.tables)
  statement_id  = "AllowEvents-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.extractor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule[each.key].arn
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${local.name}-extractor-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.extractor.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "dlq" {
  alarm_name          = "${local.name}-dlq-depth"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  tags                = var.tags
}
