#!/bin/bash
set -o pipefail

# ============================================================
# TESTE DEFINITIVO CORRIGIDO - 5 REPETICOES
# TCC QoS SDN x Baseline
# ============================================================

BASE_ROOT="/home/wifi/Documents/Benedito/dumbell"
TCC_DIR="$HOME/Documents/tcc"

REP_INICIO=1
REP_FIM=5

BWS=(10 100 1000)
DELAYS=(0 50 100 300)
LOSSES=(0.0 0.1 1.0 3.0)

RUN_ID=$(date +%Y%m%d_%H%M%S)
OUT_DIR="${BASE_ROOT}/definitivo_corrigido_5reps_${RUN_ID}"
BASE="$OUT_DIR"
LOG_DIR="${OUT_DIR}/logs_execucao"
VERIF_DIR="${OUT_DIR}/verificacoes_qdisc"
RESUMO_FINAL="${OUT_DIR}/RESUMO_FINAL_DEFINITIVO.txt"
PRECHECK="${OUT_DIR}/PRECHECK_RECURSOS.txt"

mkdir -p "$OUT_DIR"
mkdir -p "$LOG_DIR"
mkdir -p "$VERIF_DIR"
mkdir -p "${BASE}/rodadas_baseline"
mkdir -p "${BASE}/rodadas_qos"

echo "================================================="
echo " TESTE DEFINITIVO CORRIGIDO - 5 REPETICOES"
echo "================================================="
echo "Saida geral:"
echo "$OUT_DIR"
echo ""

cd "$TCC_DIR" || {
    echo "[ERRO] Nao consegui entrar em $TCC_DIR"
    exit 1
}

precheck_recursos() {
    echo "============================================================" > "$PRECHECK"
    echo "PRECHECK DE RECURSOS - $(date)" >> "$PRECHECK"
    echo "============================================================" >> "$PRECHECK"
    echo "" >> "$PRECHECK"

    echo "### DISCO" >> "$PRECHECK"
    df -h "$BASE_ROOT" >> "$PRECHECK" 2>&1
    echo "" >> "$PRECHECK"

    echo "### MEMORIA" >> "$PRECHECK"
    free -h >> "$PRECHECK" 2>&1
    echo "" >> "$PRECHECK"

    echo "### TAMANHO DO PROJETO" >> "$PRECHECK"
    du -sh "$BASE_ROOT" >> "$PRECHECK" 2>&1
    echo "" >> "$PRECHECK"

    echo "### PASTAS MAIS PESADAS" >> "$PRECHECK"
    du -h --max-depth=1 "$BASE_ROOT" | sort -h >> "$PRECHECK" 2>&1
    echo "" >> "$PRECHECK"

    echo "### PROCESSOS ANTIGOS" >> "$PRECHECK"
    ps aux | grep -E "ryu-manager|baseline.py|Qos.py|iperf3|ITGRecv|ITGSend|tshark" | grep -v grep >> "$PRECHECK" 2>&1 || true
    echo "" >> "$PRECHECK"

    FREE_KB=$(df -Pk "$BASE_ROOT" | awk 'NR==2 {print $4}')
    FREE_GB=$((FREE_KB / 1024 / 1024))

    MEM_AVAIL_KB=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
    MEM_AVAIL_GB=$((MEM_AVAIL_KB / 1024 / 1024))

    echo "Espaco livre: ${FREE_GB} GB"
    echo "RAM disponivel: ${MEM_AVAIL_GB} GB"

    if [ "$FREE_GB" -lt 10 ]; then
        echo "[ERRO_FATAL] Menos de 10 GB livres. Nao rode definitivo."
        exit 1
    fi

    if [ "$FREE_GB" -lt 20 ]; then
        echo "[AVISO] Menos de 20 GB livres. Pode ficar apertado."
    fi

    if [ "$MEM_AVAIL_GB" -lt 2 ]; then
        echo "[AVISO] Menos de 2 GB de RAM disponivel. Feche programas."
    fi

    echo "[OK] Precheck salvo em: $PRECHECK"
    echo ""
}

