terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  # Stato remoto consigliato: decommentare dopo aver creato bucket e tabella di lock.
  # backend "s3" {
  #   bucket         = "delega-unica-terraform-state"
  #   key            = "produzione/terraform.tfstate"
  #   region         = "eu-south-1"
  #   dynamodb_table = "delega-unica-terraform-lock"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.regione

  default_tags {
    tags = {
      Progetto = var.nome
      Ambiente = var.ambiente
    }
  }
}

data "aws_caller_identity" "corrente" {}
data "aws_availability_zones" "disponibili" {
  state = "available"
}
