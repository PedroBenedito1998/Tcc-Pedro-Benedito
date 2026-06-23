#!/usr/bin/env python3
"""
Topologia Dumbbell Dinamica - Mininet-WiFi 2.6 + Ryu 4.34
Cenario BASELINE (Sem QoS)

ATUALIZACOES (Nivelamento com o script QoS):
  - D-ITG implementado para fluxos UDP (Voz e Video) com payload fixo em 1000B (MTU safe).
  - Iperf3 mantido para fluxos TCP (Background/Best-Effort).
  - Estrategia de Linha do Tempo:
      0s a 30s: Apenas Ping (Baseline do cabo)
     30s a 60s: Trafego Pesado D-ITG/Iperf3 (Tempestade SEM PING)
  - Decodificacao automatica do binario do D-ITG para arquivos .dat.
"""

import copy
import time
import subprocess
import os
import threading
from functools import partial

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch, Host
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI
from mininet.topo import Topo


# ── Constantes globais ────────────────────────────────────────────────
DURACAO_TOTAL   = 60
DURACAO_PING_SO = 30
DURACAO_TRAFEGO = 30
PORT_BASE = 5100
PCAP_DIR  = os.environ.get('PCAP_DIR', '/home/wifi/Documents/Benedito/dumbell')

# Interfaces fixadas para evitar conflito entre OVS QoS e NetEm:
# r1-eth0 <-> s2-eth1: lado dos servidores/gargalo de banda
# r1-eth1 <-> s1-eth1: lado dos clientes, onde delay/loss NetEm sao aplicados
R1_SRV_IFACE = 'r1-eth0'
R1_CLI_IFACE = 'r1-eth1'
S2_GARGALO_IFACE = 's2-eth1'

# Sub-redes
NET_CLI = '10.0.0'   # clientes:   10.0.0.1 .. 10.0.0.N
NET_SRV = '20.0.0'   # servidores: 20.0.0.1 .. 20.0.0.N
GW_CLI  = '10.0.0.254'   # interface do router voltada para S1
GW_SRV  = '20.0.0.254'   # interface do router voltada para S2


# ── Perfis de trafego ─────────────────────────────────────────────────
PERFIS = {
    1: {
        'nome':      'VoIP (AC-Voz)',
        'proto':     None,
        'banda':     None,
        'dscp_dec':  184,          # DSCP 46 (EF) << 2
        'dscp_nome': 'DSCP46/EF',
    },
    2: {
        'nome':      'Video',
        'proto':     None,
        'banda':     None,
        'dscp_dec':  136,          # DSCP 34 (AF41) << 2
        'dscp_nome': 'DSCP34/AF41',
    },
    3: {
        'nome':      'Background',
        'proto':     None,
        'banda':     None,
        'dscp_dec':  32,           # DSCP 8 (CS1) << 2
        'dscp_nome': 'DSCP8/CS1',
    },
    4: {
        'nome':      'Best-Effort',
        'proto':     None,
        'banda':     None,
        'dscp_dec':  0,
        'dscp_nome': 'DSCP0/BE',
    },
}


# ── Limpeza completa de arquivos temporarios ──────────────────────────
def limpar_arquivos(config_pares=None, pcap_tmp=None, pcap_final=None,
                    txt_final=None, verbose=True):
    """
    Remove logs iperf3, D-ITG, pcap temporario, pcap final e txt residuais.
    """
    if verbose:
        info('[LIMPEZA] Removendo arquivos residuais...\n')

    # Mata qualquer iperf3 e D-ITG pendente
    subprocess.run('pkill -9 iperf3', shell=True, capture_output=True)
    subprocess.run('pkill -9 ITGRecv', shell=True, capture_output=True)
    subprocess.run('pkill -9 ITGSend', shell=True, capture_output=True)

    if config_pares:
        for cp in config_pares:
            porta = cp['porta']
            for f in ['/tmp/cli_%d.log' % porta, '/tmp/srv_%d.log' % porta, '/tmp/cli_itg_%d.bin' % porta]:
                if os.path.exists(f):
                    os.remove(f)
    else:
        subprocess.run('rm -f /tmp/cli_*.log /tmp/srv_*.log /tmp/cli_itg_*.bin',
                       shell=True, capture_output=True)

    if pcap_tmp and os.path.exists(pcap_tmp):
        os.remove(pcap_tmp)

    if pcap_final and os.path.exists(pcap_final):
        os.remove(pcap_final)

    if txt_final and os.path.exists(txt_final):
        os.remove(txt_final)

    for f in ['/tmp/tshark.log', '/tmp/tshark.pid']:
        if os.path.exists(f):
            os.remove(f)

    if verbose:
        info('[LIMPEZA] Concluida.\n')


