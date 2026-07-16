# AWS Deployment Runbook

## Purpose and stopping boundary

Phase 10 supplied the CloudFormation and operator sequence used for the live staging deployment in `us-east-1`. The `wakeup-call-staging-foundation`, `wakeup-call-staging-queue`, and `wakeup-call-staging-application` stacks are deployed. Cloudflare provides DNS for `wakeupcall.afam.app`, the ACM certificate is issued, and the public TLS health endpoint is live. The templates create billable ECR, SNS, SQS, EventBridge Scheduler, NAT Gateway, ALB, ECS/Fargate, RDS, Secrets Manager, and CloudWatch resources; Route 53 remains optional.

The currently deployed image is the immutable `b1d7d4fb93a689e17e3f2ce2e0518b80c364c375` tag. Later application phases must be built, migrated, and deployed before their behavior can be claimed in staging.

The application behavior is unchanged. One image runs as three explicit ECS task definitions:

- web: Gunicorn behind an HTTPS Application Load Balancer
- worker: `run_delivery_worker`, with real delivery disabled by default
- migration: one-shot `migrate --noinput`

The Phase 8 queue stack remains separate so queue lifecycle and application rollout can be controlled independently. Its queue URL and ARN are inputs to the Phase 10 application stack.

## Decisions and tradeoffs

- The ALB spans two public subnets. Web, worker, migration, and RDS resources use private subnets and receive no public IPs.
- Only the ALB security group can reach web port 8000. Worker and migration tasks have no inbound rule. RDS accepts PostgreSQL only from web and worker/migration security groups.
- One NAT Gateway provides outbound access for ECR image pulls, CloudWatch Logs, Secrets Manager, SQS, WeatherAPI.com, and Twilio. This lowers staging cost but is a single-AZ outbound dependency. A production availability review should add one NAT Gateway and route table per application AZ or replace AWS-service paths with VPC endpoints.
- RDS PostgreSQL 17 uses encrypted `gp3` storage, seven-day backups, private networking, and an RDS-managed master password in Secrets Manager. Multi-AZ and deletion protection are parameters and default off for a removable staging environment.
- The application secret is created with a generated Django key and empty provider fields. Provider values must be filled in through Secrets Manager before tasks start. Credentials never belong in CloudFormation parameters, shell history, task-definition plaintext, or committed parameter files.
- ECR tags are immutable, images scan on push, and only the twenty newest images are retained. Deploy a unique source-revision tag or an image digest, never `latest`.
- CloudWatch log groups retain data for 30 days. Application, RDS, queue-age, DLQ, and unhealthy-target alarms send to the shared SNS topic when its ARN is passed to both stacks. An email subscription is optional and must be confirmed.
- Services initially have desired count zero and the Scheduler is initially disabled. The migration task must succeed before web/worker capacity or the Scheduler is enabled.

## Required operator choices

Before deployment, choose and record:

- AWS account and region
- a unique environment name such as `staging`
- application DNS name and a validated ACM certificate in the same region as the ALB
- optional Route 53 hosted-zone ID
- alarm email or another confirmed SNS subscription
- whether the staging RDS instance should use Multi-AZ and deletion protection
- whether real queue delivery is authorized; leave it false for the initial deployment

Check current regional availability and pricing before creating resources. The NAT Gateway, ALB, RDS, and continuously running Fargate tasks are the main always-on costs.

## 1. Validate templates without creating resources

Run local project validation first, then ask CloudFormation to validate each template:

```bash
aws cloudformation validate-template --template-body file://infra/aws/phase10-ecr.yaml
aws cloudformation validate-template --template-body file://infra/aws/phase8-queue.yaml
aws cloudformation validate-template --template-body file://infra/aws/phase10-application.yaml
```

`validate-template` is read-only but requires working AWS credentials and a selected region. It does not prove that account quotas, certificate ownership, DNS, or regional instance types are valid.

## 2. Create the image repository and alarm topic

```bash
aws cloudformation deploy \
  --template-file infra/aws/phase10-ecr.yaml \
  --stack-name wakeup-call-staging-foundation \
  --parameter-overrides \
    EnvironmentName=staging \
    AlarmNotificationEmail=YOUR_OPERATOR_EMAIL
```

Confirm the SNS subscription email before relying on alarms. Read `RepositoryUri` and `AlarmTopicArn` from the stack outputs.

