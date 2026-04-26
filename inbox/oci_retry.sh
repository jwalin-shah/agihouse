#!/bin/bash
# Retry loop for creating A1.Flex instance in us-sanjose-1
# Logs every attempt to oci_retry.log. Stops on success.

set -u

LOG=/Users/jwalinshah/projects/inbox/oci_retry.log
COMPARTMENT="ocid1.tenancy.oc1..aaaaaaaaycgvod5nrintyc37bw3rdert4yjrcfp4wsitbtuubvx5eecwr4ka"
AD="AmHG:US-SANJOSE-1-AD-1"
SHAPE="VM.Standard.A1.Flex"
IMAGE="ocid1.image.oc1.us-sanjose-1.aaaaaaaa3bhtihetcgdkvl2srbvm23l5guf5wlmtq3toyht2l6kcuxqa2adq"
SUBNET="ocid1.subnet.oc1.us-sanjose-1.aaaaaaaaf5ybkuzbnzf764b7xrpxiqqbie3yjc3nrwk6bxl3errb5i3rlaca"
SSH_KEY=/Users/jwalinshah/.ssh/id_ed25519.pub
NAME="devtime"

attempt=0
while true; do
  attempt=$((attempt+1))
  ts=$(date '+%Y-%m-%d %H:%M:%S')
  echo "[$ts] attempt #$attempt" >> "$LOG"

  out=$(oci compute instance launch \
    --compartment-id "$COMPARTMENT" \
    --availability-domain "$AD" \
    --shape "$SHAPE" \
    --shape-config '{"ocpus": 4, "memoryInGBs": 24}' \
    --image-id "$IMAGE" \
    --subnet-id "$SUBNET" \
    --assign-public-ip true \
    --display-name "$NAME" \
    --boot-volume-size-in-gbs 200 \
    --ssh-authorized-keys-file "$SSH_KEY" \
    --wait-for-state RUNNING \
    --wait-interval-seconds 15 \
    2>&1)
  rc=$?

  if [ $rc -eq 0 ]; then
    echo "[$ts] SUCCESS on attempt #$attempt" >> "$LOG"
    echo "$out" >> "$LOG"
    exit 0
  fi

  if echo "$out" | grep -qi "Out of host capacity\|Out of capacity"; then
    echo "[$ts] out of capacity — sleeping 90s" >> "$LOG"
  else
    echo "[$ts] other error (rc=$rc):" >> "$LOG"
    echo "$out" >> "$LOG"
    echo "[$ts] sleeping 90s anyway" >> "$LOG"
  fi
  sleep 90
done