# ── Entrada interativa ────────────────────────────────────────────────
# ── Monitoramento de CPU durante a fase de trafego ────────────────────
def iniciar_monitor_cpu(tag):
    """
    Coleta uso geral da CPU do sistema durante a fase de trafego.
    O caminho do arquivo e definido pela variavel de ambiente CPU_LOG_FILE,
    passada pelo script bash definitivo.
    """
    cpu_log = os.environ.get("CPU_LOG_FILE")
    if not cpu_log:
        return None, None

    stop_event = threading.Event()

    def ler_cpu_total():
        with open("/proc/stat", "r") as stat_file:
            partes = stat_file.readline().split()

        valores = list(map(int, partes[1:]))
        idle = valores[3] + valores[4]
        total = sum(valores)
        return total, idle

    def loop():
        os.makedirs(os.path.dirname(cpu_log), exist_ok=True)

        with open(cpu_log, "w") as f:
            f.write("timestamp,tag,cpu_total_percent,cpu_idle_percent,cpu_busy_percent\n")
            total_ant, idle_ant = ler_cpu_total()
            while not stop_event.is_set():
                time.sleep(1)
                try:
                    total_atual, idle_atual = ler_cpu_total()
                    delta_total = total_atual - total_ant
                    delta_idle = idle_atual - idle_ant

                    if delta_total > 0:
                        idle_percent = (delta_idle / delta_total) * 100.0
                        busy_percent = 100.0 - idle_percent
                    else:
                        idle_percent = 0.0
                        busy_percent = 0.0

                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{ts},{tag},100.00,{idle_percent:.2f},{busy_percent:.2f}\n")
                    f.flush()

                    total_ant, idle_ant = total_atual, idle_atual
                except Exception as e:
                    try:
                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{tag},ERROR,0,0\n")
                        f.flush()
                    except Exception:
                        pass

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return stop_event, t


def parar_monitor_cpu(stop_event, thread_obj):
    if stop_event is not None:
        stop_event.set()
    if thread_obj is not None:
        thread_obj.join(timeout=2)
def coletar_configuracao():
    print('\n' + '='*60)
    print('  CONFIGURACAO DO EXPERIMENTO DUMBBELL DUAL-NET')
    print('  Clientes: 10.0.0.x/24  |  Servidores: 20.0.0.x/24')
    print('='*60)

    # A1 — Banda do gargalo
    print('\n[GARGALO] Banda em s2-eth1; delay/loss NetEm em r1-eth1:')
    while True:
        try:
            bw = int(input('  Banda do gargalo (Mbps) [1-1000]: '))
            if 1 <= bw <= 1000:
                break
            print('  [!] Digite um valor entre 1 e 1000.')
        except ValueError:
            print('  [!] Entrada invalida. Digite um numero inteiro.')

    # A2 — Atraso
    while True:
        try:
            delay_ms = int(input('  Atraso do gargalo (ms) [0-300]: '))
            if 0 <= delay_ms <= 300:
                break
            print('  [!] Digite um valor entre 0 e 300.')
        except ValueError:
            print('  [!] Entrada invalida. Digite um numero inteiro.')
    delay_str = '%dms' % delay_ms

    # A3 — Perda
    while True:
        try:
            loss = round(float(
                input('  Perda de pacotes (%) [0.0-5.0]: ').replace(',', '.')
            ), 1)
            if 0.0 <= loss <= 5.0:
                break
            print('  [!] Digite um valor entre 0.0 e 5.0.')
        except ValueError:
            print('  [!] Entrada invalida.')

    gargalo = {'bw': bw, 'delay': delay_str, 'loss': loss}
    print('\n  Gargalo: %d Mbps | %s | %.1f%% loss' % (bw, delay_str, loss))

    # B1 — Numero de pares
    while True:
        try:
            n = int(input('\nQuantos pares de hosts (1-10): '))
            if 1 <= n <= 10:
                break
            print('  [!] Digite entre 1 e 10.')
        except ValueError:
            print('  [!] Entrada invalida.')

    print('\n  Tipos disponiveis:')
    print('  1 - VoIP / AC-Voz  (DSCP 46 - EF)')
    print('  2 - Video          (DSCP 34 - AF41)')
    print('  3 - Background     (DSCP  8 - CS1)')
    print('  4 - Best-Effort    (DSCP  0 - BE)')

    # B2/B3 — tipo e protocolo de cada par
    pares = []
    for i in range(1, n + 1):
        print('\n  --- Par %d ---' % i)
        while True:
            try:
                tipo = int(input('  Tipo do Par %d (1-4): ' % i))
                if tipo in PERFIS:
                    break
                print('    [!] Digite 1, 2, 3 ou 4.')
            except ValueError:
                print('    [!] Entrada invalida.')

        while True:
            proto = input('  Protocolo do Par %d (udp/tcp): ' % i).strip().lower()
            if proto in ('udp', 'tcp'):
                break
            print('    [!] Digite "udp" ou "tcp".')

        perfil_par          = copy.deepcopy(PERFIS[tipo])
        perfil_par['proto'] = proto
        pares.append({'id': i, 'perfil': perfil_par})
        print('    -> Par %d: %-18s | %-3s | %s' % (
            i, perfil_par['nome'], proto.upper(), perfil_par['dscp_nome']))

    # Resumo
    print('\n' + '-'*60)
    print('  RESUMO')
    print('-'*60)
    print('  Banda s2-eth1: %d Mbps | NetEm r1-eth1: %s | %.1f%% loss' % (bw, delay_str, loss))
    print('  Clientes  : rede 10.0.0.0/24  (gateway 10.0.0.254)')
    print('  Servidores: rede 20.0.0.0/24  (gateway 20.0.0.254)')
    for p in pares:
        perf = p['perfil']
        print('  Par %2d | hC%d (10.0.0.%d) -> hS%d (20.0.0.%d) | %-18s | %-3s | %s' % (
            p['id'], p['id'], p['id'], p['id'], p['id'],
            perf['nome'], perf['proto'].upper(), perf['dscp_nome']))
    print('-'*60)
    print('\nIniciando...\n')

    return gargalo, pares


