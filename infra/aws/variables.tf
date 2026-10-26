variable "regione" {
  description = "Regione AWS: Milano, cosi' i dati restano in Italia"
  type        = string
  default     = "eu-south-1"
}

variable "nome" {
  type    = string
  default = "delega-unica"
}

variable "ambiente" {
  type    = string
  default = "produzione"
}

variable "certificato_https_arn" {
  description = "Certificato ACM (nella stessa regione) per il dominio del servizio"
  type        = string
}

variable "immagine_tag" {
  description = "Tag dell'immagine Docker nel repository ECR"
  type        = string
  default     = "latest"
}

variable "istanze" {
  description = "Numero di container dell'applicazione"
  type        = number
  default     = 2
}

variable "db_classe" {
  type    = string
  default = "db.t4g.small"
}

variable "db_multi_az" {
  description = "Replica del database in una seconda zona (raddoppia il costo del database)"
  type        = bool
  default     = false
}

variable "sms_mittente" {
  description = "Mittente alfanumerico degli SMS (massimo 11 caratteri)"
  type        = string
  default     = "DelegaUnica"
}

variable "repository_github" {
  description = "Repository GitHub autorizzato a caricare le immagini (proprietario/nome)"
  type        = string
  default     = "frcaliga/delega-unica"
}