validar_scripts() {
    echo "[1/8] Validando scripts principais..."

    for f in baseline.py Qos.py qos_switch.py auditoria_pos_teste_tcc.py analise_tcc_qos_baseline.py classificar_auditoria_tcc.py
    do
        if [ ! -f "$f" ]; then
            echo "[ERRO] Arquivo nao encontrado: $f"
            exit 1
        fi
    done

    python3 -m py_compile baseline.py || exit 1
    python3 -m py_compile Qos.py || exit 1
    python3 -m py_compile qos_switch.py || exit 1
    python3 -m py_compile auditoria_pos_teste_tcc.py || exit 1
    python3 -m py_compile analise_tcc_qos_baseline.py || exit 1
    python3 -m py_compile classificar_auditoria_tcc.py || exit 1

    echo "[OK] Scripts compilam."
    echo ""
}

limpar_ambiente() {
    sudo mn -c > /dev/null 2>&1
    sudo pkill -f ryu-manager > /dev/null 2>&1 || true
    sudo pkill -9 iperf3 > /dev/null 2>&1 || true
    sudo pkill -9 ITGRecv > /dev/null 2>&1 || true
    sudo pkill -9 ITGSend > /dev/null 2>&1 || true
    sudo pkill -f tshark > /dev/null 2>&1 || true
    sudo tc qdisc del dev s2-eth1 root > /dev/null 2>&1 || true
    sudo tc qdisc del dev r1-eth0 root > /dev/null 2>&1 || true
    sudo tc qdisc del dev r1-eth1 root > /dev/null 2>&1 || true
    sudo ovs-vsctl --if-exists clear Port s2-eth1 qos > /dev/null 2>&1 || true
    sudo ovs-vsctl --all destroy QoS -- --all destroy Queue > /dev/null 2>&1 || true
    sudo rm -f /tmp/cli_*.log /tmp/srv_*.log /tmp/cli_itg_*.bin
    sudo rm -f /tmp/delay_*.dat /tmp/jitter_*.dat /tmp/bitrate_*.dat /tmp/loss_*.dat
    sudo rm -f /tmp/ping_cli_*.log /tmp/tshark.log /tmp/tshark.pid
    sudo rm -f /tmp/qos_gargalo.pcap /tmp/baseline_gargalo.pcap
    sudo rm -f /tmp/ryu_automacao.log
    sudo rm -f /tmp/ryu_flowstats.csv /tmp/ryu_portstats.csv
    sleep 2
}

sufixo() {
    local bw="$1"
    local delay="$2"
    local loss="$3"
    local rep="$4"
    echo "bw${bw}_del${delay}_loss${loss}_rep${rep}"
}

cpu_total_ok() {
    local arq="$1"

    [ -s "$arq" ] || return 1
    head -n 1 "$arq" | grep -qx "timestamp,tag,cpu_total_percent,cpu_idle_percent,cpu_busy_percent" || return 1
    awk -F',' 'NR > 1 && $5 ~ /^[0-9]+([.][0-9]+)?$/ { ok = 1 } END { exit ok ? 0 : 1 }' "$arq"
}

verificar_arquivos_baseline() {
    local bw="$1"
    local delay="$2"
    local loss="$3"
    local rep="$4"

    local S=$(sufixo "$bw" "$delay" "$loss" "$rep")
    local DEST="${BASE}/rodadas_baseline/SemQoSBanda${bw}MbpsLoss${loss}Delay${delay}"

    local faltando=0

    for arq in \
        "${DEST}/baseline_${S}.txt" \
        "${DEST}/verificacao_baseline_${S}.txt" \
        "${DEST}/iperf_voz_${S}.log" \
        "${DEST}/cpu_baseline_${S}.csv" \
        "${DEST}/iperf_video_${S}.log" \
        "${DEST}/iperf_bg_${S}.log" \
        "${DEST}/delay_voz_${S}.dat" \
        "${DEST}/jitter_voz_${S}.dat" \
        "${DEST}/bitrate_voz_${S}.dat" \
        "${DEST}/loss_voz_${S}.dat" \
        "${DEST}/delay_video_${S}.dat" \
        "${DEST}/jitter_video_${S}.dat" \
        "${DEST}/bitrate_video_${S}.dat" \
        "${DEST}/loss_video_${S}.dat" \
        "${DEST}/ping_voz_${S}.log" \
        "${DEST}/ping_video_${S}.log" \
        "${DEST}/ping_bg_${S}.log"
    do
        if [ ! -s "$arq" ]; then
            faltando=$((faltando + 1))
        fi
    done

    if ! cpu_total_ok "${DEST}/cpu_baseline_${S}.csv"; then
        faltando=$((faltando + 1))
    fi

    [ "$faltando" -eq 0 ]
}

