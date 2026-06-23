#!/bin/bash

OUT="/home/wifi/Documents/Benedito/dumbell/auditoria_qos_$(date +%Y%m%d_%H%M%S).log"

echo "Auditoria salva em: $OUT"
echo "Agora rode em OUTRO terminal: bash rodar_30_vezes_qos.sh"
echo "Aguardando o switch s2 aparecer..."

while ! ovs-vsctl br-exists s2 2>/dev/null
do
    echo "s2 ainda nao existe. Aguardando..."
    sleep 1
done

echo "s2 encontrado. Iniciando auditoria por 90 segundos..."

for i in $(seq 1 90)
do
    {
        echo ""
        echo "============================================================"
        echo "AMOSTRA $i - $(date)"
        echo "============================================================"

        echo ""
        echo "### OVS SHOW"
        ovs-vsctl show 2>&1

        echo ""
        echo "### PORTAS DO S2"
        ovs-ofctl -O OpenFlow13 show s2 2>&1

        echo ""
        echo "### FLOWS DO S2"
        ovs-ofctl -O OpenFlow13 dump-flows s2 2>&1

        echo ""
        echo "### PORT STATS DO S2"
        ovs-ofctl -O OpenFlow13 dump-ports s2 2>&1

        echo ""
        echo "### QOS PORT S2-ETH1"
        ovs-vsctl get Port s2-eth1 qos 2>&1

        echo ""
        echo "### LIST QOS"
        ovs-vsctl list QoS 2>&1

        echo ""
        echo "### LIST QUEUE"
        ovs-vsctl list Queue 2>&1

        echo ""
        echo "### OVS APPCTL QOS SHOW"
        ovs-appctl qos/show s2-eth1 2>&1

        echo ""
        echo "### TC QDISC S2-ETH1"
        tc -s qdisc show dev s2-eth1 2>&1

        echo ""
        echo "### TC CLASS S2-ETH1"
        tc -s class show dev s2-eth1 2>&1

    } >> "$OUT"

    sleep 1
done

echo ""
echo "Auditoria finalizada."
echo "Arquivo:"
echo "$OUT"
