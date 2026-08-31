# Phase 11.2 AWS deployment foundation

This directory is review-only infrastructure preparation. Nothing here has
been applied. The stack contains no application release, Zerodha credential,
session, market-data connection, order capability, SSH key, or inbound rule.

## Decision summary

- Region: `ap-south-1` (Mumbai).
- OS: latest AWS-owned Amazon Linux 2023 x86_64 AMI, resolved through the AWS
  public SSM parameter at stack creation.
- Compute: `t3.small` On-Demand (2 vCPU, 2 GiB). `t3.micro` is the smaller
  1 GiB fallback; `t3.medium` is the 4 GiB contingency.
- Root: 20 GiB encrypted gp3, 3,000 IOPS, 125 MiB/s, deleted with the instance.
- State: 8 GiB encrypted gp3, 3,000 IOPS, 125 MiB/s, Multi-Attach disabled,
  retained on stack deletion, intended for `/var/lib/algotrader`.
- Administration: Systems Manager Session Manager only. No key pair or port 22.
- Static identity: one retained Elastic IP. Do not register it with Zerodha in
  Phase 11.2.
- Backups: daily DLM snapshots at 20:00 UTC, retaining 14 snapshots. These are
  crash-consistent infrastructure backups; application-consistent procedures
  are deferred until the service exists.

Mumbai is preferred over Hyderabad (`ap-south-2`) because it is the older,
default-enabled Indian region and offers the required foundation services.
Hyderabad is the Indian fallback, subject to explicit account enablement and a
service/pricing check. Singapore (`ap-southeast-1`) is the nearby mature fallback
only if Indian regions present an operational problem. No latency figure is
claimed; Zerodha REST and WebSocket latency must be measured in Phase 11.12.

## Template safety properties

`foundation.yaml` creates exactly one host and has no Auto Scaling group. Its
security group has no ingress rules. Outbound TCP 443 is intentionally broad in
the foundation because Zerodha, SSM, Secrets Manager, CloudWatch, operating-system
repositories, and future artifact sources use TLS endpoints whose IP ranges can
change. DNS TCP/UDP 53 is also allowed. Amazon Time Sync uses the link-local EC2
time endpoint and is not opened to the public internet.

The broad 443 rule can later be tightened with VPC endpoints for AWS services
and an egress proxy/firewall, but neither is justified for the minimum one-bot
foundation. Domain names cannot be expressed directly in a security-group rule.

The instance role is separate from deployment/operator authority. It contains:

- `AmazonSSMManagedInstanceCore` for managed-instance connectivity;
- read-only access to the exact secret created by this stack;
- write access to only the two stack-owned log groups;
- `PutMetricData` limited to the `Algotrader/Host` namespace.

It has no IAM mutation, EC2 mutation, S3, wildcard Secrets Manager, or broker
permission. The DLM service role is separate and trusts only `dlm.amazonaws.com`.

The secret initially contains only `{"configured":false}`. It is an empty
boundary, not a usable credential. Real values must be introduced later through
the approved manual secrets procedure and never through CloudFormation source or
parameters.

## Operator and deployment authority

Do not attach deployment permissions to the runtime role. Use a human/federated
deployment role protected by MFA and scoped to this stack. It will eventually
need CloudFormation stack operations plus permissions to create the resource
types in the template and `iam:PassRole` restricted to this stack's EC2 and DLM
roles. A read-only operator role should inspect EC2, CloudFormation, CloudWatch,
EBS, and Systems Manager, and start Session Manager sessions, but should not
mutate IAM, secrets, the state volume, or execution configuration.

Before a future apply, review the CloudFormation change set, verify the account
and `ap-south-1` region, replace the example owner, and confirm that the change
set contains one EC2 instance and no unexpected replacement of retained
resources. Never pass credentials as parameters.

## Session Manager operations

Amazon Linux 2023 normally includes SSM Agent. Phase 11.4 must verify the agent
is installed, enabled, current, and registered before treating the host as
manageable. The public subnet, Elastic IP, route, outbound 443, and instance role
allow the agent to reach public SSM endpoints without a NAT gateway. No EC2 key
pair is configured.

