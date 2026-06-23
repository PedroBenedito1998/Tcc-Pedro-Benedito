#!/usr/bin/env python3
"""
Topologia Dumbbell Dinamica – Mininet + Ryu QoS (v13 - D-ITG + Linha do Tempo)
"""

import copy
import time
import subprocess
import os
import glob
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

# Interfaces fixadas para separar QoS do OVS e NetEm:
# r1-eth0 <-> s2-eth1: lado dos servidores/gargalo de banda e filas OVS
# r1-eth1 <-> s1-eth1: lado dos clientes, onde delay/loss NetEm sao aplicados
R1_SRV_IFACE = 'r1-eth0'
R1_CLI_IFACE = 'r1-eth1'
S2_GARGALO_IFACE = 's2-eth1'

NET_CLI = '10.0.0'
NET_SRV = '20.0.0'
GW_CLI  = '10.0.0.254'
GW_SRV  = '20.0.0.254'


# ── Perfis de tráfego (com queue_id WMM) ─────────────────────────────
PERFIS = {
    1: {'nome': 'VoIP (AC-Voz)', 'proto': 'udp', 'dscp_dec': 184, 'dscp_nome': 'DSCP46/EF', 'queue_id': 0},
    2: {'nome': 'Video',         'proto': 'udp', 'dscp_dec': 136, 'dscp_nome': 'DSCP34/AF41', 'queue_id': 1},
    3: {'nome': 'Background',    'proto': 'tcp', 'dscp_dec': 32,  'dscp_nome': 'DSCP8/CS1',  'queue_id': 3},
    4: {'nome': 'Best-Effort',   'proto': 'tcp', 'dscp_dec': 0,   'dscp_nome': 'DSCP0/BE',   'queue_id': 2},
}


