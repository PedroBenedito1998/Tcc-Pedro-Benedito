#!/bin/bash
set -euo pipefail

RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
BASE="${SMOKE_BASE:-$HOME/Documents/tcc/smoke_test_final_minrate_${RUN_ID}}"
TCC_DIR="$HOME/Documents/tcc"
LOG_DIR="$BASE/logs"
VERIF="$BASE/verificacao.txt"
S="bw10_del300_loss3.0_rep1"

mkdir -p "$BASE" "$LOG_DIR"
: > "$VERIF"

cd "$TCC_DIR"

limpar() {
    sudo mn -c >/dev/null 2>&1 || true
    sudo pkill -f ryu-manager >/dev/null 2>&1 || true
    sudo pkill -9 iperf3 >/dev/null 2>&1 || true
    sudo pkill -9 ITGRecv >/dev/null 2>&1 || true
    sudo pkill -9 ITGSend >/dev/null 2>&1 || true
    sudo pkill -f tshark >/dev/null 2>&1 || true
    sudo tc qdisc del dev s2-eth1 root >/dev/null 2>&1 || true
    sudo tc qdisc del dev r1-eth0 root >/dev/null 2>&1 || true
    sudo tc qdisc del dev r1-eth1 root >/dev/null 2>&1 || true
    sudo ovs-vsctl --if-exists clear Port s2-eth1 qos >/dev/null 2>&1 || true
    sudo ovs-vsctl --all destroy QoS -- --all destroy Queue >/dev/null 2>&1 || true
    sudo rm -f /tmp/cli_*.log /tmp/srv_*.log /tmp/cli_itg_*.bin
    sudo rm -f /tmp/delay_*.dat /tmp/jitter_*.dat /tmp/bitrate_*.dat /tmp/loss_*.dat
    sudo rm -f /tmp/ping_cli_*.log /tmp/tshark.log /tmp/tshark.pid
    sudo rm -f /tmp/qos_gargalo.pcap /tmp/baseline_gargalo.pcap
    sudo rm -f /tmp/ryu_automacao.log /tmp/ryu_flowstats.csv /tmp/ryu_portstats.csv
    sleep 2
}

copiar_baseline() {
    local dest="$BASE/baseline"
    mkdir -p "$dest"
    cp -f "$BASE/baseline_dualnet.txt" "$dest/baseline_${S}.txt" 2>/dev/null || true
    cp -f /tmp/cli_5101.log "$dest/ditg_voz_${S}.log" 2>/dev/null || true
    cp -f /tmp/cli_5102.log "$dest/ditg_video_${S}.log" 2>/dev/null || true
    cp -f /tmp/cli_5103.log "$dest/iperf_bg_${S}.log" 2>/dev/null || true
    cp -f /tmp/delay_5101.dat "$dest/delay_voz_${S}.dat" 2>/dev/null || true
    cp -f /tmp/jitter_5101.dat "$dest/jitter_voz_${S}.dat" 2>/dev/null || true
    cp -f /tmp/bitrate_5101.dat "$dest/bitrate_voz_${S}.dat" 2>/dev/null || true
    cp -f /tmp/loss_5101.dat "$dest/loss_voz_${S}.dat" 2>/dev/null || true
    cp -f /tmp/delay_5102.dat "$dest/delay_video_${S}.dat" 2>/dev/null || true
    cp -f /tmp/jitter_5102.dat "$dest/jitter_video_${S}.dat" 2>/dev/null || true
    cp -f /tmp/bitrate_5102.dat "$dest/bitrate_video_${S}.dat" 2>/dev/null || true
    cp -f /tmp/loss_5102.dat "$dest/loss_video_${S}.dat" 2>/dev/null || true
    cp -f /tmp/ping_cli_1.log "$dest/ping_voz_${S}.log" 2>/dev/null || true
    cp -f /tmp/ping_cli_2.log "$dest/ping_video_${S}.log" 2>/dev/null || true
    cp -f /tmp/ping_cli_3.log "$dest/ping_bg_${S}.log" 2>/dev/null || true
}

