#!/usr/bin/env python3
"""
Controlador Ryu com QoS baseado em DSCP (WMM) – VERSÃO DEFINITIVA
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet, ipv4
from ryu.lib import hub
import time
import os

class QoS13(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(QoS13, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}

        self.flowstats_file = "/tmp/ryu_flowstats.csv"
        self.portstats_file = "/tmp/ryu_portstats.csv"

        # Reinicia os arquivos a cada execucao do controlador.
        with open(self.flowstats_file, "w") as f:
            f.write("timestamp,dpid,table_id,priority,match,packet_count,byte_count,duration_sec\n")

        with open(self.portstats_file, "w") as f:
            f.write("timestamp,dpid,port_no,rx_packets,tx_packets,rx_bytes,tx_bytes,rx_dropped,tx_dropped,rx_errors,tx_errors,duration_sec\n")

        # Thread de monitoramento. Nao interfere na decisao de encaminhamento.
        self.monitor_thread = hub.spawn(self._monitor)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def get_queue(self, dscp):
        """Mapeia o valor decimal do DSCP para a fila HTB correspondente."""
        if dscp == 46:      # EF – Voz
            return 0
        elif dscp == 34:    # AF41 – Vídeo
            return 1
        elif dscp == 8:     # BK – Background
            return 3
        return 2            # BE – Best-Effort (Fila Padrão)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None, idle=120, hard=0, queue_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        new_actions = list(actions)
        
        # Aplica a alteração de fila apenas no Switch Gargalo (S2)
        if datapath.id == 2 and queue_id is not None:
            new_actions = [parser.OFPActionSetQueue(queue_id)] + new_actions

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, new_actions)]
        
        if buffer_id:
            mod = parser.OFPFlowMod(
                datapath=datapath, buffer_id=buffer_id,
                priority=priority, match=match,
                instructions=inst, idle_timeout=idle, hard_timeout=hard)
        else:
            mod = parser.OFPFlowMod(
                datapath=datapath, priority=priority,
                match=match, instructions=inst,
                idle_timeout=idle, hard_timeout=hard)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        """
        Registra quais switches estao conectados ao controlador.
        Isso permite solicitar FlowStats e PortStats periodicamente.
        """
        datapath = ev.datapath

        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.info("registrando datapath: %s", datapath.id)
                self.datapaths[datapath.id] = datapath

        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.info("removendo datapath: %s", datapath.id)
                del self.datapaths[datapath.id]

    def _monitor(self):
        """
        Solicita estatisticas a cada 5 segundos.
        Esses dados sao complementares: servem para evidenciar regras, portas,
        contadores e comportamento do switch durante o experimento.
        """
        while True:
            for dp in list(self.datapaths.values()):
                self._request_stats(dp)
            hub.sleep(5)

    def _request_stats(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        self.logger.info("solicitando FlowStats/PortStats do datapath %s", datapath.id)

        req_flow = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req_flow)

        req_port = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req_port)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        dpid = ev.msg.datapath.id

        with open(self.flowstats_file, "a") as f:
            for stat in ev.msg.body:
                match_txt = str(stat.match).replace(",", ";")
                f.write(
                    f"{timestamp},{dpid},{stat.table_id},{stat.priority},"
                    f"\"{match_txt}\",{stat.packet_count},{stat.byte_count},{stat.duration_sec}\n"
                )

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        dpid = ev.msg.datapath.id

        with open(self.portstats_file, "a") as f:
            for stat in ev.msg.body:
                f.write(
                    f"{timestamp},{dpid},{stat.port_no},"
                    f"{stat.rx_packets},{stat.tx_packets},"
                    f"{stat.rx_bytes},{stat.tx_bytes},"
                    f"{stat.rx_dropped},{stat.tx_dropped},"
                    f"{stat.rx_errors},{stat.tx_errors},"
                    f"{stat.duration_sec}\n"
                )

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        dst = eth.dst
        src = eth.src
        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})

        self.logger.info("packet in %s %s %s %s", dpid, src, dst, in_port)
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        queue_id = None
        if out_port != ofproto.OFPP_FLOOD:
            match_kwargs = {
                'in_port': in_port,
                'eth_src': src,
                'eth_dst': dst,
            }
            ip_pkt = pkt.get_protocol(ipv4.ipv4)
            if ip_pkt:
                match_kwargs['eth_type'] = 0x0800
                match_kwargs['ipv4_src'] = ip_pkt.src
                match_kwargs['ipv4_dst'] = ip_pkt.dst
                match_kwargs['ip_proto'] = ip_pkt.proto
                match_kwargs['ip_dscp'] = ip_pkt.tos >> 2

                # Mapeia a fila de forma segura usando o dicionário nativo do Python
                if dpid == 2:
                    queue_id = self.get_queue(match_kwargs['ip_dscp'])

                match = parser.OFPMatch(**match_kwargs)
                self.add_flow(datapath, 10, match, actions, queue_id=queue_id)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
        packet_out_actions = actions
        if datapath.id == 2 and queue_id is not None:
            packet_out_actions = [parser.OFPActionSetQueue(queue_id)] + actions
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=packet_out_actions, data=data)
        datapath.send_msg(out)