verificar_arquivos_qos() {
    local bw="$1"
    local delay="$2"
    local loss="$3"
    local rep="$4"

    local S=$(sufixo "$bw" "$delay" "$loss" "$rep")
    local DEST="${BASE}/rodadas_qos/ComQoSBanda${bw}MbpsLoss${loss}Delay${delay}"

    local faltando=0

    for arq in \
        "${DEST}/qos_${S}.txt" \
        "${DEST}/verificacao_qos_${S}.txt" \
        "${DEST}/cpu_qos_${S}.csv" \
        "${DEST}/ryu_flowstats_${S}.csv" \
        "${DEST}/ryu_portstats_${S}.csv" \
        "${DEST}/iperf_voz_${S}.log" \
        "${DEST}/iperf_video_${S}.log" \
        "${DEST}/iperf_bg_${S}.log" \
        "${DEST}/delay_voz_${S}.dat" \
        "${DEST}/jitter_voz_${S}.dat" \
        "${DEST}/bitrate_voz_${S}.dat" \
        "${DEST}/loss_voz_${S}.dat" \
        "${DEST}/delay_video_${S}.dat" \
        "${DEST}/jitter_video_${S}.dat" \
        "${DEST}/bitrate_video_${S}.dat" \
        "${DEST}/loss_video_${S}.dat" \
        "${DEST}/ping_voz_${S}.log" \
        "${DEST}/ping_video_${S}.log" \
        "${DEST}/ping_bg_${S}.log"
    do
        if [ ! -s "$arq" ]; then
            faltando=$((faltando + 1))
        fi
    done

    if ! cpu_total_ok "${DEST}/cpu_qos_${S}.csv"; then
        faltando=$((faltando + 1))
    fi

    [ "$faltando" -eq 0 ]
}

