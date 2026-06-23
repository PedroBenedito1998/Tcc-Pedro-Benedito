#!/bin/bash

BASE_DIR="/home/wifi/Documents/Benedito/dumbell"
OUT="${BASE_DIR}/auditoria_baseline_$(date +%Y%m%d_%H%M%S).log"
RESUMO="${BASE_DIR}/resumo_auditoria_baseline_$(date +%Y%m%d_%H%M%S).txt"

mkdir -p "$BASE_DIR"

echo "============================================================"
echo " AUDITORIA BASELINE SDN / RYU SIMPLE_SWITCH / SEM QoS"
echo "============================================================"
echo "Log completo: $OUT"
echo "Resumo:       $RESUMO"
echo ""
echo "IMPORTANTE:"
echo "1) Deixe esta auditoria rodando."
echo "2) Em OUTRO terminal rode o script do baseline:"
echo "   cd ~/Documents/tcc"
echo "   bash rodar_30_vezes_baseline.sh"
echo ""
echo "Esperado no baseline:"
echo " - Pacotes podem estar marcados com DSCP."
echo " - NAO deve existir set_queue."
echo " - NAO deve existir QoS linux-htb configurado pelo OVS na porta s2-eth1."
echo " - O controlador simple_switch_13 deve apenas encaminhar."
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
    echo "Provavelmente o Mininet baseline nao foi iniciado no outro terminal."
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
        echo "### 7. OVS APPCTL QOS SHOW $PORTA_GARGALO"
        ovs-appctl qos/show "$PORTA_GARGALO" 2>&1

        echo ""
        echo "### 8. TC QDISC $PORTA_GARGALO"
        tc -s qdisc show dev "$PORTA_GARGALO" 2>&1

        echo ""
        echo "### 9. TC CLASS $PORTA_GARGALO"
        tc -s class show dev "$PORTA_GARGALO" 2>&1

        echo ""
        echo "### 10. DATAPATH FLOWS COMPLETO"
        ovs-appctl dpctl/dump-flows -m 2>&1

    } >> "$OUT"

    sleep 1
done

echo ""
echo "Coleta encerrada."
echo "Gerando resumo..."
echo ""

