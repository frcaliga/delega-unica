# Deploy su AWS Milano (eu-south-1)

Infrastruttura in Terraform per il servizio in produzione. Tutti i dati restano nella regione di
Milano.

| Componente | Servizio AWS | Note |
| --- | --- | --- |
| Applicazione | ECS Fargate, 2 container | Le migrazioni del database girano all'avvio, una istanza alla volta |
| Database | RDS PostgreSQL 16 | Cifrato, solo connessioni TLS, backup giornalieri conservati 35 giorni, password gestita e ruotata da RDS |
| Chiave dell'autorita' FEA | KMS, RSA 4096 non esportabile | L'applicazione puo' solo firmare: la chiave privata non esce mai dal modulo hardware |
| OTP | SNS, SMS transazionali | |
| Segreti | Secrets Manager | Chiave dell'applicazione e certificato dell'autorita' |
| Accesso | Application Load Balancer | Solo HTTPS (TLS 1.2 e 1.3); HTTP viene reindirizzato |
| Log | CloudWatch | Conservati un anno, cifrati |

Costo indicativo con la configurazione predefinita: circa 90-110 euro al mese, da verificare con
il [calcolatore AWS](https://calculator.aws/). I costi principali sono database, container e
bilanciatore. Il database Multi-AZ (`db_multi_az = true`) raddoppia il costo del database.

I backup di RDS non bastano per la conservazione ventennale delle evidenze FEA (DPCM 22/2/2013,
art. 57). Per quella va affiancato un servizio di conservazione a norma.

## Prerequisiti

- Un account AWS con un utente amministratore e la [AWS CLI](https://aws.amazon.com/cli/)
  configurata (`aws configure`, regione `eu-south-1`)
- La regione di Milano attiva nell'account: e' una regione da abilitare, in *Account > Regioni AWS*
- Terraform 1.6 o successivo, Docker, uv
- Il plugin Session Manager della AWS CLI, per i comandi di amministrazione nel container
- Un dominio per il servizio, per esempio `deleghe.tuostudio.it`

## Primo deploy

### 1. Certificato HTTPS

In *Certificate Manager*, nella regione di Milano, richiedere un certificato pubblico per il
dominio e validarlo via DNS. Copiare l'ARN del certificato.

### 2. Infrastruttura

```bash
cd infra/aws
cp produzione.tfvars.example produzione.tfvars   # inserire l'ARN del certificato
terraform init
terraform apply -var-file=produzione.tfvars -var istanze=0
```

Con `istanze=0` si crea tutto senza avviare l'applicazione, che non ha ancora immagine e
certificato dell'autorita'.

### 3. Immagine dell'applicazione

Il modo consigliato e' farla costruire a GitHub, che la carica su ECR con credenziali temporanee
(OIDC): sul PC non serve Docker.

1. Dopo l'apply, copiare `terraform output -raw ruolo_github_immagine`.
2. Su GitHub, nel repository: *Settings > Secrets and variables > Actions > Variables >
   New repository variable*, nome `AWS_ROLE_ARN`, valore l'ARN del punto 1.
3. *Actions > Immagine > Run workflow* sul ramo `main`, indicando il tag (per esempio
   `2026-10-25-1`). In pochi minuti l'immagine e' su ECR.

In alternativa, da un PC con Docker:

```bash
REPO=$(terraform output -raw repository_immagine)
TAG=2026-10-25-1
aws ecr get-login-password | docker login --username AWS --password-stdin ${REPO%%/*}
docker build --platform linux/amd64 -t $REPO:$TAG ../..
docker push $REPO:$TAG
```

I tag sono immutabili: ogni rilascio ne usa uno nuovo.

### 4. Autorita' FEA

Il certificato dell'autorita' si firma con la chiave KMS una volta sola. Il certificato e'
pubblico; la chiave privata resta in KMS.

```bash
CHIAVE=$(terraform output -raw chiave_ca_fea)
cd ../..
uv run python -m app.cli crea-ca-fea --denominazione "Studio Rossi" --kms-key-id $CHIAVE > ca-fea.pem
aws secretsmanager put-secret-value \
    --secret-id delega-unica/ca-fea-certificato --secret-string file://ca-fea.pem
```

Per proteggere la chiave, la risorsa KMS ha `prevent_destroy`. Cancellarla renderebbe
inverificabili i certificati gia' emessi.

### 5. SMS

Un account AWS nuovo e' nella *sandbox SMS*: invia solo ai numeri verificati e ha un tetto di
spesa di 1 dollaro al mese. Per la produzione, in *AWS End User Messaging SMS*, la console con
cui si configurano gli SMS di SNS:

- chiedere l'uscita dalla sandbox e alzare il tetto di spesa mensile;
- verificare i requisiti per l'Italia del mittente alfanumerico (`sms_mittente`, al massimo 11
  caratteri), che puo' richiedere una registrazione.

### 6. Avvio

Impostare `immagine_tag` in `produzione.tfvars` con il tag del punto 3, poi:

```bash
cd infra/aws
terraform apply -var-file=produzione.tfvars
```

Infine creare nel DNS un record CNAME dal dominio verso `terraform output -raw
indirizzo_bilanciatore`.

### 7. Primo studio

```bash
TASK=$(aws ecs list-tasks --cluster delega-unica --query 'taskArns[0]' --output text)
aws ecs execute-command --cluster delega-unica --task $TASK --container api --interactive \
    --command "python -m app.cli create-tenant --name 'Studio Rossi' --codice-fiscale 01234567890 \
    --admin-email admin@studiorossi.it --admin-name 'Mario Rossi'"
```

La password viene chiesta in modo interattivo.

## Rilasci successivi

Costruire e caricare un'immagine con un nuovo tag, aggiornare `immagine_tag` e rieseguire
`terraform apply`. ECS sostituisce i container uno alla volta e torna alla versione precedente
se quella nuova non supera il controllo di salute.

## Stato di Terraform

Lo stato contiene informazioni sensibili. Prima dell'uso in produzione conviene salvarlo in un
bucket S3 cifrato con lock su DynamoDB: si attiva con il blocco `backend "s3"` in `versions.tf`.
