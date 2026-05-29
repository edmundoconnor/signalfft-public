variable "environment" {
  type = string
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "nat_gateway_count" {
  description = "Number of NAT gateways (0 = no NAT, use public subnets for ECS)"
  type        = number
  default     = 0
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name        = "${var.environment}-signalfft-vpc"
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet("10.0.0.0/16", 8, count.index + 1)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name        = "${var.environment}-signalfft-public-${count.index}"
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet("10.0.0.0/16", 8, count.index + 10)
  availability_zone = local.azs[count.index]

  tags = {
    Name        = "${var.environment}-signalfft-private-${count.index}"
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name        = "${var.environment}-signalfft-igw"
    Environment = var.environment
    Project     = "signalfft"
  }
}

# NAT Gateway — only created if nat_gateway_count > 0
resource "aws_eip" "nat" {
  count  = var.nat_gateway_count
  domain = "vpc"

  tags = {
    Name        = "${var.environment}-signalfft-nat-eip-${count.index}"
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_nat_gateway" "main" {
  count         = var.nat_gateway_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index % 2].id

  tags = {
    Name        = "${var.environment}-signalfft-nat-${count.index}"
    Environment = var.environment
    Project     = "signalfft"
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name        = "${var.environment}-signalfft-public-rt"
    Environment = var.environment
    Project     = "signalfft"
  }
}

# Private route tables — only created if NAT gateways exist
resource "aws_route_table" "private" {
  count  = var.nat_gateway_count > 0 ? 2 : 0
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index % var.nat_gateway_count].id
  }

  tags = {
    Name        = "${var.environment}-signalfft-private-rt-${count.index}"
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = var.nat_gateway_count > 0 ? 2 : 0
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ---------------------------------------------------------------------------
# VPC Gateway Endpoints (free — saves NAT Gateway costs)
# ---------------------------------------------------------------------------

# S3 Gateway Endpoint (free)
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]

  tags = {
    Name        = "${var.environment}-signalfft-s3-endpoint"
    Environment = var.environment
    Project     = "signalfft"
  }
}

# DynamoDB Gateway Endpoint (free)
resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]

  tags = {
    Name        = "${var.environment}-signalfft-dynamodb-endpoint"
    Environment = var.environment
    Project     = "signalfft"
  }
}
