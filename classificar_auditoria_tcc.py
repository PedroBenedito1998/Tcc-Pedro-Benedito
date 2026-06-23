#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
classificar_auditoria_tcc.py

Classificador final para interpretar a auditoria do TCC.

Motivacao:
- A auditoria bruta marca como ERROR quando o baseline tem flow_missing,
  iperf_zero ou iperf_parse.
- Para o TCC, isso pode ser resultado experimental valido:
  colapso/starvation/indisponibilidade do baseline.
- O erro fatal deve permanecer quando:
    * ocorre no QoS;
    * falta arquivo;
    * QoS nao gera metricas;
    * QoS perde fluxo;
    * ha falha real de execucao.

Uso:
python3 classificar_auditoria_tcc.py \
  --base /home/wifi/Documents/Benedito/dumbell \
  --saida /home/wifi/Documents/Benedito/dumbell/classificacao_final_tcc.txt
"""

import argparse
import csv
from pathlib import Path
from collections import Counter, defaultdict


BASELINE_OUTAGE_CATEGORIES = {
    "iperf_zero",
    "iperf_zero_partial",
    "iperf_parse",
    "iperf_incomplete",
    "iperf_rows",
    "flow_missing",
    "dat_rows",
}

NON_FATAL_COMPARE_CATEGORIES = {
    "qos_not_better",
}

FATAL_ALWAYS = {
    "file_missing",
    "file_empty",
}


def load_csv(path: Path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="ignore") as f:
        return list(csv.DictReader(f))


def classify(row):
    severity = (row.get("severity") or "").strip()
    scenario = (row.get("scenario") or "").strip()
    category = (row.get("category") or "").strip()

    # Arquivo faltando/vazio é fatal em qualquer cenário.
    if category in FATAL_ALWAYS:
        return "FATAL", "arquivo_obrigatorio_ausente_ou_vazio"

    # Erros no QoS continuam fatais.
    if scenario == "qos" and severity == "ERROR":
        return "FATAL", "erro_no_cenario_qos"

    # Colapso no baseline é resultado experimental, não falha operacional.
    if scenario == "baseline" and category in BASELINE_OUTAGE_CATEGORIES:
        return "WARN", "colapso_ou_starvation_do_baseline"

    # Comparações QoS não melhor com rep=1 ou baseline degradado não bloqueiam.
    if scenario == "compare" and category in NON_FATAL_COMPARE_CATEGORIES:
        return "WARN", "comparacao_nao_bloqueante"

    # Qualquer ERROR restante é fatal.
    if severity == "ERROR":
        return "FATAL", "erro_nao_classificado"

    # WARN restante é aviso.
    if severity == "WARN":
        return "WARN", "aviso_nao_bloqueante"

    return "INFO", "informativo"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="/home/wifi/Documents/Benedito/dumbell")
    parser.add_argument("--saida", default=None)
    args = parser.parse_args()

    base = Path(args.base)
    problemas_path = base / "auditoria_pos_teste" / "tabelas" / "problemas_detectados.csv"

    rows = load_csv(problemas_path)

    saida = Path(args.saida) if args.saida else base / "classificacao_final_tcc.txt"

    fatais = []
    avisos = []
    infos = []

    contagem = Counter()
    por_combo = defaultdict(list)

    for row in rows:
        final, motivo = classify(row)
        row["_classificacao_final"] = final
        row["_motivo_final"] = motivo

        contagem[(final, motivo)] += 1

        combo = (
            row.get("scenario", ""),
            row.get("bw", ""),
            row.get("delay", ""),
            row.get("loss", ""),
            row.get("rep", ""),
        )
        por_combo[combo].append(row)

        if final == "FATAL":
            fatais.append(row)
        elif final == "WARN":
            avisos.append(row)
        else:
            infos.append(row)

    with saida.open("w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write("CLASSIFICACAO FINAL DA AUDITORIA - TCC QoS SDN\n")
        f.write("=" * 80 + "\n\n")

        f.write(f"Arquivo analisado: {problemas_path}\n")
        f.write(f"Total de ocorrencias: {len(rows)}\n")
        f.write(f"Fatais: {len(fatais)}\n")
        f.write(f"Avisos: {len(avisos)}\n")
        f.write(f"Informativos: {len(infos)}\n\n")

        f.write("Resumo por classificacao:\n")
        for (final, motivo), qtd in sorted(contagem.items()):
            f.write(f"  {final:5s} | {motivo:40s} | {qtd}\n")

        f.write("\n" + "=" * 80 + "\n")
        f.write("VEREDITO FINAL\n")
        f.write("=" * 80 + "\n")

        if fatais:
            f.write("RESULTADO: NAO APTO PARA O DEFINITIVO.\n")
            f.write("Motivo: ainda existem erros fatais reais.\n")
        else:
            f.write("RESULTADO: APTO PARA O DEFINITIVO.\n")
            f.write("Motivo: nao ha erros fatais; avisos restantes representam colapso/degradacao do baseline ou comparacoes nao bloqueantes.\n")

        f.write("\n" + "=" * 80 + "\n")
        f.write("ERROS FATAIS\n")
        f.write("=" * 80 + "\n")
        if not fatais:
            f.write("Nenhum erro fatal.\n")
        else:
            for r in fatais:
                f.write(
                    f"{r.get('scenario')}, bw={r.get('bw')}, delay={r.get('delay')}, "
                    f"loss={r.get('loss')}, rep={r.get('rep')}, "
                    f"cat={r.get('category')}, msg={r.get('message')}, file={r.get('file')}\n"
                )

        f.write("\n" + "=" * 80 + "\n")
        f.write("AVISOS / RESULTADOS EXPERIMENTAIS\n")
        f.write("=" * 80 + "\n")
        if not avisos:
            f.write("Nenhum aviso.\n")
        else:
            for r in avisos:
                f.write(
                    f"{r.get('scenario')}, bw={r.get('bw')}, delay={r.get('delay')}, "
                    f"loss={r.get('loss')}, rep={r.get('rep')}, "
                    f"cat={r.get('category')} -> {r.get('_motivo_final')}, "
                    f"msg={r.get('message')}\n"
                )

        f.write("\n" + "=" * 80 + "\n")
        f.write("INTERPRETACAO PARA O TCC\n")
        f.write("=" * 80 + "\n")
        f.write(
            "O criterio de sucesso nao exige que o QoS seja numericamente melhor em todas as comparacoes brutas.\n"
            "O criterio principal e manter os servicos sensiveis, como voz e video, disponiveis e com atraso/jitter/perda controlados.\n"
            "Quando o baseline apresenta iperf zerado, flow_missing ou indisponibilidade de fluxo sob condicoes severas,\n"
            "isso deve ser reportado como colapso do baseline, nao como falha do mecanismo QoS.\n"
            "Falha critica seria observar esses colapsos no cenario QoS, ou ausencia de arquivos/metricas obrigatorias.\n"
        )

    print(f"Classificacao final salva em: {saida}")
    print("")
    print(saida.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
