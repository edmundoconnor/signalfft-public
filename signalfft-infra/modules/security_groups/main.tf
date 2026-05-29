variable "environment" {
  type = string
}

variable "vpc_id" {
  type = string
}

resource "aws_security_group" "alb" {
  name        = "${var.environment}-signalfft-alb-sg"
  description = "ALB security group for SignalFFT dashboard"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.environment}-signalfft-alb-sg"
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_security_group" "ecs_tasks" {
  name        = "${var.environment}-signalfft-ecs-tasks-sg"
  description = "ECS tasks security group for SignalFFT"
  vpc_id      = var.vpc_id

  ingress {
    description     = "From ALB"
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.environment}-signalfft-ecs-tasks-sg"
    Environment = var.environment
    Project     = "signalfft"
  }
}

output "alb_sg_id" {
  value = aws_security_group.alb.id
}

output "ecs_tasks_sg_id" {
  value = aws_security_group.ecs_tasks.id
}
