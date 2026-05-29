variable "environment" {
  type = string
}

resource "aws_s3_bucket" "dashboard_ui" {
  bucket = "${var.environment}-signalfft-dashboard-ui"

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_s3_bucket_public_access_block" "dashboard_ui" {
  bucket = aws_s3_bucket.dashboard_ui.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_website_configuration" "dashboard_ui" {
  bucket = aws_s3_bucket.dashboard_ui.id

  index_document {
    suffix = "index.html"
  }

  error_document {
    key = "index.html"
  }
}

resource "aws_s3_bucket_policy" "dashboard_ui" {
  bucket = aws_s3_bucket.dashboard_ui.id

  depends_on = [aws_s3_bucket_public_access_block.dashboard_ui]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "PublicReadGetObject"
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.dashboard_ui.arn}/*"
    }]
  })
}

output "bucket_name" {
  value = aws_s3_bucket.dashboard_ui.bucket
}

output "website_endpoint" {
  value = aws_s3_bucket_website_configuration.dashboard_ui.website_endpoint
}