resumir_smoke() {
    python3 - "$BASE" "$S" > "$BASE/resultado_smoke.txt" <<'PY'
import sys
from pathlib import Path

base = Path(sys.argv[1])
s = sys.argv[2]

paths = {
    "baseline_delay_voz": base / "baseline" / f"delay_voz_{s}.dat",
    "baseline_delay_video": base / "baseline" / f"delay_video_{s}.dat",
    "baseline_jitter_voz": base / "baseline" / f"jitter_voz_{s}.dat",
    "baseline_jitter_video": base / "baseline" / f"jitter_video_{s}.dat",
    "baseline_loss_voz": base / "baseline" / f"loss_voz_{s}.dat",
    "baseline_loss_video": base / "baseline" / f"loss_video_{s}.dat",
    "qos_delay_voz": base / "rodadas_qos" / "ComQoSBanda10MbpsLoss3.0Delay300" / f"delay_voz_{s}.dat",
    "qos_delay_video": base / "rodadas_qos" / "ComQoSBanda10MbpsLoss3.0Delay300" / f"delay_video_{s}.dat",
    "qos_jitter_voz": base / "rodadas_qos" / "ComQoSBanda10MbpsLoss3.0Delay300" / f"jitter_voz_{s}.dat",
    "qos_jitter_video": base / "rodadas_qos" / "ComQoSBanda10MbpsLoss3.0Delay300" / f"jitter_video_{s}.dat",
    "qos_loss_voz": base / "rodadas_qos" / "ComQoSBanda10MbpsLoss3.0Delay300" / f"loss_voz_{s}.dat",
    "qos_loss_video": base / "rodadas_qos" / "ComQoSBanda10MbpsLoss3.0Delay300" / f"loss_video_{s}.dat",
}

def values(path):
    vals = []
    if not path.exists():
        return vals
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                vals.append(float(parts[1]))
            except ValueError:
                pass
    return vals

print("SMOKE TEST CORRECAO NETEM/QOS")
print("combo=bw10_delay300_loss3.0_rep1")
print("")

ok = True
for name, path in paths.items():
    vals = values(path)
    if not vals:
        print(f"{name}: AUSENTE/SEM_AMOSTRAS path={path}")
        ok = False
        continue
    media = sum(vals) / len(vals)
    print(f"{name}: n={len(vals)} media={media:.6f} min={min(vals):.6f} max={max(vals):.6f}")

baseline_delay = values(paths["baseline_delay_voz"]) + values(paths["baseline_delay_video"])
qos_delay = values(paths["qos_delay_voz"]) + values(paths["qos_delay_video"])

if baseline_delay and qos_delay:
    b = sum(baseline_delay) / len(baseline_delay)
    q = sum(qos_delay) / len(qos_delay)
    print("")
    print(f"media_delay_baseline_voz_video={b:.6f}s")
    print(f"media_delay_qos_voz_video={q:.6f}s")
    print(f"criterio_delay_baseline_250a350ms={0.250 <= b <= 0.350}")
    print(f"criterio_delay_qos_250a350ms={0.250 <= q <= 0.350}")
    ok = ok and (0.250 <= b <= 0.350) and (0.250 <= q <= 0.350)

print("")
print("SMOKE_PASSOU=" + ("SIM" if ok else "NAO"))
PY
}

echo "============================================================"
echo "SMOKE BASELINE"
echo "============================================================"
limpar
conda run -n ryu-env ryu-manager ryu.app.simple_switch_13 > /tmp/ryu_automacao.log 2>&1 &
sleep 5
export PCAP_DIR="$BASE"
export VERIFY_LOG_FILE="$VERIF"
export VERIFY_SCENARIO="smoke_baseline_${S}"
export CPU_LOG_FILE="$LOG_DIR/cpu_baseline_${S}.csv"
printf "10\n300\n3.0\n3\n1\nudp\n2\nudp\n3\ntcp\n" | sudo -E python3 baseline.py 2>&1 | tee "$LOG_DIR/baseline_${S}.log"
unset CPU_LOG_FILE VERIFY_LOG_FILE VERIFY_SCENARIO PCAP_DIR
copiar_baseline
sudo pkill -f ryu-manager >/dev/null 2>&1 || true

echo "============================================================"
echo "SMOKE QoS"
echo "============================================================"
limpar
conda run -n ryu-env ryu-manager qos_switch.py > /tmp/ryu_automacao.log 2>&1 &
sleep 5
export REP=1
export PCAP_DIR="$BASE"
export VERIFY_LOG_FILE="$VERIF"
export VERIFY_SCENARIO="smoke_qos_${S}"
export CPU_LOG_FILE="$LOG_DIR/cpu_qos_${S}.csv"
printf "10\n300\n3.0\n3\n1\nudp\n2\nudp\n3\ntcp\n" | sudo -E python3 Qos.py 2>&1 | tee "$LOG_DIR/qos_${S}.log"
unset CPU_LOG_FILE VERIFY_LOG_FILE VERIFY_SCENARIO PCAP_DIR REP
cp -f /tmp/ryu_flowstats.csv "$BASE/ryu_flowstats_${S}.csv" 2>/dev/null || true
cp -f /tmp/ryu_portstats.csv "$BASE/ryu_portstats_${S}.csv" 2>/dev/null || true
sudo pkill -f ryu-manager >/dev/null 2>&1 || true

resumir_smoke
echo "============================================================"
echo "RESULTADO"
echo "============================================================"
cat "$BASE/resultado_smoke.txt"
echo ""
echo "Verificacao:"
echo "$VERIF"
