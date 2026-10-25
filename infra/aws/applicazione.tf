resource "aws_ecr_repository" "applicazione" {
  name                 = var.nome
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.dati.arn
  }
}

resource "aws_cloudwatch_log_group" "applicazione" {
  name              = "/ecs/${var.nome}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.dati.arn
}

resource "aws_ecs_cluster" "principale" {
  name = var.nome

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

data "aws_iam_policy_document" "assume_ecs" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Ruolo usato da ECS per avviare il container: immagine, log e segreti
resource "aws_iam_role" "esecuzione" {
  name               = "${var.nome}-esecuzione"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs.json
}

resource "aws_iam_role_policy_attachment" "esecuzione" {
  role       = aws_iam_role.esecuzione.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "esecuzione_segreti" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_db_instance.database.master_user_secret[0].secret_arn,
      aws_secretsmanager_secret.secret_key.arn,
      aws_secretsmanager_secret.ca_fea_certificato.arn,
    ]
  }

  statement {
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.dati.arn]
  }
}

resource "aws_iam_role_policy" "esecuzione_segreti" {
  name   = "segreti"
  role   = aws_iam_role.esecuzione.id
  policy = data.aws_iam_policy_document.esecuzione_segreti.json
}

# Ruolo dell'applicazione: firmare con la chiave dell'autorita' e inviare SMS
resource "aws_iam_role" "task" {
  name               = "${var.nome}-applicazione"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs.json
}

data "aws_iam_policy_document" "task" {
  statement {
    actions   = ["kms:Sign", "kms:GetPublicKey"]
    resources = [aws_kms_key.ca_fea.arn]
  }

  statement {
    # L'invio diretto a un numero di telefono non ha una risorsa SNS a cui limitarlo
    actions   = ["sns:Publish"]
    resources = ["*"]
  }

  statement {
    # Comandi di amministrazione nel container con "aws ecs execute-command"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "firma-e-sms"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

locals {
  segreto_db = aws_db_instance.database.master_user_secret[0].secret_arn
}

resource "aws_ecs_task_definition" "applicazione" {
  family                   = var.nome
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.esecuzione.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name         = "api"
      image        = "${aws_ecr_repository.applicazione.repository_url}:${var.immagine_tag}"
      essential    = true
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      environment = [
        { name = "DB_HOST", value = aws_db_instance.database.address },
        { name = "DB_NAME", value = aws_db_instance.database.db_name },
        { name = "DB_USER", value = aws_db_instance.database.username },
        { name = "REQUIRE_MFA", value = "true" },
        { name = "AWS_REGION", value = var.regione },
        { name = "FEA_CA_KMS_KEY_ID", value = aws_kms_key.ca_fea.arn },
        { name = "SMS_PROVIDER", value = "sns" },
        { name = "SMS_MITTENTE", value = var.sms_mittente },
      ]
      secrets = [
        { name = "DB_PASSWORD", valueFrom = "${local.segreto_db}:password::" },
        { name = "SECRET_KEY", valueFrom = aws_secretsmanager_secret.secret_key.arn },
        { name = "FEA_CA_CERT_PEM", valueFrom = aws_secretsmanager_secret.ca_fea_certificato.arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.applicazione.name
          awslogs-region        = var.regione
          awslogs-stream-prefix = "api"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "applicazione" {
  name                               = var.nome
  cluster                            = aws_ecs_cluster.principale.id
  task_definition                    = aws_ecs_task_definition.applicazione.arn
  desired_count                      = var.istanze
  launch_type                        = "FARGATE"
  enable_execute_command             = true
  health_check_grace_period_seconds  = 120
  deployment_minimum_healthy_percent = 50

  network_configuration {
    subnets          = aws_subnet.pubblica[*].id
    security_groups  = [aws_security_group.applicazione.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.applicazione.arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.https]
}
