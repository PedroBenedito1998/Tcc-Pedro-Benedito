# Experimento TCC - QoS SDN com DSCP, OpenFlow e HTB

Este repositorio contem apenas o codigo usado para executar, auditar e analisar o experimento do TCC sobre priorizacao de trafego por classe de servico em ambiente SDN.

Os dados brutos, logs completos, capturas `.pcap`, tabelas geradas e imagens finais nao estao incluidos neste pacote para evitar arquivos volumosos e preservar o repositorio como artefato de codigo.

## Conteudo

- `baseline.py`: topologia dumbbell e execucao do cenario sem QoS por filas.
- `Qos.py`: topologia dumbbell e execucao do cenario com filas HTB no Open vSwitch.
- `qos_switch.py`: controlador Ryu/OpenFlow 1.3 que le DSCP e aplica `set_queue`.
- `rodar_definitivo_5reps.sh`: automacao da matriz principal com 5 repeticoes.
- `rodar_smoke_netem_qos.sh`: execucao curta para validacao do posicionamento de NetEm e QoS.
- `auditar_qos_live.sh`, `auditoria.sh`, `auditoria_baseline.sh`: scripts auxiliares de auditoria de regras, filas e ambiente.
- `auditoria_pos_teste_tcc.py`: auditoria pos-execucao dos artefatos gerados.
- `analise_tcc_qos_baseline.py`: consolidacao de metricas, tabelas e plots a partir das saidas do experimento.
- `classificar_auditoria_tcc.py`: classificacao final de alertas e falhas da auditoria.
- `calcular_ic95_configuracoes_5reps.py`: calculo de intervalos de confianca por bootstrap.
- `gerar_plot_vitorias_config_organizado.py`: geracao do plot agregado de vitorias por configuracao.

## Ambiente usado no TCC

- Ubuntu 24.04.2 LTS em maquina virtual.
- Mininet 2.6.
- Ryu 4.34 com OpenFlow 1.3.
- Open vSwitch 3.3.0.
- D-ITG 2.8.1.
- iperf3 3.16.
- tshark/Wireshark 4.2.2.
- Python 3.13.12.

## Dependencias Python

As bibliotecas Python usadas nas rotinas de auditoria e analise estao em `requirements.txt`. Mininet, Ryu, Open vSwitch, D-ITG, iperf3, tshark e ferramentas `tc`/NetEm devem ser instalados no sistema operacional, pois nao sao dependencias pip comuns.

## Observacoes de reproducibilidade

Os scripts possuem caminhos locais usados no testbed original, como:

- `/home/wifi/Documents/Benedito/dumbell`
- `$HOME/Documents/tcc`

Antes de executar em outro ambiente, ajuste `BASE_ROOT`, `TCC_DIR` e, quando necessario, a variavel `PCAP_DIR`.

A matriz principal considera:

- Bandas: 10, 100 e 1000 Mbps.
- Atrasos: 0, 50, 100 e 300 ms.
- Perdas: 0.0%, 0.1%, 1.0% e 3.0%.
- Repeticoes: 5 por cenario.

Os fluxos ativos sao voz UDP, video UDP e TCP de background. A classe best effort fica configurada como suporte para trafego DSCP 0, mas nao e um fluxo principal da matriz experimental.

## Execucao basica

No ambiente Linux com Mininet e ferramentas instaladas:

```bash
python3 -m py_compile baseline.py Qos.py qos_switch.py auditoria_pos_teste_tcc.py analise_tcc_qos_baseline.py classificar_auditoria_tcc.py
bash rodar_smoke_netem_qos.sh
bash rodar_definitivo_5reps.sh
```

Depois da execucao:

```bash
python3 auditoria_pos_teste_tcc.py --base /caminho/para/saida
python3 analise_tcc_qos_baseline.py --base /caminho/para/saida
```

Os comandos exatos podem precisar de `sudo`, dependendo da instalacao do Mininet, Open vSwitch e `tc`.