salvar_baseline() {
    local bw="$1"
    local delay="$2"
    local loss="$3"
    local rep="$4"

    local S=$(sufixo "$bw" "$delay" "$loss" "$rep")
    local DEST="${BASE}/rodadas_baseline/SemQoSBanda${bw}MbpsLoss${loss}Delay${delay}"
    local LOGSRC="${LOG_DIR}/baseline_sources_${S}.log"

    mkdir -p "$DEST"
    sudo chown -R wifi:wifi "$DEST" 2>/dev/null
    sudo chmod -R u+rwX "$DEST" 2>/dev/null

    {
        echo "SALVAR_BASELINE $S - $(date)"
        echo "DEST=$DEST"
        ls -lh "${BASE}/baseline_dualnet.txt" 2>/dev/null || echo "AUSENTE baseline_dualnet.txt"
        ls -lh /tmp/cli_5101.log 2>/dev/null || echo "AUSENTE cli_5101"
        ls -lh /tmp/cli_5102.log 2>/dev/null || echo "AUSENTE cli_5102"
        ls -lh /tmp/cli_5103.log 2>/dev/null || echo "AUSENTE cli_5103"
    } > "$LOGSRC"

    [ -f "${BASE}/baseline_dualnet.txt" ] && mv -f "${BASE}/baseline_dualnet.txt" "${DEST}/baseline_${S}.txt"
    sudo rm -f "${BASE}/baseline_dualnet.pcap"

    cp -f /tmp/cli_5101.log "${DEST}/iperf_voz_${S}.log" 2>/dev/null
    cp -f /tmp/cli_5102.log "${DEST}/iperf_video_${S}.log" 2>/dev/null
    cp -f /tmp/cli_5103.log "${DEST}/iperf_bg_${S}.log" 2>/dev/null

    cp -f /tmp/delay_5101.dat "${DEST}/delay_voz_${S}.dat" 2>/dev/null
    cp -f /tmp/jitter_5101.dat "${DEST}/jitter_voz_${S}.dat" 2>/dev/null
    cp -f /tmp/bitrate_5101.dat "${DEST}/bitrate_voz_${S}.dat" 2>/dev/null
    cp -f /tmp/loss_5101.dat "${DEST}/loss_voz_${S}.dat" 2>/dev/null

    cp -f /tmp/delay_5102.dat "${DEST}/delay_video_${S}.dat" 2>/dev/null
    cp -f /tmp/jitter_5102.dat "${DEST}/jitter_video_${S}.dat" 2>/dev/null
    cp -f /tmp/bitrate_5102.dat "${DEST}/bitrate_video_${S}.dat" 2>/dev/null
    cp -f /tmp/loss_5102.dat "${DEST}/loss_video_${S}.dat" 2>/dev/null

    cp -f /tmp/ping_cli_1.log "${DEST}/ping_voz_${S}.log" 2>/dev/null
    cp -f /tmp/ping_cli_2.log "${DEST}/ping_video_${S}.log" 2>/dev/null
    cp -f /tmp/ping_cli_3.log "${DEST}/ping_bg_${S}.log" 2>/dev/null

    cp -f /tmp/ryu_automacao.log "${DEST}/ryu_${S}.log" 2>/dev/null
    cp -f "${LOG_DIR}/cpu_baseline_${S}.csv" "${DEST}/cpu_baseline_${S}.csv" 2>/dev/null
    cp -f "${VERIF_DIR}/verificacao_baseline_${S}.txt" "${DEST}/verificacao_baseline_${S}.txt" 2>/dev/null
    cp -f /tmp/ryu_automacao.log "${LOG_DIR}/ryu_baseline_${S}.log" 2>/dev/null
    cp -f /tmp/cli_5103.log "${LOG_DIR}/iperf_bg_baseline_${S}.log" 2>/dev/null

    if ! verificar_arquivos_baseline "$bw" "$delay" "$loss" "$rep"; then
        echo "[ERRO_FATAL] Baseline incompleto: $S"
        echo "Veja: $LOGSRC"
        return 1
    fi

    echo "[OK_BASELINE] $S"
    return 0
}

