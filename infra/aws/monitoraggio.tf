# Allarmi via email: errori del servizio e container che non rispondono
resource "aws_sns_topic" "allarmi" {
  name = "${var.nome}-allarmi"
}

# AWS manda un'email con un link da confermare prima di inviare gli allarmi
resource "aws_sns_topic_subscription" "allarmi_email" {
  topic_arn = aws_sns_topic.allarmi.arn
  protocol  = "email"
  endpoint  = var.email_allarmi
}

resource "aws_cloudwatch_metric_alarm" "errori_500" {
  alarm_name          = "${var.nome}-errori-500"
  alarm_description   = "L'applicazione ha risposto con errori 500: controllare il log /ecs/${var.nome}"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.allarmi.arn]
  ok_actions          = [aws_sns_topic.allarmi.arn]

  dimensions = {
    LoadBalancer = aws_lb.principale.arn_suffix
    TargetGroup  = aws_lb_target_group.applicazione.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "container_non_disponibili" {
  alarm_name          = "${var.nome}-container-non-disponibili"
  alarm_description   = "Meno container funzionanti di quelli previsti"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 5
  threshold           = var.istanze
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.allarmi.arn]
  ok_actions          = [aws_sns_topic.allarmi.arn]

  dimensions = {
    LoadBalancer = aws_lb.principale.arn_suffix
    TargetGroup  = aws_lb_target_group.applicazione.arn_suffix
  }
}
