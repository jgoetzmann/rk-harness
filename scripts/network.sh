#!/bin/sh
# Egress allowlist for the rk-net bridge — HANDOFF §13.3. Run inside WSL2 as root after
# `docker network create rk-net`. Everything not listed is dropped.
set -eu
NET="${1:-rk-net}"
# HANDOFF §13.3 list plus the two hosts Codex OAuth (RK_LLM=codex) talks to.
ALLOW="api.openai.com github.com api.github.com codeload.github.com pypi.org files.pythonhosted.org chatgpt.com auth.openai.com"

SUBNET=$(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' "$NET")
echo "rk-net subnet: $SUBNET"

# Fresh chain hooked from DOCKER-USER (evaluated before Docker's own rules).
iptables -N RK-EGRESS 2>/dev/null || iptables -F RK-EGRESS
iptables -D DOCKER-USER -s "$SUBNET" -j RK-EGRESS 2>/dev/null || true
iptables -I DOCKER-USER -s "$SUBNET" -j RK-EGRESS

# Established flows and DNS to the Docker resolver only.
iptables -A RK-EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
iptables -A RK-EGRESS -p udp --dport 53 -j RETURN
iptables -A RK-EGRESS -p tcp --dport 53 -j RETURN

for host in $ALLOW; do
  for ip in $(getent ahostsv4 "$host" | awk '{print $1}' | sort -u); do
    iptables -A RK-EGRESS -d "$ip" -p tcp --dport 443 -j RETURN
    echo "allow $host -> $ip:443"
  done
done

iptables -A RK-EGRESS -j LOG --log-prefix "rk-egress-drop: " --log-level 4
iptables -A RK-EGRESS -j DROP
echo "egress restricted for $NET. Re-run periodically: the allowed hosts' addresses rotate."