salvar_qos() {
    local bw="$1"
    local delay="$2"
    local loss="$3"
    local rep="$4"

    local S=$(sufixo "$bw" "$delay" "$loss" "$rep")
    local DEST="${BASE}/rodadas_qos/ComQoSBanda${bw}MbpsLoss${loss}Delay${delay}"
    local LOGSRC="${LOG_DIR}/qos_sources_${S}.log"

    mkdir -p "$DEST"
    sudo chown -R wifi:wifi "$DEST" 2>/dev/null
    sudo chmod -R u+rwX "$DEST" 2>/dev/null

    {
        echo "SALVAR_QOS $S - $(date)"
        echo "DEST=$DEST"
        ls -lh "${BASE}/qos_dualnet.txt" 2>/dev/null || echo "AUSENTE qos_dualnet.txt"
        ls -lh /tmp/cli_5101.log 2>/dev/null || echo "AUSENTE cli_5101"
        ls -lh /tmp/cli_5102.log 2>/dev/null || echo "AUSENTE cli_5102"
        ls -lh /tmp/cli_5103.log 2>/dev/null || echo "AUSENTE cli_5103"
    } > "$LOGSRC"

    [ -f "${BASE}/qos_dualnet.txt" ] && mv -f "${BASE}/qos_dualnet.txt" "${DEST}/qos_${S}.txt"
    sudo rm -f "${BASE}/qos_dualnet.pcap"

    cp -f /tmp/cli_5101.log "${DEST}/iperf_voz_${S}.log" 2>/dev/null
    cp -f /tmp/cli_5102.log "${DEST}/iperf_video_${S}.log" 2>/dev/null
    cp -f /tmp/cli_5103.log "${DEST}/iperf_bg_${S}.log" 2>/dev/null

    cp -f /tmp/ping_cli_1.log "${DEST}/ping_voz_${S}.log" 2>/dev/null
    cp -f /tmp/ping_cli_2.log "${DEST}/ping_video_${S}.log" 2>/dev/null
    cp -f /tmp/ping_cli_3.log "${DEST}/ping_bg_${S}.log" 2>/dev/null

    cp -f /tmp/ryu_automacao.log "${DEST}/ryu_${S}.log" 2>/dev/null
    cp -f "${LOG_DIR}/cpu_qos_${S}.csv" "${DEST}/cpu_qos_${S}.csv" 2>/dev/null
    cp -f /tmp/ryu_flowstats.csv "${DEST}/ryu_flowstats_${S}.csv" 2>/dev/null
    cp -f /tmp/ryu_portstats.csv "${DEST}/ryu_portstats_${S}.csv" 2>/dev/null
    cp -f "${VERIF_DIR}/verificacao_qos_${S}.txt" "${DEST}/verificacao_qos_${S}.txt" 2>/dev/null
    cp -f /tmp/ryu_automacao.log "${LOG_DIR}/ryu_qos_${S}.log" 2>/dev/null
    cp -f /tmp/cli_5103.log "${LOG_DIR}/iperf_bg_qos_${S}.log" 2>/dev/null

    {
        echo ""
        echo "Arquivos .dat QoS esperados:"
        ls -lh "${DEST}/delay_voz_${S}.dat" 2>/dev/null || echo "AUSENTE delay_voz_${S}.dat"
        ls -lh "${DEST}/jitter_voz_${S}.dat" 2>/dev/null || echo "AUSENTE jitter_voz_${S}.dat"
        ls -lh "${DEST}/bitrate_voz_${S}.dat" 2>/dev/null || echo "AUSENTE bitrate_voz_${S}.dat"
        ls -lh "${DEST}/loss_voz_${S}.dat" 2>/dev/null || echo "AUSENTE loss_voz_${S}.dat"
        ls -lh "${DEST}/delay_video_${S}.dat" 2>/dev/null || echo "AUSENTE delay_video_${S}.dat"
        ls -lh "${DEST}/jitter_video_${S}.dat" 2>/dev/null || echo "AUSENTE jitter_video_${S}.dat"
        ls -lh "${DEST}/bitrate_video_${S}.dat" 2>/dev/null || echo "AUSENTE bitrate_video_${S}.dat"
        ls -lh "${DEST}/loss_video_${S}.dat" 2>/dev/null || echo "AUSENTE loss_video_${S}.dat"
    } >> "$LOGSRC"

    sudo chown -R wifi:wifi "$DEST" 2>/dev/null
    sudo chmod -R u+rwX "$DEST" 2>/dev/null

    if ! verificar_arquivos_qos "$bw" "$delay" "$loss" "$rep"; then
        echo "[ERRO_FATAL] QoS incompleto: $S"
        echo "Veja: $LOGSRC"
        return 1
    fi

    echo "[OK_QOS] $S"
    return 0
}

rodar_baseline() {
    local bw="$1"
    local delay="$2"
    local loss="$3"
    local rep="$4"
    local S=$(sufixo "$bw" "$delay" "$loss" "$rep")

    if verificar_arquivos_baseline "$bw" "$delay" "$loss" "$rep"; then
        echo "[SKIP_BASELINE] Ja completo: $S"
        return 0
    fi

    echo ""
    echo "================================================="
    echo " BASELINE | $S"
    echo "================================================="

    limpar_ambiente

    conda run -n ryu-env ryu-manager ryu.app.simple_switch_13 > /tmp/ryu_automacao.log 2>&1 &
    sleep 5
    export PCAP_DIR="$BASE"
    export VERIFY_LOG_FILE="${VERIF_DIR}/verificacao_baseline_${S}.txt"
    export VERIFY_SCENARIO="baseline_${S}"
    : > "$VERIFY_LOG_FILE"
    export CPU_LOG_FILE="${LOG_DIR}/cpu_baseline_${S}.csv"
    echo -e "${bw}\n${delay}\n${loss}\n3\n1\nudp\n2\nudp\n3\ntcp\n" | sudo -E python3 baseline.py \
        2>&1 | tee "${LOG_DIR}/baseline_exec_${S}.log"
    unset CPU_LOG_FILE
    unset PCAP_DIR
    unset VERIFY_LOG_FILE
    unset VERIFY_SCENARIO
    salvar_baseline "$bw" "$delay" "$loss" "$rep" || exit 1

    sudo pkill -f ryu-manager 2>/dev/null
    echo "[OK] Baseline $S finalizado."
    sleep 3
}

