# GitHub Actions costruisce l'immagine e la carica su ECR senza chiavi di accesso salvate:
# il workflow ottiene credenziali temporanee tramite OIDC, solo dal ramo main di questo repository.
# Dal 15 luglio 2026 i repository nuovi presentano il soggetto con gli identificativi numerici
# (repo:proprietario@id/nome@id), che non cambiano se il nome viene riusato da altri.

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

data "aws_iam_policy_document" "assume_github" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for soggetto in var.soggetti_github : "${soggetto}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_immagine" {
  name               = "${var.nome}-github-immagine"
  assume_role_policy = data.aws_iam_policy_document.assume_github.json
}

data "aws_iam_policy_document" "github_immagine" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.applicazione.arn]
  }
}

resource "aws_iam_role_policy" "github_immagine" {
  name   = "push-immagine"
  role   = aws_iam_role.github_immagine.id
  policy = data.aws_iam_policy_document.github_immagine.json
}
