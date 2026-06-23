#!/bin/bash

BASE_DIR="/home/wifi/Documents/Benedito/dumbell"
OUT="${BASE_DIR}/auditoria_qos_$(date +%Y%m%d_%H%M%S).log"
RESUMO="${BASE_DIR}/resumo_auditoria_qos_$(date +%Y%m%d_%H%M%S).txt"

mkdir -p "$BASE_DIR"

echo "============================================================"
echo " AUDITORIA QoS SDN / RYU / OVS / FILAS HTB"
echo "============================================================"
echo "Log completo: $OUT"
echo "Resumo:       $RESUMO"
echo ""
echo "IMPORTANTE:"
echo "1) Deixe esta auditoria rodando."
echo "2) Em OUTRO terminal rode:"
echo "   cd ~/Documents/tcc"
echo "   bash rodar_30_vezes_qos.sh"
echo ""
echo "Aguardando o switch s2 aparecer por ate 240 segundos..."
echo ""

S2_ACHADO=0

for espera in $(seq 1 240)
do
    if ovs-vsctl br-exists s2 2>/dev/null; then
        S2_ACHADO=1
        echo "s2 encontrado. Iniciando coleta."
        break
    fi

    echo "s2 ainda nao existe... tentativa ${espera}/240"
    sleep 1
done

if [ "$S2_ACHADO" -ne 1 ]; then
    echo "ERRO: s2 nao apareceu dentro do tempo limite."
    echo "Provavelmente o Mininet nao foi iniciado no outro terminal."
    exit 1
fi

PORTA_GARGALO="s2-eth1"

echo ""
echo "Coletando dados por 150 segundos..."
echo "Porta de gargalo assumida: $PORTA_GARGALO"
echo ""

for i in $(seq 1 150)
do
    if ! ovs-vsctl br-exists s2 2>/dev/null; then
        echo "Amostra $i: s2 nao existe mais. Continuando registro..."
        {
            echo ""
            echo "============================================================"
            echo "AMOSTRA $i - $(date)"
            echo "============================================================"
            echo "s2 nao existe mais nesta amostra."
        } >> "$OUT"
        sleep 1
        continue
    fi

    echo "Amostra $i/150 coletada."

    {
        echo ""
        echo "============================================================"
        echo "AMOSTRA $i - $(date)"
        echo "============================================================"

        echo ""
        echo "### 1. BRIDGES EXISTENTES"
        ovs-vsctl list-br 2>&1

        echo ""
        echo "### 2. OVS SHOW"
        ovs-vsctl show 2>&1

        echo ""
        echo "### 3. PORTAS DO S2"
        ovs-ofctl -O OpenFlow13 show s2 2>&1

        echo ""
        echo "### 4. FLOWS OPENFLOW DO S2"
        ovs-ofctl -O OpenFlow13 dump-flows s2 2>&1

        echo ""
        echo "### 5. PORT STATS DO S2"
        ovs-ofctl -O OpenFlow13 dump-ports s2 2>&1

        echo ""
        echo "### 6. QOS DA PORTA $PORTA_GARGALO"
        ovs-vsctl get Port "$PORTA_GARGALO" qos 2>&1

        echo ""
        echo "### 7. LIST QOS"
        ovs-vsctl list QoS 2>&1

        echo ""
        echo "### 8. LIST QUEUE"
        ovs-vsctl list Queue 2>&1

        echo ""
        echo "### 9. OVS APPCTL QOS SHOW $PORTA_GARGALO"
        ovs-appctl qos/show "$PORTA_GARGALO" 2>&1

        echo ""
        echo "### 10. TC QDISC $PORTA_GARGALO"
        tc -s qdisc show dev "$PORTA_GARGALO" 2>&1

        echo ""
        echo "### 11. TC CLASS $PORTA_GARGALO"
        tc -s class show dev "$PORTA_GARGALO" 2>&1

        echo ""
        echo "### 12. DATAPATH FLOWS COMPLETO"
        ovs-appctl dpctl/dump-flows -m 2>&1

    } >> "$OUT"

    sleep 1
done

echo ""
echo "Coleta encerrada."
echo "Gerando resumo..."
echo ""