rodar_qos() {
    local bw="$1"
    local delay="$2"
    local loss="$3"
    local rep="$4"
    local S=$(sufixo "$bw" "$delay" "$loss" "$rep")

    if verificar_arquivos_qos "$bw" "$delay" "$loss" "$rep"; then
        echo "[SKIP_QOS] Ja completo: $S"
        return 0
    fi

    echo ""
    echo "================================================="
    echo " QoS | $S"
    echo "================================================="

    limpar_ambiente

    conda run -n ryu-env ryu-manager qos_switch.py > /tmp/ryu_automacao.log 2>&1 &
    sleep 5

    export REP="$rep"
    export PCAP_DIR="$BASE"
    export VERIFY_LOG_FILE="${VERIF_DIR}/verificacao_qos_${S}.txt"
    export VERIFY_SCENARIO="qos_${S}"
    : > "$VERIFY_LOG_FILE"
    export CPU_LOG_FILE="${LOG_DIR}/cpu_qos_${S}.csv"

    echo -e "${bw}\n${delay}\n${loss}\n3\n1\nudp\n2\nudp\n3\ntcp\n" | sudo -E python3 Qos.py \
        2>&1 | tee "${LOG_DIR}/qos_exec_${S}.log"
    unset CPU_LOG_FILE    
    unset PCAP_DIR
    unset VERIFY_LOG_FILE
    unset VERIFY_SCENARIO
    unset REP

    salvar_qos "$bw" "$delay" "$loss" "$rep" || exit 1

    sudo pkill -f ryu-manager 2>/dev/null
    echo "[OK] QoS $S finalizado."
    sleep 3
}

validar_scripts
precheck_recursos

echo "[2/8] Iniciando matriz definitiva."
echo "Repeticoes: ${REP_INICIO} ate ${REP_FIM}"
echo "Bandas: ${BWS[*]}"
echo "Delays: ${DELAYS[*]}"
echo "Losses: ${LOSSES[*]}"
echo ""

echo "================================================="
echo " FASE 1: BASELINE"
echo "================================================="

for rep in $(seq "$REP_INICIO" "$REP_FIM")
do
    for bw in "${BWS[@]}"
    do
        for delay in "${DELAYS[@]}"
        do
            for loss in "${LOSSES[@]}"
            do
                rodar_baseline "$bw" "$delay" "$loss" "$rep"
            done
        done
    done
done

echo "================================================="
echo " FASE 2: QoS"
echo "================================================="

for rep in $(seq "$REP_INICIO" "$REP_FIM")
do
    for bw in "${BWS[@]}"
    do
        for delay in "${DELAYS[@]}"
        do
            for loss in "${LOSSES[@]}"
            do
                rodar_qos "$bw" "$delay" "$loss" "$rep"
            done
        done
    done
done

echo "================================================="
echo " FASE 3: AUDITORIA POS-TESTE"
echo "================================================="

python3 auditoria_pos_teste_tcc.py \
    --base "$BASE" \
    --reps "$REP_FIM" \
    2>&1 | tee "${OUT_DIR}/auditoria_pos_teste_stdout.log"

if [ "$BASE" != "$OUT_DIR" ]; then
    cp -r "${BASE}/auditoria_pos_teste" "${OUT_DIR}/auditoria_pos_teste" 2>/dev/null
fi

