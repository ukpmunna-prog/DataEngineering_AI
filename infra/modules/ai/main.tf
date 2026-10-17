locals {
  name = "sn-change-intel-${var.env}"
  functions = {
    embedder = {
      handler = "src.embedder.handler.handler"
      input   = jsonencode({})
      cron    = var.embed_schedule
    }
    agent = {
      handler = "src.agent.handler.handler"
      input   = jsonencode({ score_upcoming = true })
      cron    = var.score_schedule
    }
  }
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

resource "aws_iam_role" "ai" {
  for_each           = local.functions
  name               = "${local.name}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_cloudwatch_log_group" "ai" {
  for_each          = local.functions
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = 30
  tags              = var.tags
}

data "aws_iam_policy_document" "ai" {
  for_each = local.functions

  statement {
    actions   = ["s3:ListBucket"]
    resources = [var.raw_bucket_arn]
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${var.raw_bucket_arn}/raw/*", "${var.raw_bucket_arn}/state/*"]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${var.raw_bucket_arn}/state/*", "${var.raw_bucket_arn}/assessments/*"]
  }
  statement {
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [var.kms_key_arn]
  }
  statement {
    actions = ["s3vectors:PutVectors", "s3vectors:QueryVectors", "s3vectors:GetVectors"]
    resources = [
      "arn:aws:s3vectors:*:*:bucket/${var.vector_bucket}",
      "arn:aws:s3vectors:*:*:bucket/${var.vector_bucket}/index/${var.vector_index}",
    ]
  }
  statement {
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:*::foundation-model/*",
      "arn:aws:bedrock:*:*:inference-profile/*",
    ]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ai[each.key].arn}:*"]
  }
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [var.dlq_arn]
  }
}

resource "aws_iam_role_policy" "ai" {
  for_each = local.functions
  role     = aws_iam_role.ai[each.key].id
  policy   = data.aws_iam_policy_document.ai[each.key].json
}

resource "aws_lambda_function" "ai" {
  for_each         = local.functions
  function_name    = "${local.name}-${each.key}"
  role             = aws_iam_role.ai[each.key].arn
  handler          = each.value.handler
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 1024
  filename         = var.lambda_package
  source_code_hash = filebase64sha256(var.lambda_package)

  environment {
    variables = {
      RAW_BUCKET       = var.raw_bucket
      VECTOR_BUCKET    = var.vector_bucket
      VECTOR_INDEX     = var.vector_index
      EMBED_MODEL_ID   = var.embed_model_id
      BEDROCK_MODEL_ID = var.bedrock_model_id
    }
  }

  dead_letter_config { target_arn = var.dlq_arn }
  depends_on = [aws_cloudwatch_log_group.ai]
  tags       = var.tags
}

resource "aws_cloudwatch_event_rule" "ai" {
  for_each            = local.functions
  name                = "${local.name}-${each.key}"
  schedule_expression = each.value.cron
  is_enabled          = true
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "ai" {
  for_each = local.functions
  rule     = aws_cloudwatch_event_rule.ai[each.key].name
  arn      = aws_lambda_function.ai[each.key].arn
  input    = each.value.input
}

resource "aws_lambda_permission" "ai" {
  for_each      = local.functions
  statement_id  = "AllowEvents-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ai[each.key].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ai[each.key].arn
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  for_each            = local.functions
  alarm_name          = "${local.name}-${each.key}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.ai[each.key].function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  tags                = var.tags
}