# ── Topologia ─────────────────────────────────────────────────────────
class DumbbellDualNet(Topo):
    """
    Topologia Dumbbell com duas sub-redes separadas por um roteador.
    Gargalo: banda no s2-eth1 e delay/loss NetEm no r1-eth1.
    """

    def __init__(self, pares, gargalo, **kwargs):
        self.pares   = pares
        self.gargalo = gargalo
        super().__init__(**kwargs)

    def build(self):
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')

        r1 = self.addHost('r1', ip=None)

        # Ordem proposital:
        #   r1-eth0 <-> s2-eth1: lado dos servidores, sem NetEm.
        #   r1-eth1 <-> s1-eth1: lado dos clientes, com NetEm aplicado depois.
        self.addLink(r1, s2)
        self.addLink(r1, s1)

        for p in self.pares:
            i  = p['id']
            ip = '%s.%d/24' % (NET_CLI, i)
            hc = self.addHost('hC%d' % i,
                              ip=ip,
                              defaultRoute='via %s' % GW_CLI)
            self.addLink(hc, s1)

        for p in self.pares:
            i  = p['id']
            ip = '%s.%d/24' % (NET_SRV, i)
            hs = self.addHost('hS%d' % i,
                              ip=ip,
                              defaultRoute='via %s' % GW_SRV)
            self.addLink(hs, s2)


# ── Comandos Geradores de Trafego (NOVO: D-ITG) ────────────────────────
def cmd_servidor(porta, proto):
    if proto == 'udp':
        return 'ITGRecv &'
    else:
        return 'iperf3 -s -p %d --logfile /tmp/srv_%d.log &' % (porta, porta)

def cmd_cliente(ip_srv, porta, perfil, duracao_s, bw_alvo_mbps):
    if perfil['proto'] == 'udp':
        # Comando D-ITG para UDP. Payload fixo em 1000 Bytes (-c 1000). 
        # A taxa varia em pacotes por segundo (-C pkt_rate).
        pkt_rate = int((bw_alvo_mbps * 1000000) / 8000) if bw_alvo_mbps > 0 else 100
        duracao_ms = duracao_s * 1000
        
        return 'ITGSend -a %s -T UDP -C %d -c 1000 -tos %d -t %d -l /tmp/cli_itg_%d.bin &' % (
            ip_srv, pkt_rate, perfil['dscp_dec'], duracao_ms, porta)
    else:
        # Reduzimos 5 segundos do Iperf3 TCP para compensar o atraso do Handshake
        return 'iperf3 -c %s -p %d -t %d -S %d -R --logfile /tmp/cli_%d.log &' % (
            ip_srv, porta, (duracao_s - 5), perfil['dscp_dec'], porta)


# ── Filtro tshark baseado nas portas ativas ───────────────────────────
def montar_filtro_tshark(config_pares):
    partes = []
    for cp in config_pares:
        p = cp['porta']
        partes.append(f'(tcp port {p} or udp port {p})')
    return ' or '.join(partes)


