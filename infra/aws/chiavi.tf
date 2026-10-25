# Chiave dell'autorita' FEA: RSA 4096 non esportabile. L'applicazione puo' solo firmare
# e leggere la chiave pubblica; nessuno puo' estrarre la privata.
resource "aws_kms_key" "ca_fea" {
  description              = "Autorita' interna dei certificati FEA monouso"
  customer_master_key_spec = "RSA_4096"
  key_usage                = "SIGN_VERIFY"
  deletion_window_in_days  = 30
  policy                   = data.aws_iam_policy_document.ca_fea.json

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "ca_fea" {
  name          = "alias/${var.nome}-ca-fea"
  target_key_id = aws_kms_key.ca_fea.key_id
}

data "aws_iam_policy_document" "ca_fea" {
  statement {
    sid       = "Amministrazione"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.corrente.account_id}:root"]
    }
  }

  statement {
    sid       = "FirmaApplicazione"
    actions   = ["kms:Sign", "kms:GetPublicKey", "kms:DescribeKey"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.task.arn]
    }
  }
}

# Cifratura di database, segreti e log
resource "aws_kms_key" "dati" {
  description             = "Cifratura dei dati di ${var.nome}"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.dati.json
}

resource "aws_kms_alias" "dati" {
  name          = "alias/${var.nome}-dati"
  target_key_id = aws_kms_key.dati.key_id
}

data "aws_iam_policy_document" "dati" {
  statement {
    sid       = "Amministrazione"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.corrente.account_id}:root"]
    }
  }

  statement {
    sid       = "LogCloudWatch"
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.regione}.amazonaws.com"]
    }
  }
}
