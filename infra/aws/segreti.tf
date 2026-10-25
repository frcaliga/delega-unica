# Chiave per i token di accesso e per l'impronta degli OTP
data "aws_secretsmanager_random_password" "secret_key" {
  password_length     = 64
  exclude_punctuation = true
}

resource "aws_secretsmanager_secret" "secret_key" {
  name       = "${var.nome}/secret-key"
  kms_key_id = aws_kms_key.dati.arn
}

resource "aws_secretsmanager_secret_version" "secret_key" {
  secret_id     = aws_secretsmanager_secret.secret_key.id
  secret_string = data.aws_secretsmanager_random_password.secret_key.random_password

  lifecycle {
    # Generata una sola volta: cambiarla invaliderebbe sessioni e OTP in corso
    ignore_changes = [secret_string]
  }
}

# Certificato pubblico dell'autorita' FEA, prodotto da "python -m app.cli crea-ca-fea --kms-key-id"
# e caricato a mano dopo il primo apply (vedi infra/aws/README.md)
resource "aws_secretsmanager_secret" "ca_fea_certificato" {
  name       = "${var.nome}/ca-fea-certificato"
  kms_key_id = aws_kms_key.dati.arn
}