## 3. Build and publish one immutable image

Authenticate Docker to the repository, use a unique source revision as the image tag, and build for the task definition's `X86_64` runtime:

```bash
aws ecr get-login-password | docker login --username AWS --password-stdin AWS_ACCOUNT_ID.dkr.ecr.AWS_REGION.amazonaws.com
docker build --platform linux/amd64 --build-arg REQUIREMENTS_FILE=requirements/production.txt --tag wakeup-call:SOURCE_REVISION .
docker tag wakeup-call:SOURCE_REVISION REPOSITORY_URI:SOURCE_REVISION
docker push REPOSITORY_URI:SOURCE_REVISION
```

Record the exact `REPOSITORY_URI:SOURCE_REVISION` as `ImageUri`. Do not overwrite or reuse a deployed tag.

## 4. Deploy the queue with sending disabled

```bash
aws cloudformation deploy \
  --template-file infra/aws/phase8-queue.yaml \
  --stack-name wakeup-call-staging-queue \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    EnvironmentName=staging \
    ScheduleState=DISABLED \
    AlarmTopicArn=ALARM_TOPIC_ARN
```

Record `DeliveryQueueUrl`, `DeliveryQueueArn`, and `DispatcherScheduleName` from the outputs. The queue is at-least-once and has a three-receive DLQ redrive policy aligned with the worker.

## 5. Deploy application infrastructure at zero capacity

```bash
aws cloudformation deploy \
  --template-file infra/aws/phase10-application.yaml \
  --stack-name wakeup-call-staging-application \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    EnvironmentName=staging \
    ImageUri=REPOSITORY_URI:SOURCE_REVISION \
    ApplicationDomain=wakeup.example.com \
    CertificateArn=ACM_CERTIFICATE_ARN \
    HostedZoneId=ROUTE53_ZONE_ID_OR_EMPTY_STRING \
    DeliveryQueueUrl=DELIVERY_QUEUE_URL \
    DeliveryQueueArn=DELIVERY_QUEUE_ARN \
    AlarmTopicArn=ALARM_TOPIC_ARN \
    WebDesiredCount=0 \
    WorkerDesiredCount=0 \
    EnableRealWorkerDelivery=false
```

Keep a private, uncommitted deployment parameter record so later stack updates retain the same values. CloudFormation parameters are not a place for credentials.

## 6. Configure secrets

Use the `ApplicationSecretArn` output. In the Secrets Manager console, edit its JSON while preserving the generated `DJANGO_SECRET_KEY`. Fill these keys:

```json
{
  "DJANGO_SECRET_KEY": "generated-value-already-present",
  "WEATHER_API_KEY": "provider-value",
  "TWILIO_ACCOUNT_SID": "provider-value",
  "TWILIO_AUTH_TOKEN": "provider-value",
  "TWILIO_VERIFY_SERVICE_SID": "provider-value",
  "TWILIO_SMS_FROM_NUMBER": "provider-value",
  "TWILIO_VOICE_FROM_NUMBER": "provider-value"
}
```

Do not paste the actual JSON into chat, tickets, source control, command arguments, or logs. If secrets are updated after services start, force a new ECS deployment because running tasks do not receive secret changes automatically.

## 7. Configure Twilio callback values

The callback URLs are application configuration, not credentials supplied by Twilio. Construct them from the exact public application origin and committed endpoint paths:

```text
TWILIO_VOICE_STATUS_CALLBACK_URL=https://wakeupcall.afam.app/twilio/voice/status/
TWILIO_VOICE_ACTION_CALLBACK_URL=https://wakeupcall.afam.app/twilio/voice/action/
TWILIO_SMS_INBOUND_CALLBACK_URL=https://wakeupcall.afam.app/twilio/sms/inbound/
```

Keep the scheme, host, path, and trailing slash exact. Twilio includes the complete URL when signing a webhook, so a configured URL that differs from the URL used by Django will fail signature validation.

`TWILIO_SMS_FROM_NUMBER` is the SMS-capable Twilio number assigned to the account, in E.164 format. Find it in Twilio Console under **Phone Numbers → Manage → Active Numbers**. Copy the number itself, not its `PN...` resource SID. For this deployment it remains an application-secret JSON value rather than a CloudFormation parameter:

