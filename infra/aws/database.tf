resource "aws_db_subnet_group" "database" {
  name       = var.nome
  subnet_ids = aws_subnet.privata[*].id
}

resource "aws_db_parameter_group" "database" {
  name   = "${var.nome}-pg16"
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
    # Come lo registra RDS: con "immediate" ogni plan proporrebbe una modifica inutile
    apply_method = "pending-reboot"
  }
}

resource "aws_db_instance" "database" {
  identifier     = var.nome
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_classe

  allocated_storage     = 20
  max_allocated_storage = 200
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.dati.arn

  db_name                       = "delega"
  username                      = "delega"
  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.dati.arn

  db_subnet_group_name   = aws_db_subnet_group.database.name
  parameter_group_name   = aws_db_parameter_group.database.name
  vpc_security_group_ids = [aws_security_group.database.id]
  multi_az               = var.db_multi_az
  publicly_accessible    = false

  backup_retention_period    = 35
  copy_tags_to_snapshot      = true
  deletion_protection        = true
  skip_final_snapshot        = false
  final_snapshot_identifier  = "${var.nome}-finale"
  auto_minor_version_upgrade = true

  lifecycle {
    prevent_destroy = true
  }
}
