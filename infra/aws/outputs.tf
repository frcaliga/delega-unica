output "indirizzo_bilanciatore" {
  description = "Nome DNS da puntare con un record CNAME dal dominio del servizio"
  value       = aws_lb.principale.dns_name
}

output "repository_immagine" {
  value = aws_ecr_repository.applicazione.repository_url
}

output "chiave_ca_fea" {
  value = aws_kms_key.ca_fea.arn
}

output "segreto_certificato_ca_fea" {
  value = aws_secretsmanager_secret.ca_fea_certificato.name
}

output "ruolo_github_immagine" {
  description = "Da impostare nella variabile AWS_ROLE_ARN delle GitHub Actions"
  value       = aws_iam_role.github_immagine.arn
}