# ── Exportar pcap para TXT (mantendo o pcap) ─────────────────────────
def exportar_pcap_para_txt(pcap_path, txt_path, r1_host):
    info('\n[EXPORT] Exportando pcap para TXT...\n')

    cmd = (
        'tshark -Q -r %s '
        '-T fields '
        '-e frame.number '
        '-e frame.time_relative '
        '-e ip.src '
        '-e ip.dst '
        '-e ip.proto '
        '-e ip.dsfield.dscp '
        '-e tcp.dstport '
        '-e udp.dstport '
        '-e frame.len '
        '-E header=y '
        '-E separator=/t '
        '-E quote=d '
        '2>/dev/null'
    ) % pcap_path

    saida = r1_host.cmd(cmd)

    with open(txt_path, 'w') as f:
        f.write(saida)

    if os.path.exists(txt_path):
        tamanho = os.path.getsize(txt_path)
        if tamanho > 0:
            with open(txt_path, 'r') as f:
                linhas = sum(1 for _ in f)
            info('[EXPORT] TXT gerado: %s (%d linhas, %.1f KB)\n' % (txt_path, linhas, tamanho/1024.0))
            info('[EXPORT] pcap mantido em: %s\n' % pcap_path)
        else:
            info('[AVISO] TXT vazio. pcap mantido em: %s\n' % pcap_path)
    else:
        info('[AVISO] TXT nao foi criado. pcap mantido em: %s\n' % pcap_path)


def delay_ms(gargalo):
    return int(str(gargalo['delay']).replace('ms', ''))


def aplicar_netem_downlink(r1, gargalo):
    """
    Aplica atraso/perda apenas no lado cliente do roteador.
    Isso mantem o NetEm fora da interface s2-eth1, onde o QoS do OVS atua no
    cenario QoS.
    """
    d_ms = delay_ms(gargalo)
    loss = float(gargalo['loss'])

    r1.cmd('tc qdisc del dev %s root 2>/dev/null' % R1_SRV_IFACE)
    r1.cmd('tc qdisc del dev %s root 2>/dev/null' % R1_CLI_IFACE)

    if d_ms > 0 or loss > 0:
        partes = ['tc qdisc replace dev %s root netem' % R1_CLI_IFACE]
        if d_ms > 0:
            partes.append('delay %dms' % d_ms)
        if loss > 0:
            partes.append('loss %.1f%%' % loss)
        partes.append('limit 10000')
        r1.cmd(' '.join(partes))


def aplicar_limitador_baseline(s2, gargalo):
    """
    Baseline sem filas por classe: limita a banda total em uma unica classe HTB.
    Atraso/perda ficam separados no r1-eth1.
    """
    bw = int(gargalo['bw'])
    s2.cmd('ovs-vsctl --if-exists clear Port %s qos' % S2_GARGALO_IFACE)
    s2.cmd('ovs-vsctl --all destroy QoS -- --all destroy Queue 2>/dev/null')
    s2.cmd('tc qdisc del dev %s root 2>/dev/null' % S2_GARGALO_IFACE)
    s2.cmd('tc qdisc replace dev %s root handle 1: htb default 1' % S2_GARGALO_IFACE)
    s2.cmd(
        'tc class replace dev %s parent 1: classid 1:1 htb rate %dmbit ceil %dmbit'
        % (S2_GARGALO_IFACE, bw, bw)
    )


def salvar_verificacao(r1, s2, etapa):
    """
    Salva evidencias de qdisc/OVS quando VERIFY_LOG_FILE estiver definido.
    O bash definitivo e o smoke test usam isso para auditar cada execucao.
    """
    caminho = os.environ.get('VERIFY_LOG_FILE')
    if not caminho:
        return

    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    cenario = os.environ.get('VERIFY_SCENARIO', 'baseline')

    blocos = [
        ('tc qdisc show dev s2-eth1', s2.cmd('tc qdisc show dev %s 2>&1' % S2_GARGALO_IFACE)),
        ('tc qdisc show dev r1-eth0', r1.cmd('tc qdisc show dev %s 2>&1' % R1_SRV_IFACE)),
        ('tc qdisc show dev r1-eth1', r1.cmd('tc qdisc show dev %s 2>&1' % R1_CLI_IFACE)),
        ('ovs-vsctl list QoS', s2.cmd('ovs-vsctl list QoS 2>&1')),
        ('ovs-vsctl list Queue', s2.cmd('ovs-vsctl list Queue 2>&1')),
        ('ovs-ofctl -O OpenFlow13 dump-flows s2', s2.cmd('ovs-ofctl -O OpenFlow13 dump-flows s2 2>&1')),
    ]

    with open(caminho, 'a') as f:
        f.write('\n============================================================\n')
        f.write('cenario=%s etapa=%s timestamp=%s\n' % (
            cenario, etapa, time.strftime('%Y-%m-%d %H:%M:%S')
        ))
        f.write('============================================================\n')
        for titulo, saida in blocos:
            f.write('\n### %s\n' % titulo)
            f.write(saida if saida.endswith('\n') else saida + '\n')


