# Due zone: sottoreti pubbliche per bilanciatore e container, private per il database.
# I container stanno nelle sottoreti pubbliche con IP pubblico per raggiungere KMS, SNS ed ECR
# senza NAT gateway; il gruppo di sicurezza accetta traffico solo dal bilanciatore.

locals {
  zone = slice(data.aws_availability_zones.disponibili.names, 0, 2)
}

resource "aws_vpc" "principale" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = var.nome }
}

resource "aws_internet_gateway" "principale" {
  vpc_id = aws_vpc.principale.id
}

resource "aws_subnet" "pubblica" {
  count             = 2
  vpc_id            = aws_vpc.principale.id
  cidr_block        = cidrsubnet(aws_vpc.principale.cidr_block, 8, count.index)
  availability_zone = local.zone[count.index]
  tags              = { Name = "${var.nome}-pubblica-${count.index}" }
}

resource "aws_subnet" "privata" {
  count             = 2
  vpc_id            = aws_vpc.principale.id
  cidr_block        = cidrsubnet(aws_vpc.principale.cidr_block, 8, count.index + 10)
  availability_zone = local.zone[count.index]
  tags              = { Name = "${var.nome}-privata-${count.index}" }
}

resource "aws_route_table" "pubblica" {
  vpc_id = aws_vpc.principale.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.principale.id
  }
}

resource "aws_route_table_association" "pubblica" {
  count          = 2
  subnet_id      = aws_subnet.pubblica[count.index].id
  route_table_id = aws_route_table.pubblica.id
}

resource "aws_security_group" "alb" {
  name   = "${var.nome}-alb"
  vpc_id = aws_vpc.principale.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "applicazione" {
  name   = "${var.nome}-applicazione"
  vpc_id = aws_vpc.principale.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "database" {
  name   = "${var.nome}-database"
  vpc_id = aws_vpc.principale.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.applicazione.id]
  }
}
