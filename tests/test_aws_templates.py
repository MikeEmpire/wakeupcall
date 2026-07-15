from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AWS_INFRA = ROOT / "infra" / "aws"


def _template(name):
    return (AWS_INFRA / name).read_text()


def test_foundation_uses_immutable_scanned_images_and_alarm_topic():
    template = _template("phase10-ecr.yaml")

    assert "ImageTagMutability: IMMUTABLE" in template
    assert "ScanOnPush: true" in template
    assert "DeletionPolicy: Retain" in template
    assert "AWS::SNS::Topic" in template
    assert "AWS::SNS::Subscription" in template


def test_application_services_are_private_and_disabled_by_default():
    template = _template("phase10-application.yaml")

    assert template.count("AssignPublicIp: DISABLED") == 2
    assert template.count("Default: 0") == 2
    assert "SourceSecurityGroupId: !Ref LoadBalancerSecurityGroup" in template
    assert "GroupDescription: Worker and migration tasks have no inbound access." in template
    assert "PubliclyAccessible: false" in template


def test_application_uses_distinct_commands_and_explicit_migration_task():
    template = _template("phase10-application.yaml")

    assert "run_delivery_worker" in template
    assert "MigrationTaskDefinition:" in template
    assert "Command: [python, manage.py, migrate, --noinput]" in template
    assert "EnableRealWorkerDelivery" in template


def test_task_secrets_use_secrets_manager_json_keys():
    template = _template("phase10-application.yaml")

    assert "AWS::SecretsManager::Secret" in template
    assert "ManageMasterUserPassword: true" in template
    assert "${ApplicationSecret}:DJANGO_SECRET_KEY::" in template
    assert "${SecretArn}:password::" in template
    assert "TWILIO_AUTH_TOKEN" in template


def test_phase8_scheduler_and_real_worker_remain_disabled_by_default():
    queue_template = _template("phase8-queue.yaml")
    application_template = _template("phase10-application.yaml")

    assert "Default: DISABLED" in queue_template
    assert 'Default: "false"' in application_template
    assert "AlarmTopicArn" in queue_template