# ── Funcao principal ──────────────────────────────────────────────────
def run():
    setLogLevel('info')

    info('\n[INIT] Limpeza inicial de arquivos residuais...\n')
    limpar_arquivos(verbose=True)

    gargalo, pares = coletar_configuracao()
    n = len(pares)

    OVS13 = partial(OVSSwitch, protocols='OpenFlow13', failMode='secure')

    topo = DumbbellDualNet(pares, gargalo)
    net  = Mininet(
        topo=topo,
        switch=OVS13,
        link=TCLink,
        controller=None,
        autoSetMacs=True,
        autoStaticArp=False
    )

    net.addController('c0', controller=RemoteController,
                      ip='127.0.0.1', port=6653)
    net.start()

    # ── Configurar o roteador R1 ──────────────────────────────────────
    info('\n[ROUTER] Configurando R1 (roteador entre 10.0.0.x e 20.0.0.x)...\n')
    r1 = net.get('r1')

    r1.cmd('ip addr flush dev %s' % R1_SRV_IFACE)
    r1.cmd('ip addr flush dev %s' % R1_CLI_IFACE)
    r1.cmd('ip addr add %s/24 dev %s' % (GW_SRV, R1_SRV_IFACE))
    r1.cmd('ip addr add %s/24 dev %s' % (GW_CLI, R1_CLI_IFACE))
    r1.cmd('ip link set %s up' % R1_SRV_IFACE)
    r1.cmd('ip link set %s up' % R1_CLI_IFACE)
    r1.cmd('sysctl -w net.ipv4.ip_forward=1')

    info('[ROUTER] R1 configurado:\n')
    info('         %s = %s/24 (gateway servidores S2, sem NetEm)\n' % (R1_SRV_IFACE, GW_SRV))
    info('         %s = %s/24 (gateway clientes S1, com NetEm downlink)\n' % (R1_CLI_IFACE, GW_CLI))
    info('         ip_forward = 1\n')

    # ── FASE 1: OpenFlow 1.3 nos switches ────────────────────────────
    info('\n[FASE 1] Configurando OpenFlow 1.3 nos switches...\n')
    for sw in net.switches:
        sw.cmd('ovs-vsctl set bridge %s protocols=OpenFlow13' % sw.name)
        sw.cmd('ovs-vsctl set-controller %s tcp:127.0.0.1:6653' % sw.name)

    info('[FASE 1] Aguardando conexao dos switches ao controlador Ryu...\n')
    conectado = False
    for tentativa in range(1, 9):
        time.sleep(3)
        ovs = subprocess.run(['ovs-vsctl', 'show'],
                             capture_output=True, text=True)
        if 'is_connected: true' in ovs.stdout:
            conectado = True
            info('[FASE 1] Controlador conectado! (tentativa %d)\n' % tentativa)
            break
        info('[FASE 1] Aguardando... (%ds)\n' % (tentativa * 3))

    if not conectado:
        info('[ERRO] Controlador nao respondeu em 24s.\n')
        net.stop()
        return

    s2 = net.get('s2')
    info('\n[QDISC] Aplicando banda baseline em %s e NetEm apenas em %s...\n' % (
        S2_GARGALO_IFACE, R1_CLI_IFACE
    ))
    aplicar_limitador_baseline(s2, gargalo)
    aplicar_netem_downlink(r1, gargalo)
    salvar_verificacao(r1, s2, 'apos_config_qdisc')

    # ── Teste de conectividade entre redes ───────────────────────────
    info('\n[FASE 1] Testando conectividade entre redes (hC1 -> hS1)...\n')
    hc1 = net.get('hC1')
    resultado = hc1.cmd('ping -c 3 -W 2 %s.1' % NET_SRV)
    if '0 received' in resultado or 'unreachable' in resultado:
        info('[ERRO] hC1 nao consegue pingar hS1. Verifique o roteador.\n')
        info(resultado + '\n')
        net.stop()
        return
    info('[FASE 1] OK - hC1 (10.0.0.1) alcanca hS1 (20.0.0.1) via R1.\n')

    if n > 1:
        resultado2 = hc1.cmd('ping -c 2 -W 2 %s.%d' % (NET_SRV, n))
        if '0 received' not in resultado2:
            info('[FASE 1] OK - hC1 alcanca hS%d (20.0.0.%d) via R1.\n' % (n, n))

    info('\n' + '='*60 + '\n')
    info('  Topologia Dumbbell Dual-Net - FASE 1 CONCLUIDA\n')
    info('  %d par(es) | Clientes: 10.0.0.x | Servidores: 20.0.0.x\n' % n)
    info('  Banda s2-eth1: %d Mbps | NetEm r1-eth1: %s | %.1f%% loss\n' % (
        gargalo['bw'], gargalo['delay'], gargalo['loss']))
    info('-'*60 + '\n')
    for p in pares:
        i    = p['id']
        perf = p['perfil']
        info('  Par %2d | hC%d (10.0.0.%d) -> hS%d (20.0.0.%d) | %-18s | %-3s | %s\n' % (
            i, i, i, i, i, perf['nome'], perf['proto'].upper(), perf['dscp_nome']))
    info('='*60 + '\n')

    # ── FASE 2: Cenario Baseline ──────────────────────────────────────
    info('\n[FASE 2] Cenario Baseline (Sem QoS) — Duas redes separadas\n')
    info('[FASE 2] Duracao: %ds | Banda s2-eth1: %d Mbps | NetEm r1-eth1: %s | %.1f%% loss\n\n' % (
        DURACAO_TOTAL, gargalo['bw'], gargalo['delay'], gargalo['loss']))

    config_pares = []
    for p in pares:
        i = p['id']
        config_pares.append({
            'id':     i,
            'perfil': p['perfil'],
            'porta':  PORT_BASE + i,
            'hc':     net.get('hC%d' % i),
            'hs':     net.get('hS%d' % i),
            'ip_s':   '%s.%d' % (NET_SRV, i),
            'ip_c':   '%s.%d' % (NET_CLI, i),
        })

    pcap_tmp   = '/tmp/baseline_gargalo.pcap'
    pcap_final = '%s/baseline_dualnet.pcap' % PCAP_DIR
    txt_final  = '%s/baseline_dualnet.txt'  % PCAP_DIR

    info('[2.1] Limpando logs e arquivos de execucoes anteriores...\n')
    limpar_arquivos(config_pares=config_pares, pcap_tmp=pcap_tmp, pcap_final=pcap_final, txt_final=txt_final, verbose=False)

    info('[2.1] Preparando as Pontas Receptoras...\n')
    for cp in config_pares:
        proto = cp['perfil']['proto']
        porta = cp['porta']
        if proto == 'udp':
            info('      hC%d (CLIENTE) aguardando UDP na porta %d...\n' % (cp['id'], porta))
            cp['hc'].cmd('ITGRecv -l /tmp/cli_itg_%d.bin &' % porta)
        else:
            info('      hS%d (SERVIDOR) aguardando TCP na porta %d...\n' % (cp['id'], porta))
            cp['hs'].cmd('iperf3 -s -p %d --logfile /tmp/srv_%d.log &' % (porta, porta))
    # Aguarda receptores/servidores estabilizarem antes de iniciar captura/trafego.
    time.sleep(3)

    os.makedirs(PCAP_DIR, exist_ok=True)
    filtro_tshark = montar_filtro_tshark(config_pares) 
    info('\n[2.2] Iniciando captura tshark em r1-eth1 (NetEm/downlink, snaplen=96)...\n')
    info('[2.2] (Filtro BPF desativado na captura para confiabilidade)\n')
    r1.cmd("tshark -i r1-eth1 -s 96 -w %s > /tmp/tshark.log 2>&1 & echo $! > /tmp/tshark.pid" % pcap_tmp)
    # Garante que a captura esteja ativa antes dos pings/trafego.
    time.sleep(3)

    info('\n[2.3] Calculando limites de banda por aplicacao...\n')
    num_voz = sum(1 for p in config_pares if p['perfil']['nome'] == 'VoIP (AC-Voz)')
    num_video = sum(1 for p in config_pares if p['perfil']['nome'] == 'Video')

    # Taxas UDP fixas por faixa de gargalo, conforme definidas para o experimento final.
    #
    # Gargalo 10 Mbps:
    #   Voz   = 1 Mbps
    #   Video = 5 Mbps
    #
    # Gargalo 100 Mbps e 1000 Mbps:
    #   Voz   = 10 Mbps
    #   Video = 50 Mbps
    #
    # O TCP Background nao recebe limite direto no iperf3; ele tenta ocupar a banda restante
    # conforme as condicoes do enlace e, no cenario QoS, conforme a fila de Background.
    if gargalo['bw'] == 10:
        voz_alvo_mbps = 1
        video_alvo_mbps = 5
    else:
        voz_alvo_mbps = 10
        video_alvo_mbps = 50

    bw_voz_por_par = max(1, int(voz_alvo_mbps / num_voz)) if num_voz > 0 else 0
    bw_video_por_par = max(1, int(video_alvo_mbps / num_video)) if num_video > 0 else 0

    info('[2.3] Taxas UDP configuradas: Voz=%d Mbps por par | Video=%d Mbps por par\n' % (
        bw_voz_por_par, bw_video_por_par
    ))

    # ════════════════ A NOVA LINHA DO TEMPO (60 Segundos) ════════════════
    info('\n[2.3A] Fase 1: PINGS INICIADOS (Rede Vazia) - 30 segundos\n')
    for cp in config_pares:
        perf = cp['perfil']
        tos_hex = hex(perf['dscp_dec']) 
        cmd_ping = 'ping -w %d -i 1 -Q %s %s > /tmp/ping_cli_%d.log &' % (DURACAO_PING_SO, tos_hex, cp['ip_s'], cp['id'])
        cp['hc'].cmd(cmd_ping)

    for restante in range(DURACAO_PING_SO, 0, -10):
        info('      %ds restantes (So Ping)...\n' % restante)
        time.sleep(10)

    info('\n[2.3B] Fase 2: TEMPESTADE DOWNLINK INICIADA (Servidor -> Cliente) - 30 segundos\n')

    cpu_stop, cpu_thread = iniciar_monitor_cpu("baseline_trafego")

    for cp in config_pares:
        perf = cp['perfil']
        porta = cp['porta']
        bw_alvo = 0
        if perf['nome'] == 'VoIP (AC-Voz)': bw_alvo = bw_voz_por_par
        elif perf['nome'] == 'Video': bw_alvo = bw_video_por_par
        
        if perf['proto'] == 'udp':
            pkt_rate = int((bw_alvo * 1000000) / 8000) if bw_alvo > 0 else 100
            duracao_ms = DURACAO_TRAFEGO * 1000

            dscp_real = perf['dscp_dec'] >> 2

            cp['hs'].cmd("iptables -t mangle -F OUTPUT")
            cp['hs'].cmd(
                "iptables -t mangle -A OUTPUT "
                "-p udp -d %s "
                "-j DSCP --set-dscp %d" % (cp['ip_c'], dscp_real)
            )

            info('      hS%d atirando UDP para hC%d | %-18s | %s\n' % (
                cp['id'], cp['id'], perf['nome'], perf['dscp_nome']
            ))

            cp['hs'].cmd(
                'ITGSend -a %s -T UDP -C %d -c 1000 -t %d &' %
                (cp['ip_c'], pkt_rate, duracao_ms)
            )
        else:
            dscp_real = perf['dscp_dec'] >> 2

            # Como o iperf3 usa -R, os dados TCP saem do servidor hS para o cliente hC.
            # Portanto a marcacao DSCP precisa ser forcada no servidor.
            cp['hs'].cmd("iptables -t mangle -F OUTPUT")
            cp['hs'].cmd(
                "iptables -t mangle -A OUTPUT "
                "-p tcp -d %s "
                "-j DSCP --set-dscp %d" % (cp['ip_c'], dscp_real)
            )

            # Ajuste de buffers TCP para cenarios de alto BDP:
            # exemplo extremo: 1000 Mbps com 300 ms de atraso.
            for h in (cp['hc'], cp['hs']):
                h.cmd("sysctl -w net.core.rmem_max=134217728 >/dev/null 2>&1")
                h.cmd("sysctl -w net.core.wmem_max=134217728 >/dev/null 2>&1")
                h.cmd("sysctl -w net.ipv4.tcp_rmem='4096 87380 134217728' >/dev/null 2>&1")
                h.cmd("sysctl -w net.ipv4.tcp_wmem='4096 65536 134217728' >/dev/null 2>&1")
                h.cmd("sysctl -w net.ipv4.tcp_congestion_control=cubic >/dev/null 2>&1")

            info('      hC%d puxando TCP de hS%d | %-18s | %s\n' % (
                cp['id'], cp['id'], perf['nome'], perf['dscp_nome']
            ))

            cp['hc'].cmd(
                'iperf3 -c %s -p %d -t %d -S %d -R --connect-timeout 30000 --logfile /tmp/cli_%d.log &' %
                (cp['ip_s'], porta, (DURACAO_TRAFEGO - 5), perf['dscp_dec'], porta)
            )

    for restante in range(DURACAO_TRAFEGO, 0, -10):
        info('      %ds restantes (Somente Trafego)...\n' % restante)
        time.sleep(10)

    # Espera extra para o iperf3/D-ITG fecharem arquivos em cenarios com alto delay/perda.
    # Isso reduz logs incompletos sem mudar a duracao efetiva do trafego principal.
    info('      Aguardando 5s para finalizacao dos logs...\n')
    time.sleep(5)
    parar_monitor_cpu(cpu_stop, cpu_thread)

    info('\n[FASE 2] Experimento concluido. Encerrando processos...\n')

    # Para processos
    tshark_pid = r1.cmd('cat /tmp/tshark.pid 2>/dev/null').strip()
    if tshark_pid:
        r1.cmd('kill %s 2>/dev/null' % tshark_pid)
    else:
        r1.cmd('pkill -f tshark 2>/dev/null')
    
    # Mata D-ITG
    subprocess.run('pkill -9 ITGRecv', shell=True, capture_output=True)
    time.sleep(2)

    # Decodifica o arquivo binario do D-ITG (Multiplas Extracoes)
    for cp in config_pares:
        if cp['perfil']['proto'] == 'udp':
            p = cp['porta']
            # 1. Sumario Global para Auditoria
            cp['hc'].cmd(f"ITGDec /tmp/cli_itg_{p}.bin > /tmp/cli_{p}.log 2>/dev/null")
            
            # 2. Extracao temporal correta (a cada 1000ms)
            cmd_temporal = (
                f"ITGDec /tmp/cli_itg_{p}.bin "
                f"-d 1000 /tmp/delay_{p}.dat "
                f"-j 1000 /tmp/jitter_{p}.dat "
                f"-b 1000 /tmp/bitrate_{p}.dat "
                f"-p 1000 /tmp/loss_{p}.dat"
            )
            cp['hc'].cmd(f"{cmd_temporal} > /tmp/cli_{p}_temporal.log 2>/tmp/itgdec_baseline_{p}.err")

    r1.cmd('cp %s %s 2>/dev/null' % (pcap_tmp, pcap_final))
    os.system('chmod 644 %s 2>/dev/null' % pcap_final)

    tamanho_pcap = r1.cmd('stat -c %%s %s 2>/dev/null' % pcap_final).strip()
    if tamanho_pcap and tamanho_pcap != '0':
        info('[2.2] pcap final gerado com %s bytes.\n' % tamanho_pcap)
    else:
        info('[AVISO] pcap final vazio ou nao gerado.\n')

    exportar_pcap_para_txt(pcap_final, txt_final, r1)
    salvar_verificacao(r1, s2, 'apos_trafego')

    if os.path.exists(pcap_tmp):
        os.remove(pcap_tmp)
        info('[LIMPEZA] pcap temporario /tmp removido.\n')

    # ── Resultados (Preservando as instrucoes originais) ─────────────
    info('\n' + '='*60 + '\n')
    info('  RESULTADOS DO CENARIO BASELINE DUAL-NET\n')
    info('  Banda s2-eth1: %d Mbps | NetEm r1-eth1: %s | %.1f%% loss\n' % (
        gargalo['bw'], gargalo['delay'], gargalo['loss']))
    info('='*60 + '\n')
    for cp in config_pares:
        result = cp['hc'].cmd('cat /tmp/cli_%d.log 2>/dev/null' % cp['porta'])
        if result.strip() and cp['perfil']['proto'] == 'udp':
            info('\n[Par %d - %s | %s] hS%d -> hC%d (DOWNLINK D-ITG Fim-a-Fim):\n%s\n' % (
                cp['id'], cp['perfil']['nome'], cp['perfil']['proto'].upper(),
                cp['id'], cp['id'], '\n'.join(result.splitlines()[-15:])))
        elif result.strip():
            info('\n[Par %d - %s | %s] hS%d -> hC%d (DOWNLINK Iperf3):\n%s\n' % (
                cp['id'], cp['perfil']['nome'], cp['perfil']['proto'].upper(),
                cp['id'], cp['id'], result))

    info('\n[OK] Dados salvos em:\n')
    info('      TXT : %s\n' % txt_final)
    info('      PCAP: %s\n' % pcap_final)
    info('[OK] Para analisar no Python/pandas:\n')
    info('     import pandas as pd\n')
    info('     df = pd.read_csv("%s", sep="\\t")\n' % txt_final)
    info('\n     Filtros uteis no TXT:\n')
    for cp in config_pares:
        info('       Par %d (%-18s | %-3s): porta %d\n' % (
            cp['id'], cp['perfil']['nome'],
            cp['perfil']['proto'].upper(),
            cp['porta']))
    info('='*60 + '\n')

    info('\n[LIMPEZA FINAL] Removendo logs iperf3 de /tmp...\n')
    info('[LIMPEZA FINAL] Concluida.\n')

    info('\nCLI disponivel para inspecao. Digite "exit" para encerrar.\n')
    net.stop()
    info('Rede encerrada.\n')


if __name__ == '__main__':
    run()
