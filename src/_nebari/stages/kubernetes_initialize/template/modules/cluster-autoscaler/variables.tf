variable "namespace" {
  description = "Namespace for helm chart resource"
  type        = string
}

variable "cluster-name" {
  description = "Cluster name for kubernetes cluster"
  type        = string
}

variable "overrides" {
  description = "Helm overrides to apply"
  type        = list(string)
  default     = []
}
