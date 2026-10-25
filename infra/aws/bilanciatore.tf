resource "aws_lb" "principale" {
  name                       = var.nome
  load_balancer_type         = "application"
  subnets                    = aws_subnet.pubblica[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "applicazione" {
  name        = var.nome
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.principale.id

  health_check {
    path    = "/health"
    matcher = "200"
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.principale.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificato_https_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.applicazione.arn
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.principale.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}
