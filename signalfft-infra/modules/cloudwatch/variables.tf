variable "environment" {
  type = string
}

variable "ecs_cluster_name" {
  type    = string
  default = "prod-signalfft"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "alert_email" {
  type    = string
  default = ""
}