```text
TWILIO_SMS_FROM_NUMBER=+1XXXXXXXXXX
```

In the selected active number's Messaging configuration, set **A message comes in** to a webhook using HTTP `POST` and `https://wakeupcall.afam.app/twilio/sms/inbound/`. The Voice action URL is not configured on the phone-number page: the worker embeds it in the outbound call's `<Gather>` TwiML. The Voice status URL is supplied when the application creates the outbound call.

The web task requires `TWILIO_VOICE_ACTION_CALLBACK_URL`, `TWILIO_SMS_INBOUND_CALLBACK_URL`, and `TWILIO_SMS_FROM_NUMBER`. The worker requires `TWILIO_VOICE_ACTION_CALLBACK_URL` so it can render the menu TwiML. The Phase 10 template injects both callback URLs into the web task, injects the Voice action URL into the worker task, and reads the web task's SMS sender number from the existing application secret. These updated task definitions and the latest Phase 14/15 image are not live until an operator performs the rollout.

## 8. Run and verify the migration task

Read `ClusterArn`, `MigrationTaskDefinitionArn`, `ApplicationSubnetIds`, and `WorkerSecurityGroupId` from the application stack outputs. Run exactly one migration task in the private application subnets:

```bash
aws ecs run-task \
  --cluster CLUSTER_ARN \
  --launch-type FARGATE \
  --platform-version 1.4.0 \
  --task-definition MIGRATION_TASK_DEFINITION_ARN \
  --network-configuration 'awsvpcConfiguration={subnets=[SUBNET_ONE,SUBNET_TWO],securityGroups=[WORKER_SECURITY_GROUP_ID],assignPublicIp=DISABLED}'
```

Wait for the task to stop, then inspect only its exit code and the migration log group. Do not start services unless the container exit code is zero:

```bash
aws ecs wait tasks-stopped --cluster CLUSTER_ARN --tasks MIGRATION_TASK_ARN
aws ecs describe-tasks --cluster CLUSTER_ARN --tasks MIGRATION_TASK_ARN --query 'tasks[0].containers[0].{exitCode:exitCode,reason:reason}'
```

## 9. Start services safely

Update the application stack using the same parameters, changing `WebDesiredCount=1` and `WorkerDesiredCount=1`. Keep `EnableRealWorkerDelivery=false` initially. Confirm:

- the web ECS service reaches steady state
- the ALB target is healthy at `/health/`
- `https://APPLICATION_DOMAIN/health/` returns `{"status":"ok"}`
- the worker long-polls without credential, database, or queue errors
- no sensitive values, phone numbers, message bodies, or raw provider responses appear in logs
- a deliberately created due demo event reaches `suppressed`, never Twilio
- SNS alarm delivery has been confirmed

Only after those checks should the queue stack be updated to `ScheduleState=ENABLED`. A Scheduler tick contains no phone number or event body.

## 10. Real-delivery gate

Real queue delivery requires both `EnableRealWorkerDelivery=true` in the application stack and the worker command's `--allow-real-delivery`; the template changes them together. Leave the parameter false until destinations, provider compliance, cost, and operator authorization have been reviewed. Demo events still select `DemoMessageSender` in application orchestration and cannot reach Twilio.

Changing this gate requires a new worker task definition and ECS deployment. It does not change API-created events, which remain demo-only.

## Rollback and teardown

- Roll back application code by redeploying a previously published immutable `ImageUri`, running its compatible migrations if required, and waiting for ECS steady state.
- Disable the Scheduler before stopping workers or investigating queue failures.
- To scale application compute to zero without deleting data, update the queue stack to `ScheduleState=DISABLED`, then update the application stack to `WebDesiredCount=0` and `WorkerDesiredCount=0`. This stops Fargate task charges but does not stop charges for the NAT Gateway, ALB, RDS, or retained storage.
- Inspect DLQ messages without logging message bodies or receipt handles. Automatic provider replay remains prohibited where Twilio acceptance is ambiguous.
- Deleting the application stack snapshots RDS. The application secret and ECR repository are retained deliberately and require separate, explicit cleanup. Deletion protection must be disabled before deleting an RDS instance that has it enabled.

No deployment step changes the meaning of `submitted`: it records provider acceptance, not final handset delivery or proof that a person heard a call.