Future operator workflow uses Session Manager to open a shell, inspect systemd
and read explicitly approved diagnostics, and run the manual authentication
command. Operators must not print the session file, secret values, position
state, or audit contents casually.

## State ownership and duplicate prevention

The independent state volume is retained and tagged
`Component=authoritative-state`, `ExecutionOwner=NONE_EXECUTION_DISABLED`, and
`MountPoint=/var/lib/algotrader`. The template attaches it to one instance and
explicitly disables Multi-Attach. Phase 11.4 will format and mount it; Phase 11.5
will make the service fail if the mount is absent and add the process-lifetime
application lock. Infrastructure preparation alone is not sufficient fencing.

## Cost planning

Approximate Mumbai monthly baseline at 730 hours, before tax and support:

| Item | Planning estimate (USD/month) |
| --- | ---: |
| `t3.small` Linux On-Demand | about 16.35 |
| 20 + 8 GiB gp3 | about 2.55 |
| One public IPv4 / Elastic IP | 3.65 |
| One Secrets Manager secret | 0.40 |
| CloudWatch alarms and low-volume logs | 1.00–5.00 |
| Initial/incremental EBS snapshots | 0.50–2.00 |
| **Planning total** | **about 24.45–29.95** |

Usage-dependent charges include outbound data transfer, CloudWatch ingestion and
retention, snapshot changed blocks, Secrets Manager API calls, and T3 unlimited
surplus CPU credits. Confirm every rate in AWS Pricing Calculator immediately
before apply; this estimate is not a quote.

## Review and future deployment commands

No command below has been run. `validate-template` is read-only, while
`create-change-set` and `deploy` alter AWS state and require separate approval.

```sh
aws cloudformation validate-template \
  --region ap-south-1 \
  --template-body file://deploy/aws/foundation.yaml

aws cloudformation deploy \
  --region ap-south-1 \
  --stack-name algotrader-production-foundation \
  --template-file deploy/aws/foundation.yaml \
  --parameter-overrides Owner=REPLACE_BEFORE_APPLY \
  --capabilities CAPABILITY_IAM \
  --no-execute-changeset
```

Even `--no-execute-changeset` creates AWS change-set state, so it is prohibited
until explicit provisioning approval.

## Safe teardown

1. Verify execution is disabled and prevent service restart.
2. Stop the service and confirm no application process remains.
3. Inspect broker state through the approved recovery procedure.
4. Create and verify a final state-volume snapshot.
5. Record the state volume ID, snapshot IDs, Elastic IP allocation ID, stack ID,
   and deployed release identity.
6. Delete replaceable compute only after confirming retained-resource behavior.
7. Preserve the state volume, snapshots, log groups, secret boundary, and Elastic
   IP. CloudFormation retention policies intentionally leave these behind.
8. Release the Elastic IP only when permanently retiring the registered static
   identity; detached public IPv4 addresses continue to incur cost.
9. Delete retained state only through a separately reviewed destructive action.

## Recovery onto a replacement host

1. Keep execution disabled and do not start application code.
2. Create the replacement in the same Availability Zone as the retained state
   volume, or restore an encrypted snapshot into the replacement AZ.
3. Attach the exact authoritative state volume with Multi-Attach disabled.
4. Re-associate the exact retained Elastic IP.
5. Restore the instance profile, no-ingress security group, SSM registration,
   clock synchronization, logging, and filesystem ownership.
6. Verify the volume is mounted at `/var/lib/algotrader`; never fall back to an
   empty root-directory path.
7. Deploy the exact approved release and restore the manual session only if it is
   still eligible.
8. Run recovery, continuity, preflight, and health inspection.
9. Treat pending orders, broker conflicts, corrupt state, or missing history as
   blocking conditions.
10. Require a later explicit operator action before execution can be enabled.

Application restart or EC2 replacement must never submit an order solely because
it restarted.