# ── Limpeza de arquivos temporários ───────────────────────────────────
def limpar_arquivos(config_pares=None, pcap_tmp=None, pcap_final=None,
                    txt_final=None, verbose=True):
    if verbose:
        info('[LIMPEZA] Removendo arquivos residuais e processos antigos...\n')

    subprocess.run('pkill -9 iperf3', shell=True, capture_output=True)
    subprocess.run('pkill -9 ITGRecv', shell=True, capture_output=True)
    subprocess.run('pkill -9 ITGSend', shell=True, capture_output=True)

    if config_pares:
        for cp in config_pares:
            porta = cp['porta']
            subprocess.run(f'rm -f /tmp/cli_{porta}* /tmp/srv_{porta}* /tmp/cli_itg_{porta}*', shell=True)
    else:
        subprocess.run('rm -f /tmp/cli_*.log /tmp/srv_*.log /tmp/cli_itg_*.bin /tmp/cli_*_temporal.log /tmp/*.dat',
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

    print('\n[GARGALO] Banda/filas em s2-eth1; delay/loss NetEm em r1-eth1:')
    while True:
        try:
            bw = int(input('  Banda do gargalo (Mbps) [1-1000]: '))
            if 1 <= bw <= 1000:
                break
            print('  [!] Digite um valor entre 1 e 1000.')
        except ValueError:
            print('  [!] Entrada invalida. Digite um numero inteiro.')

    while True:
        try:
            delay_ms = int(input('  Atraso do gargalo (ms) [0-300]: '))
            if 0 <= delay_ms <= 300:
                break
            print('  [!] Digite um valor entre 0 e 300.')
        except ValueError:
            print('  [!] Entrada invalida. Digite um numero inteiro.')
    delay_str = '%dms' % delay_ms

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

    while True:
        try:
            n = int(input('\nQuantos pares de hosts (1-10): '))
            if 1 <= n <= 10:
                break
            print('  [!] Digite entre 1 e 10.')
        except ValueError:
            print('  [!] Entrada invalida.')

    print('\n  Tipos disponiveis:')
    print('  1 - VoIP / AC-Voz   (DSCP 46 - EF    | Queue 0 - prioridade MAXIMA)')
    print('  2 - Video           (DSCP 34 - AF41   | Queue 1 - prioridade ALTA)')
    print('  3 - Background      (DSCP  8 - CS1   | Queue 3 - prioridade MINIMA)')
    print('  4 - Best-Effort     (DSCP  0 - BE     | Queue 2 - prioridade NORMAL)')

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
        print('    -> Par %d: %-18s | %-3s | %s | Queue %d' % (
            i, perfil_par['nome'], proto.upper(),
            perfil_par['dscp_nome'], perfil_par['queue_id']))

    print('\n' + '-'*60)
    print('  RESUMO')
    print('-'*60)
    print('  Banda/filas s2-eth1: %d Mbps | NetEm r1-eth1: %s | %.1f%% loss' % (bw, delay_str, loss))
    print('  Clientes  : rede 10.0.0.0/24  (gateway 10.0.0.254)')
    print('  Servidores: rede 20.0.0.0/24  (gateway 20.0.0.254)')
    for p in pares:
        perf = p['perfil']
        print('  Par %2d | hC%d (10.0.0.%d) <- hS%d (20.0.0.%d) | %-18s | %-3s | %s | Q%d' % (
            p['id'], p['id'], p['id'], p['id'], p['id'],
            perf['nome'], perf['proto'].upper(), perf['dscp_nome'],
            perf['queue_id']))
    print('-'*60)
    print('\nIniciando...\n')

    return gargalo, pares


# ── Topologia ─────────────────────────────────────────────────────────
class DumbbellDualNet(Topo):
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


# ── Exportar pcap para TXT ────────────────────────────────────────────
def exportar_pcap_para_txt(pcap_path, txt_path, r1_host):
    info('\n[EXPORT] Exportando pcap para TXT...\n')
    cmd = (
        'tshark -Q -r %s -T fields -e frame.number -e frame.time_relative '
        '-e ip.src -e ip.dst -e ip.proto -e ip.dsfield.dscp '
        '-e tcp.dstport -e udp.dstport -e frame.len -E header=y '
        '-E separator=/t -E quote=d 2>/dev/null'
    ) % pcap_path
    r1_host.cmd(f'{cmd} > {txt_path}')


def delay_ms(gargalo):
    return int(str(gargalo['delay']).replace('ms', ''))


def aplicar_netem_downlink(r1, gargalo):
    """
    Aplica atraso/perda apenas no lado cliente do roteador.
    O NetEm fica separado da interface s2-eth1, onde o OVS QoS cria as filas.
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


def salvar_verificacao(r1, s2, etapa):
    """
    Salva evidencias de qdisc/OVS quando VERIFY_LOG_FILE estiver definido.
    O dump de fluxos no fim da execucao mostra tambem as regras com set_queue.
    """
    caminho = os.environ.get('VERIFY_LOG_FILE')
    if not caminho:
        return

    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    cenario = os.environ.get('VERIFY_SCENARIO', 'qos')

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


# ════════════════ BLOCO DE QoS ═══════════════════════════════
def descobrir_porta_gargalo(s2):
    portas = s2.cmd("ovs-vsctl list-ports s2").strip().splitlines()
    for p in portas:
        info_intf = s2.cmd("ovs-vsctl get interface %s external_ids" % p)
        if 'r1' in info_intf:
            return p
    return 's2-eth1'

def descobrir_ofport(s2, intf_name):
    ofport = s2.cmd("ovs-vsctl get interface %s ofport" % intf_name).strip()
    return ofport if ofport != '-1' else '1'

def criar_filas_qos(s2, bw_mbps, config_pares):
    intf_gargalo = descobrir_porta_gargalo(s2)
    ofport_gargalo = descobrir_ofport(s2, intf_gargalo)
    
    max_rate = str(bw_mbps * 1_000_000)
    info('\n[QoS] Criando 4 filas HTB na interface %s (OF Port %s)...\n' % (intf_gargalo, ofport_gargalo))

    s2.cmd('ovs-vsctl -- clear Port %s qos' % intf_gargalo)
    s2.cmd('ovs-vsctl --all destroy QoS -- --all destroy Queue 2>/dev/null')
    s2.cmd('tc qdisc del dev %s root 2>/dev/null' % intf_gargalo)

    # Garantias minimas alinhadas as taxas UDP geradas no experimento:
    #   10 Mbps:   voz=1 Mbps,  video=5 Mbps
    #   100 Mbps:  voz=10 Mbps, video=50 Mbps
    #   1000 Mbps: voz=10 Mbps, video=50 Mbps
    # Best-Effort permanece com reserva simbolica para trafego auxiliar/nao
    # classificado. O restante fica com Background, sem limitar o uso de banda
    # ociosa, pois todas as filas usam max-rate igual a banda total.
    total_rate = int(bw_mbps * 1_000_000)
    be_min_rate = min(100_000, max(1_000, total_rate // 100))

    voz_target = 1_000_000 if bw_mbps == 10 else 10_000_000
    video_target = 5_000_000 if bw_mbps == 10 else 50_000_000

    max_prioritarios = max(total_rate - be_min_rate, 1)
    if voz_target + video_target > max_prioritarios:
        escala = float(max_prioritarios) / float(voz_target + video_target)
        min_q0 = max(1_000, int(voz_target * escala))
        min_q1 = max(1_000, int(video_target * escala))
    else:
        min_q0 = voz_target
        min_q1 = video_target

    min_q2 = be_min_rate
    min_q3 = max(1_000, total_rate - min_q0 - min_q1 - min_q2)

    cmd = (
        'ovs-vsctl -- set Port %s qos=@newqos -- '
        '--id=@newqos create QoS type=linux-htb other-config:max-rate=%s '
        'queues=0=@q0,1=@q1,2=@q2,3=@q3 -- '
        '--id=@q0 create Queue other-config:min-rate=%d other-config:max-rate=%s -- '
        '--id=@q1 create Queue other-config:min-rate=%d other-config:max-rate=%s -- '
        '--id=@q2 create Queue other-config:min-rate=%d other-config:max-rate=%s -- '
        '--id=@q3 create Queue other-config:min-rate=%d other-config:max-rate=%s'
        % (intf_gargalo, max_rate, min_q0, max_rate, min_q1, max_rate, min_q2, max_rate, min_q3, max_rate)
    )
    s2.cmd(cmd)
    info('[QoS] Filas HTB criadas no gargalo.\n')
    info('[QoS] Min-rate: Voz=%.3f Mbps | Video=%.3f Mbps | BE=%.3f Mbps | Background=%.3f Mbps\n' % (
        min_q0 / 1e6, min_q1 / 1e6, min_q2 / 1e6, min_q3 / 1e6
    ))
    info('      As regras de fluxo serao injetadas dinamicamente pelo RYU via DSCP!\n')
    time.sleep(2)


def run():
    setLogLevel('info')

    info('\n[INIT] Preparando ambiente...\n')
    limpar_arquivos(verbose=True)

    gargalo, pares = coletar_configuracao()
    topo = DumbbellDualNet(pares, gargalo)
    net  = Mininet(topo=topo, switch=partial(OVSSwitch, protocols='OpenFlow13', failMode='secure'), link=TCLink, controller=None)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    net.start()

    info('\n[ROUTER] Configurando R1 (roteador entre 10.0.0.x e 20.0.0.x)...\n')
    r1 = net.get('r1')
    r1.cmd('ip addr flush dev %s' % R1_SRV_IFACE)
    r1.cmd('ip addr flush dev %s' % R1_CLI_IFACE)
    r1.cmd(f'ip addr add {GW_SRV}/24 dev {R1_SRV_IFACE}')
    r1.cmd(f'ip addr add {GW_CLI}/24 dev {R1_CLI_IFACE}')
    r1.cmd('ip link set %s up' % R1_SRV_IFACE)
    r1.cmd('ip link set %s up' % R1_CLI_IFACE)
    r1.cmd('sysctl -w net.ipv4.ip_forward=1')

    info('\n[FASE 1] Configurando OpenFlow 1.3 nos switches...\n')
    for sw in net.switches:
        sw.cmd(f'ovs-vsctl set bridge {sw.name} protocols=OpenFlow13')
        sw.cmd(f'ovs-vsctl set-controller {sw.name} tcp:127.0.0.1:6653')

    info('[FASE 1] Aguardando conexao dos switches ao controlador Ryu...\n')
    conectado = False
    for tentativa in range(1, 9):
        time.sleep(3)
        ovs = subprocess.run(['ovs-vsctl', 'show'], capture_output=True, text=True)
        if 'is_connected: true' in ovs.stdout:
            conectado = True
            info('[FASE 1] Controlador conectado! (tentativa %d)\n' % tentativa)
            break
        info('[FASE 1] Aguardando... (%ds)\n' % (tentativa * 3))

    if not conectado:
        info('[ERRO] Controlador nao respondeu em 24s.\n')
        net.stop()
        return

    config_pares = []
    for p in pares:
        i = p['id']
        config_pares.append({
            'id': i, 'perfil': p['perfil'], 'porta': PORT_BASE + i,
            'hc': net.get(f'hC{i}'), 'hs': net.get(f'hS{i}'),
            'ip_s': f'{NET_SRV}.{i}', 'ip_c': f'{NET_CLI}.{i}',
        })

    s1 = net.get('s1')
    s2 = net.get('s2')

    info('\n[QDISC] Aplicando NetEm apenas em %s; %s permanece sem NetEm.\n' % (
        R1_CLI_IFACE, R1_SRV_IFACE
    ))
    aplicar_netem_downlink(r1, gargalo)
    
    info('\n[QoS] Habilitando passagem de pacotes ARP...\n')
    s1.cmd("ovs-ofctl -O OpenFlow13 add-flow unix:/var/run/openvswitch/s1.mgmt 'priority=150,arp,actions=NORMAL'")
    s2.cmd("ovs-ofctl -O OpenFlow13 add-flow unix:/var/run/openvswitch/s2.mgmt 'priority=150,arp,actions=NORMAL'")

    criar_filas_qos(s2, gargalo['bw'], config_pares)
    salvar_verificacao(r1, s2, 'apos_config_qdisc_qos')
    
    time.sleep(3) 

    info('\n[FASE 1] Testando conectividade entre redes (hC1 -> hS1)...\n')
    hc1 = net.get('hC1')
    resultado = hc1.cmd('ping -c 3 -W 2 %s.1' % NET_SRV)
    if '0 received' in resultado or 'unreachable' in resultado:
        info('[ERRO] hC1 nao consegue pingar hS1. Verifique o roteador.\n')
        net.stop()
        return
    info('[FASE 1] OK - hC1 (10.0.0.1) alcanca hS1 (20.0.0.1) via R1.\n')

    info('\n[FASE 2] Cenario com QoS WMM — Downlink (Srv -> Cli)\n')
    pcap_tmp, pcap_final, txt_final = '/tmp/qos_gargalo.pcap', '%s/qos_dualnet.pcap' % PCAP_DIR, '%s/qos_dualnet.txt' % PCAP_DIR
    limpar_arquivos(config_pares=config_pares, pcap_tmp=pcap_tmp, pcap_final=pcap_final, txt_final=txt_final, verbose=False)

    info('\n[2.1] Preparando as Pontas Receptoras...\n')
    for cp in config_pares: 
        porta = cp['porta']
        if cp['perfil']['proto'] == 'udp':
            info(f"      hC{cp['id']} (CLIENTE) aguardando UDP na porta {porta}...\n")
            cp['hc'].cmd(f'ITGRecv -l /tmp/cli_itg_{porta}.bin &')
        else:
            info(f"      hS{cp['id']} (SERVIDOR) aguardando TCP na porta {porta}...\n")
            cp['hs'].cmd(f'iperf3 -s -p {porta} --logfile /tmp/srv_{porta}.log &')
    # Aguarda receptores/servidores estabilizarem antes de iniciar captura/trafego.
    time.sleep(3)

    os.makedirs(PCAP_DIR, exist_ok=True)
    r1.cmd("tshark -i r1-eth1 -s 96 -w %s > /tmp/tshark.log 2>&1 & echo $! > /tmp/tshark.pid" % pcap_tmp)
    # Garante que a captura esteja ativa antes dos pings/trafego.
    time.sleep(3)

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
    # conforme as condicoes do enlace e a fila de Background no QoS.
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


    # ── Fase 1: somente ping, sem tráfego ───────────────────────────────
    info('\n[2.3A] Fase 1: PINGS INICIADOS (Rede Vazia) - 30 segundos\n')
    for cp in config_pares:
        cp['hc'].cmd(
            f"ping -w {DURACAO_PING_SO} -i 1 "
            f"-Q {hex(cp['perfil']['dscp_dec'])} "
            f"{cp['ip_s']} > /tmp/ping_cli_{cp['id']}.log &"
        )

    for restante in range(DURACAO_PING_SO, 0, -10):
        info('      %ds restantes (Somente Ping).\n' % restante)
        time.sleep(10)

    # ── Fase 2: somente tráfego, sem ping paralelo ─────────────────────
    info('\n[2.3B] TEMPESTADE DOWNLINK INICIADA (Servidor -> Cliente) - apenas trafego\n')

    cpu_stop, cpu_thread = iniciar_monitor_cpu("qos_trafego")

    for cp in config_pares:
        perf = cp['perfil']
        porta = cp['porta']
        bw_alvo = bw_voz_por_par if 'Voz' in perf['nome'] else (bw_video_por_par if 'Video' in perf['nome'] else 0)
        
        if perf['proto'] == 'udp':
            pkt_rate = int((bw_alvo * 1000000) / 8000) if bw_alvo > 0 else 100

            dscp_real = perf['dscp_dec'] >> 2

            cp['hs'].cmd("iptables -t mangle -F OUTPUT")
            cp['hs'].cmd(
                f"iptables -t mangle -A OUTPUT "
                f"-p udp -d {cp['ip_c']} "
                f"-j DSCP --set-dscp {dscp_real}"
            )

            info(f"      hS{cp['id']} atirando UDP para hC{cp['id']} | {perf['nome']} | {perf['dscp_nome']}\n")
            cp['hs'].cmd(
                f"ITGSend -a {cp['ip_c']} "
                f"-T UDP -C {pkt_rate} -c 1000 "
                f"-t {DURACAO_TRAFEGO * 1000} &"
            )
        else:
            dscp_real = perf['dscp_dec'] >> 2

            # Como o iperf3 usa -R, os dados TCP saem do servidor hS para o cliente hC.
            # Portanto a marcacao DSCP precisa ser forcada no servidor.
            cp['hs'].cmd("iptables -t mangle -F OUTPUT")
            cp['hs'].cmd(
                f"iptables -t mangle -A OUTPUT "
                f"-p tcp -d {cp['ip_c']} "
                f"-j DSCP --set-dscp {dscp_real}"
            )

            # Ajuste de buffers TCP para cenarios de alto BDP:
            # exemplo extremo: 1000 Mbps com 300 ms de atraso.
            for h in (cp['hc'], cp['hs']):
                h.cmd("sysctl -w net.core.rmem_max=134217728 >/dev/null 2>&1")
                h.cmd("sysctl -w net.core.wmem_max=134217728 >/dev/null 2>&1")
                h.cmd("sysctl -w net.ipv4.tcp_rmem='4096 87380 134217728' >/dev/null 2>&1")
                h.cmd("sysctl -w net.ipv4.tcp_wmem='4096 65536 134217728' >/dev/null 2>&1")
                h.cmd("sysctl -w net.ipv4.tcp_congestion_control=cubic >/dev/null 2>&1")

            info(f"      hC{cp['id']} puxando TCP de hS{cp['id']} | {perf['nome']} | {perf['dscp_nome']}\n")

            cp['hc'].cmd(
                f"iperf3 -c {cp['ip_s']} -p {porta} "
                f"-t {DURACAO_TRAFEGO - 5} "
                f"-S {perf['dscp_dec']} "
                f"-R --connect-timeout 30000 "
                f"--logfile /tmp/cli_{porta}.log &"
            )

    for restante in range(DURACAO_TRAFEGO, 0, -10):
        info('      %ds restantes (Somente Trafego)...\n' % restante)
        time.sleep(10)

    # Espera extra para garantir fechamento dos logs em cenarios com alto delay/perda.
    info('      Aguardando 5s para finalizacao dos logs...\n')
    time.sleep(5)
    parar_monitor_cpu(cpu_stop, cpu_thread)

    info('\n[FASE 2] Experimento concluido. Processando D-ITG e Capturas...\n')
    r1.cmd('pkill -f tshark 2>/dev/null')
    subprocess.run('pkill -9 ITGRecv', shell=True)
    time.sleep(2)

    # ── Extração D-ITG com nomes finais por classe ─────────────────────
    delay_limpo = str(gargalo['delay']).replace('ms', '')
    loss_txt = f"{gargalo['loss']:.1f}"
    bw_txt = str(gargalo['bw'])

    pasta_resultado = os.path.join(
        PCAP_DIR,
        'rodadas_qos',
        f'ComQoSBanda{bw_txt}MbpsLoss{loss_txt}Delay{delay_limpo}'
    )
    os.makedirs(pasta_resultado, exist_ok=True)

    padrao_rep = os.path.join(
        pasta_resultado,
        f"delay_*_bw{bw_txt}_del{delay_limpo}_loss{loss_txt}_rep*.dat"
    )

    reps_existentes = []
    for caminho in glob.glob(padrao_rep):
        nome = os.path.basename(caminho)
        parte = nome.split('_rep')[-1].replace('.dat', '')
        if parte.isdigit():
            reps_existentes.append(int(parte))

    rep_txt = os.environ.get('REP') or os.environ.get('REPETICAO')
    if rep_txt is None:
        rep_txt = str(max(reps_existentes, default=0) + 1)

    nome_classe = {
        'VoIP (AC-Voz)': 'voz',
        'Video': 'video',
        'Background': 'bg',
        'Best-Effort': 'be',
    }

    for cp in config_pares:
        if cp['perfil']['proto'] == 'udp':
            p = cp['porta']
            classe = nome_classe.get(cp['perfil']['nome'], f"par{cp['id']}")
            sufixo = f"bw{bw_txt}_del{delay_limpo}_loss{loss_txt}_rep{rep_txt}"

            bin_itg = f"/tmp/cli_itg_{p}.bin"
            log_sumario = f"/tmp/cli_{p}.log"

            delay_dat = os.path.join(pasta_resultado, f"delay_{classe}_{sufixo}.dat")
            jitter_dat = os.path.join(pasta_resultado, f"jitter_{classe}_{sufixo}.dat")
            bitrate_dat = os.path.join(pasta_resultado, f"bitrate_{classe}_{sufixo}.dat")
            loss_dat = os.path.join(pasta_resultado, f"loss_{classe}_{sufixo}.dat")

            # 1. Sumário global do D-ITG
            cmd_sumario = f"ITGDec {bin_itg} > {log_sumario} 2>/tmp/itgdec_{classe}_sumario.err"
            ret_sumario = cp['hc'].cmd(cmd_sumario)

            # 2. Arquivos temporais em /tmp primeiro
            tmp_delay = f"/tmp/delay_{classe}_{sufixo}.dat"
            tmp_jitter = f"/tmp/jitter_{classe}_{sufixo}.dat"
            tmp_bitrate = f"/tmp/bitrate_{classe}_{sufixo}.dat"
            tmp_loss = f"/tmp/loss_{classe}_{sufixo}.dat"

            cmd_temporal = (
                f"ITGDec {bin_itg} "
                f"-d 1000 {tmp_delay} "
                f"-j 1000 {tmp_jitter} "
                f"-b 1000 {tmp_bitrate} "
                f"-p 1000 {tmp_loss} "
                f"> /tmp/cli_{p}_temporal.log 2>/tmp/itgdec_{classe}_temporal.err"
            )
            cp['hc'].cmd(cmd_temporal)

            # 3. Copia para a pasta final
            subprocess.run(f"cp {tmp_delay} {delay_dat}", shell=True)
            subprocess.run(f"cp {tmp_jitter} {jitter_dat}", shell=True)
            subprocess.run(f"cp {tmp_bitrate} {bitrate_dat}", shell=True)
            subprocess.run(f"cp {tmp_loss} {loss_dat}", shell=True)

            # 4. Verificação real
            arquivos_ok = all(os.path.exists(arq) for arq in [delay_dat, jitter_dat, bitrate_dat, loss_dat])

            if arquivos_ok:
                info(f"[D-ITG] OK - arquivos temporais criados para {classe}: {pasta_resultado}\n")
            else:
                info(f"[ERRO D-ITG] Falha ao criar arquivos temporais para {classe}.\n")
                info(f"           Verifique: /tmp/itgdec_{classe}_temporal.err\n")
                info(f"           Verifique: /tmp/cli_{p}_temporal.log\n")

    r1.cmd('cp %s %s 2>/dev/null' % (pcap_tmp, pcap_final))
    os.system('chmod 644 %s 2>/dev/null' % pcap_final)
    exportar_pcap_para_txt(pcap_final, txt_final, r1)
    salvar_verificacao(r1, s2, 'apos_trafego')

    info('\n' + '='*60 + '\n  RESULTADOS DOWNLINK (Com QoS)\n' + '='*60 + '\n')
    for cp in config_pares:
        result = cp['hc'].cmd(f"cat /tmp/cli_{cp['porta']}.log 2>/dev/null")
        if result.strip():
            linhas = '\n'.join(result.splitlines()[-15:]) if cp['perfil']['proto'] == 'udp' else result
            info(f"\n[Par {cp['id']} - {cp['perfil']['nome']}] hS{cp['id']} -> hC{cp['id']}:\n{linhas}\n")

    net.stop()

if __name__ == '__main__':
    run()