{
    echo "============================================================"
    echo " RESUMO DA AUDITORIA QoS"
    echo "============================================================"
    echo ""
    echo "Log completo:"
    echo "$OUT"
    echo ""

    echo "============================================================"
    echo "1. MAPA ESPERADO"
    echo "============================================================"
    echo "Voz        DSCP 46 -> ToS decimal 184 -> ToS hex 0xb8 -> Queue 0"
    echo "Video      DSCP 34 -> ToS decimal 136 -> ToS hex 0x88 -> Queue 1"
    echo "Background DSCP 8  -> ToS decimal 32  -> ToS hex 0x20 -> Queue 3"
    echo "BestEffort DSCP 0  -> ToS decimal 0   -> ToS hex 0x00 -> Queue 2"
    echo ""

    echo "============================================================"
    echo "2. SWITCH S2 FOI ENCONTRADO?"
    echo "============================================================"
    grep -n "PORTAS DO S2" "$OUT" | head -n 5 || echo "ERRO: nenhuma amostra com s2 encontrada."

    echo ""
    echo "============================================================"
    echo "3. QOS HTB EXISTE NA PORTA s2-eth1?"
    echo "============================================================"
    grep -n "QoS: s2-eth1 linux-htb" "$OUT" | tail -n 10 || echo "ERRO: QoS linux-htb nao encontrado em s2-eth1."

    echo ""
    echo "============================================================"
    echo "4. REGRAS OPENFLOW COM SET_QUEUE"
    echo "============================================================"
    grep -n "set_queue" "$OUT" | tail -n 80 || echo "ERRO: set_queue nao encontrado."

    echo ""
    echo "============================================================"
    echo "5. PING / ICMP MARCADO"
    echo "============================================================"
    echo ""
    echo "ICMP Voz esperado: nw_tos=184 -> set_queue:0"
    grep -nE "icmp.*nw_tos=184.*set_queue:0" "$OUT" | tail -n 10 || echo "NAO ENCONTRADO: ICMP voz marcado."
    echo ""
    echo "ICMP Video esperado: nw_tos=136 -> set_queue:1"
    grep -nE "icmp.*nw_tos=136.*set_queue:1" "$OUT" | tail -n 10 || echo "NAO ENCONTRADO: ICMP video marcado."
    echo ""
    echo "ICMP Background esperado: nw_tos=32 -> set_queue:3"
    grep -nE "icmp.*nw_tos=32.*set_queue:3" "$OUT" | tail -n 10 || echo "NAO ENCONTRADO: ICMP background marcado."

    echo ""
    echo "============================================================"
    echo "6. TRAFEGO PRINCIPAL OPENFLOW - RESULTADO ESPERADO"
    echo "============================================================"
    echo ""
    echo "VOZ UDP esperado: 20.0.0.1 -> 10.0.0.1 | nw_tos=184 | set_queue:0"
    grep -nE "udp.*nw_src=20\.0\.0\.1,nw_dst=10\.0\.0\.1.*nw_tos=184.*set_queue:0" "$OUT" | tail -n 20 || echo "NAO ENCONTRADO: VOZ UDP correta."
    echo ""
    echo "VIDEO UDP esperado: 20.0.0.2 -> 10.0.0.2 | nw_tos=136 | set_queue:1"
    grep -nE "udp.*nw_src=20\.0\.0\.2,nw_dst=10\.0\.0\.2.*nw_tos=136.*set_queue:1" "$OUT" | tail -n 20 || echo "NAO ENCONTRADO: VIDEO UDP correto."
    echo ""
    echo "BACKGROUND TCP esperado: 20.0.0.3 -> 10.0.0.3 | nw_tos=32 | set_queue:3"
    grep -nE "tcp.*nw_src=20\.0\.0\.3,nw_dst=10\.0\.0\.3.*nw_tos=32.*set_queue:3" "$OUT" | tail -n 20 || echo "NAO ENCONTRADO: BACKGROUND TCP correto."

    echo ""
    echo "============================================================"
    echo "7. ERRO ANTIGO - UDP SEM MARCACAO CAINDO NA QUEUE 2"
    echo "============================================================"
    echo ""
    echo "VOZ UDP errado: nw_tos=0 -> set_queue:2"
    grep -nE "udp.*nw_src=20\.0\.0\.1,nw_dst=10\.0\.0\.1.*nw_tos=0.*set_queue:2" "$OUT" | tail -n 20 || echo "OK: erro antigo da voz UDP nao encontrado."
    echo ""
    echo "VIDEO UDP errado: nw_tos=0 -> set_queue:2"
    grep -nE "udp.*nw_src=20\.0\.0\.2,nw_dst=10\.0\.0\.2.*nw_tos=0.*set_queue:2" "$OUT" | tail -n 20 || echo "OK: erro antigo do video UDP nao encontrado."

    echo ""
    echo "============================================================"
    echo "8. DATAPATH FLOWS - TOS HEX"
    echo "============================================================"
    echo ""
    echo "VOZ esperado no datapath: src=20.0.0.1 dst=10.0.0.1 proto=17 tos=0xb8"
    grep -nE "ipv4\(src=20\.0\.0\.1,dst=10\.0\.0\.1,proto=17,tos=0xb8" "$OUT" | tail -n 20 || echo "NAO ENCONTRADO: datapath voz com tos=0xb8."
    echo ""
    echo "VIDEO esperado no datapath: src=20.0.0.2 dst=10.0.0.2 proto=17 tos=0x88"
    grep -nE "ipv4\(src=20\.0\.0\.2,dst=10\.0\.0\.2,proto=17,tos=0x88" "$OUT" | tail -n 20 || echo "NAO ENCONTRADO: datapath video com tos=0x88."
    echo ""
    echo "BACKGROUND esperado no datapath: src=20.0.0.3 dst=10.0.0.3 proto=6 tos=0x20"
    grep -nE "ipv4\(src=20\.0\.0\.3,dst=10\.0\.0\.3,proto=6,tos=0x20" "$OUT" | tail -n 20 || echo "NAO ENCONTRADO: datapath background com tos=0x20."

    echo ""
    echo "============================================================"
    echo "9. ERRO ANTIGO NO DATAPATH - UDP COM TOS 0"
    echo "============================================================"
    echo ""
    echo "VOZ UDP errado no datapath: tos=0"
    grep -nE "ipv4\(src=20\.0\.0\.1,dst=10\.0\.0\.1,proto=17,tos=0/0xfc" "$OUT" | tail -n 20 || echo "OK: datapath voz UDP tos=0 nao encontrado."
    echo ""
    echo "VIDEO UDP errado no datapath: tos=0"
    grep -nE "ipv4\(src=20\.0\.0\.2,dst=10\.0\.0\.2,proto=17,tos=0/0xfc" "$OUT" | tail -n 20 || echo "OK: datapath video UDP tos=0 nao encontrado."

    echo ""
    echo "============================================================"
    echo "10. ESTATISTICAS DAS FILAS OVS"
    echo "============================================================"
    grep -ni -A40 "OVS APPCTL QOS SHOW" "$OUT" | tail -n 180 || echo "Nao foi possivel ler ovs-appctl qos/show."

    echo ""
    echo "============================================================"
    echo "11. ESTATISTICAS TC CLASS"
    echo "============================================================"
    grep -ni -A80 "TC CLASS" "$OUT" | tail -n 220 || echo "Nao foi possivel ler tc class."

    echo ""
    echo "============================================================"
    echo "12. ERROS POSSIVEIS NA AUDITORIA"
    echo "============================================================"
    grep -niE "error|erro|failed|fail|cannot|no such|permission|denied|traceback|exception" "$OUT" | tail -n 80 || echo "Nenhum erro obvio encontrado."

    echo ""
    echo "============================================================"
    echo "13. CONCLUSAO AUTOMATICA"
    echo "============================================================"

    VOZ_OK=$(grep -E "udp.*nw_src=20\.0\.0\.1,nw_dst=10\.0\.0\.1.*nw_tos=184.*set_queue:0" "$OUT" | wc -l)
    VIDEO_OK=$(grep -E "udp.*nw_src=20\.0\.0\.2,nw_dst=10\.0\.0\.2.*nw_tos=136.*set_queue:1" "$OUT" | wc -l)
    BG_OK=$(grep -E "tcp.*nw_src=20\.0\.0\.3,nw_dst=10\.0\.0\.3.*nw_tos=32.*set_queue:3" "$OUT" | wc -l)

    VOZ_ERR=$(grep -E "udp.*nw_src=20\.0\.0\.1,nw_dst=10\.0\.0\.1.*nw_tos=0.*set_queue:2" "$OUT" | wc -l)
    VIDEO_ERR=$(grep -E "udp.*nw_src=20\.0\.0\.2,nw_dst=10\.0\.0\.2.*nw_tos=0.*set_queue:2" "$OUT" | wc -l)

    echo "Linhas corretas VOZ UDP:       $VOZ_OK"
    echo "Linhas corretas VIDEO UDP:     $VIDEO_OK"
    echo "Linhas corretas BACKGROUND TCP:$BG_OK"
    echo "Linhas erro antigo VOZ UDP:    $VOZ_ERR"
    echo "Linhas erro antigo VIDEO UDP:  $VIDEO_ERR"
    echo ""

    if [ "$VOZ_OK" -gt 0 ] && [ "$VIDEO_OK" -gt 0 ] && [ "$BG_OK" -gt 0 ] && [ "$VOZ_ERR" -eq 0 ] && [ "$VIDEO_ERR" -eq 0 ]; then
        echo "RESULTADO FINAL: SUCESSO COMPLETO."
        echo "Todos os fluxos principais foram marcados e enviados para as filas corretas."
    elif [ "$BG_OK" -gt 0 ] && { [ "$VOZ_ERR" -gt 0 ] || [ "$VIDEO_ERR" -gt 0 ]; }; then
        echo "RESULTADO FINAL: FALHA PARCIAL."
        echo "Background TCP esta correto, mas Voz/Video UDP ainda aparecem sem marcacao e caindo na Queue 2."
    else
        echo "RESULTADO FINAL: INCONCLUSIVO OU FALHA."
        echo "Verifique os blocos acima para identificar o ponto exato."
    fi

} > "$RESUMO"

echo "============================================================"
echo " AUDITORIA FINALIZADA"
echo "============================================================"
echo "Log completo:"
echo "$OUT"
echo ""
echo "Resumo:"
echo "$RESUMO"
echo ""
echo "Para ver o resumo:"
echo "cat \"$RESUMO\""
echo ""
echo "Para ver apenas a conclusao:"
echo "grep -A20 \"13. CONCLUSAO AUTOMATICA\" \"$RESUMO\""