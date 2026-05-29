variable "environment" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "alb_sg_id" {
  type = string
}

resource "aws_lb" "dashboard" {
  name               = "${var.environment}-sfft-dashboard"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.alb_sg_id]
  subnets            = var.public_subnet_ids

  tags = {
    Name        = "${var.environment}-signalfft-dashboard-alb"
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_lb_target_group" "dashboard" {
  name        = "${var.environment}-sfft-dashboard-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "/health"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = {
    Environment = var.environment
    Project     = "signalfft"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.dashboard.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.dashboard.arn
  }
}

output "alb_dns_name" {
  value = aws_lb.dashboard.dns_name
}

output "alb_arn" {
  value = aws_lb.dashboard.arn
}

output "target_group_arn" {
  value = aws_lb_target_group.dashboard.arn
}
