# Staging Deny-by-Default Egress

Schema version: `omega-staging-deny-by-default-egress-v1`.

The proposed App Runner service uses a dedicated VPC connector, two dedicated private subnets, a custom network ACL with no allow entries, route tables with no explicit routes, and a connector security group with no ingress and no usable egress path. There is no internet/NAT/transit/VPN/peering gateway, VPC endpoint, default IPv4/IPv6 route, or public IP assignment.

EC2 creates an implicit allow-all outbound rule when a security group is created with no egress rules. To avoid that unsafe ambiguity, the connector group contains one inline TCP/9 rule whose destination is a dedicated security group that has no ingress and is never attached to any resource. That restricted rule suppresses the implicit allow-all rule. The custom network ACL independently denies every packet. The App Runner connector is structurally required to use only the connector group, never the sink group.

Private ingress and disabled automatic deployments remain mandatory. This design has passed semantic structural checks and cfn-lint 1.53.2 on Windows, but it has not been deployed or observed. State: `DESIGNED_NOT_DEPLOYMENT_VERIFIED`.
