variable "environment" {
  description = "Deployment environment (dev, sim, prod)"
  type        = string
}

variable "billing_mode" {
  description = "DynamoDB billing mode"
  type        = string
  default     = "PAY_PER_REQUEST"
}

variable "read_capacity" {
  description = "Read capacity units (only used if PROVISIONED)"
  type        = number
  default     = 5
}

variable "write_capacity" {
  description = "Write capacity units (only used if PROVISIONED)"
  type        = number
  default     = 5
}

variable "tags" {
  description = "Resource tags"
  type        = map(string)
  default = {
    project = "signalfft"
  }
}

variable "ttl_enabled" {
  description = "Enable TTL on waves table"
  type        = bool
  default     = true
}
