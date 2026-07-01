#!/usr/bin/env bash
# Wire the enma-llm-request-failed CloudWatch alarm to an SNS SMS subscriber.
#
# Why this is a separate script: the deploying IAM user (Enmalabs) lacks
# sns:CreateTopic. Run this from a session with full SNS perms (the founder
# console session) once, the day P0.5 beta starts.
#
# Usage:
#     bash scripts/setup_llm_failure_alarm.sh +91XXXXXXXXXX
#
# Idempotent — re-running with the same number is a no-op except for one
# DuplicateSubscription error you can ignore.
set -euo pipefail

REGION="ap-south-1"
TOPIC_NAME="enma-llm-alarms"
ALARM_NAME="enma-llm-request-failed"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 +91XXXXXXXXXX" >&2
  exit 1
fi
PHONE="$1"

echo "[1/3] Ensure SNS topic ${TOPIC_NAME} exists..."
TOPIC_ARN=$(aws --region "${REGION}" sns create-topic --name "${TOPIC_NAME}" --query 'TopicArn' --output text)
echo "  ${TOPIC_ARN}"

echo "[2/3] Subscribe ${PHONE} (SMS)..."
aws --region "${REGION}" sns subscribe \
  --topic-arn "${TOPIC_ARN}" \
  --protocol sms \
  --notification-endpoint "${PHONE}" \
  --return-subscription-arn \
  --query 'SubscriptionArn' --output text

echo "[3/3] Point the alarm at the topic..."
aws --region "${REGION}" cloudwatch put-metric-alarm \
  --alarm-name "${ALARM_NAME}" \
  --alarm-description "P0: > 5 LLM request failures in 15 min" \
  --metric-name LlmRequestFailed \
  --namespace Enma/Backend \
  --statistic Sum \
  --period 900 \
  --evaluation-periods 1 \
  --threshold 5 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching \
  --alarm-actions "${TOPIC_ARN}"

echo "DONE — alarm now pages ${PHONE} on >5 llm_request_failed in 15 min."