echo "================================================="
echo " FASE 4: CLASSIFICACAO FINAL"
echo "================================================="

python3 classificar_auditoria_tcc.py \
    --base "$BASE" \
    2>&1 | tee "${OUT_DIR}/classificacao_final_stdout.log"

if [ "$BASE" != "$OUT_DIR" ]; then
    cp -f "${BASE}/classificacao_final_tcc.txt" "${OUT_DIR}/classificacao_final_tcc.txt" 2>/dev/null
fi

echo "================================================="
echo " FASE 5: ANALISE ESTATISTICA E PLOTS"
echo "================================================="

python3 analise_tcc_qos_baseline.py \
    --base "$BASE" \
    --out "${OUT_DIR}/analise_tcc" \
    2>&1 | tee "${OUT_DIR}/analise_tcc_stdout.log"

echo "================================================="
echo " RESUMO FINAL"
echo "================================================="

{
    echo "============================================================"
    echo "RESUMO FINAL - TESTE DEFINITIVO"
    echo "============================================================"
    echo "Data: $(date)"
    echo "Pasta geral: $OUT_DIR"
    echo ""

    echo "============================================================"
    echo "REPETICOES"
    echo "============================================================"
    echo "REP_INICIO=$REP_INICIO"
    echo "REP_FIM=$REP_FIM"
    echo ""

    echo "============================================================"
    echo "CONTAGEM DE ARQUIVOS"
    echo "============================================================"
    echo "Baseline arquivos:"
    find "${BASE}/rodadas_baseline" -type f | wc -l
    echo "QoS arquivos:"
    find "${BASE}/rodadas_qos" -type f | wc -l
    echo ""

    echo "TXT Baseline:"
    find "${BASE}/rodadas_baseline" -name "baseline_*_rep*.txt" | wc -l
    echo "TXT QoS:"
    find "${BASE}/rodadas_qos" -name "qos_*_rep*.txt" | wc -l
    echo ""

    echo "============================================================"
    echo "CLASSIFICACAO FINAL"
    echo "============================================================"
    if [ -f "${OUT_DIR}/classificacao_final_tcc.txt" ]; then
        cat "${OUT_DIR}/classificacao_final_tcc.txt"
    else
        echo "classificacao_final_tcc.txt nao encontrado."
    fi
    echo ""

    echo "============================================================"
    echo "AUDITORIA BRUTA - VEREDITO"
    echo "============================================================"
    R="${OUT_DIR}/auditoria_pos_teste/relatorios/relatorio_auditoria_pos_teste.txt"
    if [ -f "$R" ]; then
        grep -A20 "7) VEREDITO" "$R" || true
    else
        echo "Relatorio de auditoria nao encontrado."
    fi
    echo ""

    echo "============================================================"
    echo "PROBLEMAS DETECTADOS"
    echo "============================================================"
    P="${OUT_DIR}/auditoria_pos_teste/tabelas/problemas_detectados.csv"
    if [ -f "$P" ]; then
        cat "$P"
    else
        echo "problemas_detectados.csv nao encontrado."
    fi
    echo ""

    echo "============================================================"
    echo "ANALISE TCC"
    echo "============================================================"
    A="${OUT_DIR}/analise_tcc/relatorios/relatorio_analise_tcc.txt"
    if [ -f "$A" ]; then
        cat "$A"
    else
        echo "Relatorio de analise nao encontrado."
    fi
    echo ""

    echo "============================================================"
    echo "RECURSOS"
    echo "============================================================"
    df -h "$BASE_ROOT"
    free -h

} > "$RESUMO_FINAL"

echo ""
echo "================================================="
echo " TESTE DEFINITIVO FINALIZADO"
echo "================================================="
echo "Pasta geral:"
echo "$OUT_DIR"
echo ""
echo "Resumo:"
echo "$RESUMO_FINAL"
echo ""
echo "Para ver:"
echo "cat \"$RESUMO_FINAL\""
echo ""
echo "Para compactar:"
echo "tar -czf \"${OUT_DIR}.tar.gz\" -C \"$(dirname "$OUT_DIR")\" \"$(basename "$OUT_DIR")\""
