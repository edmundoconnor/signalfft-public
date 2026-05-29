variable "environment" {
  type = string
}

variable "lifecycle_ia_days" {
  type    = number
  default = 90
}

variable "lifecycle_expire_days" {
  type    = number
  default = 365
}

resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.environment}-signalfft-artifacts"

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "archive-and-expire"
    status = "Enabled"

    filter {
      prefix = ""
    }

    transition {
      days          = var.lifecycle_ia_days
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = var.lifecycle_expire_days
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