LATEST_BASELINE_TXT=$(ls -t "${BASE_DIR}"/rodadas_baseline/SemQoSBanda*/*baseline_*.txt 2>/dev/null | head -n 1)
LATEST_BASELINE_DIR=""
if [ -n "$LATEST_BASELINE_TXT" ]; then
    LATEST_BASELINE_DIR=$(dirname "$LATEST_BASELINE_TXT")
fi

{
    echo "============================================================"
    echo " RESUMO DA AUDITORIA BASELINE"
    echo "============================================================"
    echo ""
    echo "Log completo:"
    echo "$OUT"
    echo ""
    echo "Arquivo TXT baseline mais recente:"
    echo "${LATEST_BASELINE_TXT:-NAO ENCONTRADO}"
    echo ""

    echo "============================================================"
    echo "1. MAPA ESPERADO DOS DSCPs NOS PACOTES"
    echo "============================================================"
    echo "Voz        DSCP 46 -> ToS decimal 184 -> ToS hex 0xb8"
    echo "Video      DSCP 34 -> ToS decimal 136 -> ToS hex 0x88"
    echo "Background DSCP 8  -> ToS decimal 32  -> ToS hex 0x20"
    echo "BestEffort DSCP 0  -> ToS decimal 0   -> ToS hex 0x00"
    echo ""
    echo "No baseline, esses DSCPs podem existir nos pacotes,"
    echo "mas NAO deve haver set_queue nem filas QoS por classe."

    echo ""
    echo "============================================================"
    echo "2. SWITCH S2 FOI ENCONTRADO?"
    echo "============================================================"
    grep -n "PORTAS DO S2" "$OUT" | head -n 5 || echo "ERRO: nenhuma amostra com s2 encontrada."

    echo ""
    echo "============================================================"
    echo "3. CONTROLADOR CONECTOU?"
    echo "============================================================"
    grep -n "is_connected: true" "$OUT" | head -n 10 || echo "AVISO: is_connected true nao encontrado no log da auditoria."

    echo ""
    echo "============================================================"
    echo "4. EXISTE SET_QUEUE? NO BASELINE DEVE SER ZERO"
    echo "============================================================"
    SETQ=$(grep -c "set_queue" "$OUT")
    echo "Ocorrencias de set_queue: $SETQ"
    if [ "$SETQ" -eq 0 ]; then
        echo "OK: baseline nao instalou regras set_queue."
    else
        echo "ERRO: baseline encontrou set_queue. Isso nao deveria ocorrer no simple_switch_13."
        grep -n "set_queue" "$OUT" | head -n 50
    fi

    echo ""
    echo "============================================================"
    echo "5. EXISTE QoS LINUX-HTB VIA OVS? NO BASELINE NAO DEVE EXISTIR"
    echo "============================================================"
    QOS_OVS=$(grep -c "QoS: s2-eth1 linux-htb" "$OUT")
    echo "Ocorrencias de 'QoS: s2-eth1 linux-htb': $QOS_OVS"
    if [ "$QOS_OVS" -eq 0 ]; then
        echo "OK: baseline nao aplicou QoS OVS por fila em s2-eth1."
    else
        echo "AVISO/ERRO: apareceu QoS linux-htb em s2-eth1. Verifique se sobrou configuracao do cenario QoS."
        grep -n -A30 "QoS: s2-eth1 linux-htb" "$OUT" | head -n 80
    fi

    echo ""
    echo "============================================================"
    echo "6. OPENFLOW DO S2: REGRAS DEVEM SER DE ENCAMINHAMENTO SIMPLES"
    echo "============================================================"
    echo "Amostras de flows:"
    grep -n "priority" "$OUT" | grep "actions=" | head -n 30 || echo "Nenhuma regra OpenFlow encontrada."

    echo ""
    echo "============================================================"
    echo "7. DATAPATH: VOZ UDP COM DSCP 46 / TOS 0xb8"
    echo "============================================================"
    grep -nE "ipv4\(src=20\.0\.0\.1,dst=10\.0\.0\.1,proto=17,tos=0xb8" "$OUT" | tail -n 20 || echo "NAO ENCONTRADO: voz UDP com tos=0xb8 no datapath."

    echo ""
    echo "============================================================"
    echo "8. DATAPATH: VIDEO UDP COM DSCP 34 / TOS 0x88"
    echo "============================================================"
    grep -nE "ipv4\(src=20\.0\.0\.2,dst=10\.0\.0\.2,proto=17,tos=0x88" "$OUT" | tail -n 20 || echo "NAO ENCONTRADO: video UDP com tos=0x88 no datapath."

    echo ""
    echo "============================================================"
    echo "9. DATAPATH: BACKGROUND TCP COM DSCP 8 / TOS 0x20"
    echo "============================================================"
    grep -nE "ipv4\(src=20\.0\.0\.3,dst=10\.0\.0\.3,proto=6,tos=0x20" "$OUT" | tail -n 20 || echo "NAO ENCONTRADO: background TCP com tos=0x20 no datapath."

    echo ""
    echo "============================================================"
    echo "10. ERRO DE MARCACAO: VOZ UDP COM TOS 0"
    echo "============================================================"
    VOZ_ERR=$(grep -cE "ipv4\(src=20\.0\.0\.1,dst=10\.0\.0\.1,proto=17,tos=0/0xfc" "$OUT")
    echo "Ocorrencias voz UDP tos=0: $VOZ_ERR"
    if [ "$VOZ_ERR" -eq 0 ]; then
        echo "OK: voz UDP nao apareceu sem marcacao no datapath."
    else
        echo "ERRO: voz UDP apareceu sem DSCP no datapath."
        grep -nE "ipv4\(src=20\.0\.0\.1,dst=10\.0\.0\.1,proto=17,tos=0/0xfc" "$OUT" | head -n 20
    fi

    echo ""
    echo "============================================================"
    echo "11. ERRO DE MARCACAO: VIDEO UDP COM TOS 0"
    echo "============================================================"
    VIDEO_ERR=$(grep -cE "ipv4\(src=20\.0\.0\.2,dst=10\.0\.0\.2,proto=17,tos=0/0xfc" "$OUT")
    echo "Ocorrencias video UDP tos=0: $VIDEO_ERR"
    if [ "$VIDEO_ERR" -eq 0 ]; then
        echo "OK: video UDP nao apareceu sem marcacao no datapath."
    else
        echo "ERRO: video UDP apareceu sem DSCP no datapath."
        grep -nE "ipv4\(src=20\.0\.0\.2,dst=10\.0\.0\.2,proto=17,tos=0/0xfc" "$OUT" | head -n 20
    fi

    echo ""
    echo "============================================================"
    echo "12. CONTAGEM DSCP NO TXT FINAL DO BASELINE"
    echo "============================================================"
    if [ -n "$LATEST_BASELINE_TXT" ] && [ -f "$LATEST_BASELINE_TXT" ]; then
        echo "Arquivo analisado:"
        echo "$LATEST_BASELINE_TXT"
        echo ""
        awk -F'\t' 'NR>1 {gsub(/"/,"",$6); if ($6!="") print $6}' "$LATEST_BASELINE_TXT" | sort | uniq -c
    else
        echo "ERRO: TXT final do baseline nao encontrado."
    fi

    echo ""
    echo "============================================================"
    echo "13. ARQUIVOS FINAIS GERADOS"
    echo "============================================================"
    if [ -n "$LATEST_BASELINE_DIR" ] && [ -d "$LATEST_BASELINE_DIR" ]; then
        echo "Pasta:"
        echo "$LATEST_BASELINE_DIR"
        echo ""
        ls -lh "$LATEST_BASELINE_DIR"
    else
        echo "ERRO: pasta final do baseline nao encontrada."
    fi

    echo ""
    echo "============================================================"
    echo "14. VERIFICACAO DOS .DAT"
    echo "============================================================"
    if [ -n "$LATEST_BASELINE_DIR" ] && [ -d "$LATEST_BASELINE_DIR" ]; then
        echo "Arquivos .dat encontrados:"
        ls -lh "$LATEST_BASELINE_DIR"/*.dat 2>/dev/null || echo "ERRO: nenhum .dat encontrado."
    else
        echo "ERRO: pasta final do baseline nao encontrada."
    fi

    echo ""
    echo "============================================================"
    echo "15. LOG DO RYU BASELINE"
    echo "============================================================"
    if [ -f /tmp/ryu_automacao.log ]; then
        echo "Erros no /tmp/ryu_automacao.log:"
        grep -Ei "error|exception|traceback|failed|bad|warn" /tmp/ryu_automacao.log || echo "OK: nenhum erro grave encontrado no log do Ryu."
    else
        echo "AVISO: /tmp/ryu_automacao.log nao encontrado."
    fi

    echo ""
    echo "============================================================"
    echo "16. CONCLUSAO AUTOMATICA"
    echo "============================================================"

    VOZ_DSCP=$(grep -cE "ipv4\(src=20\.0\.0\.1,dst=10\.0\.0\.1,proto=17,tos=0xb8" "$OUT")
    VIDEO_DSCP=$(grep -cE "ipv4\(src=20\.0\.0\.2,dst=10\.0\.0\.2,proto=17,tos=0x88" "$OUT")
    BG_DSCP=$(grep -cE "ipv4\(src=20\.0\.0\.3,dst=10\.0\.0\.3,proto=6,tos=0x20" "$OUT")
    SETQ_COUNT=$(grep -c "set_queue" "$OUT")
    QOS_COUNT=$(grep -c "QoS: s2-eth1 linux-htb" "$OUT")

    echo "Voz UDP com DSCP correto no datapath:        $VOZ_DSCP"
    echo "Video UDP com DSCP correto no datapath:      $VIDEO_DSCP"
    echo "Background TCP com DSCP correto no datapath: $BG_DSCP"
    echo "Ocorrencias set_queue:                       $SETQ_COUNT"
    echo "Ocorrencias QoS OVS linux-htb em s2-eth1:    $QOS_COUNT"
    echo "Ocorrencias erro voz UDP tos=0:              $VOZ_ERR"
    echo "Ocorrencias erro video UDP tos=0:            $VIDEO_ERR"
    echo ""

    if [ "$VOZ_DSCP" -gt 0 ] && [ "$VIDEO_DSCP" -gt 0 ] && [ "$BG_DSCP" -gt 0 ] && [ "$SETQ_COUNT" -eq 0 ] && [ "$QOS_COUNT" -eq 0 ] && [ "$VOZ_ERR" -eq 0 ] && [ "$VIDEO_ERR" -eq 0 ]; then
        echo "RESULTADO FINAL: BASELINE CORRETO."
        echo "Pacotes estao marcados, mas nao ha priorizacao via set_queue/QoS."
        echo "Este baseline esta adequado para comparar contra o cenario QoS."
    elif [ "$SETQ_COUNT" -gt 0 ] || [ "$QOS_COUNT" -gt 0 ]; then
        echo "RESULTADO FINAL: BASELINE CONTAMINADO POR QoS."
        echo "Apareceu set_queue ou QoS OVS. Limpe o ambiente antes de validar."
    else
        echo "RESULTADO FINAL: BASELINE INCONCLUSIVO OU COM FALHA."
        echo "Verifique os blocos acima."
    fi

} > "$RESUMO"

echo "============================================================"
echo " AUDITORIA BASELINE FINALIZADA"
echo "============================================================"
echo "Log completo:"
echo "$OUT"
echo ""
echo "Resumo:"
echo "$RESUMO"
echo ""
echo "Para ver a conclusao:"
echo "grep -A25 \"16. CONCLUSAO AUTOMATICA\" \"$RESUMO\""
echo ""
echo "Para ver tudo:"
echo "cat \"$RESUMO\""