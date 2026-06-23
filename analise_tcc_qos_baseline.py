#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analise_tcc_qos_baseline.py

Analise estatistica e geracao de plots para o TCC:
- Baseline Sem QoS x QoS SDN via DSCP
- UDP: atraso fim-a-fim, jitter, perda e bitrate D-ITG para Voz e Video
- TCP: goodput/vazao util via iperf3, throughput observado no gargalo via TXT/tshark,
       overhead estimado = throughput_gargalo - goodput
- DSCP: contagem de pacotes no TXT exportado do pcap
- Saidas: CSVs, PNGs e relatorio TXT

Padrao esperado de pastas:
  /home/wifi/Documents/Benedito/dumbell/
    rodadas_baseline/SemQoSBanda10MbpsLoss0.0Delay0/
    rodadas_qos/ComQoSBanda10MbpsLoss0.0Delay0/

Exemplo:
  python3 analise_tcc_qos_baseline.py
  python3 analise_tcc_qos_baseline.py --base /home/wifi/Documents/Benedito/dumbell --bw 10 --delay 0 --loss 0.0
"""

import argparse
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# =============================================================================
# Configuracao
# =============================================================================

CLASSES_UDP = ["voz", "video"]
TODAS_CLASSES = ["voz", "video", "bg"]

DSCP_ESPERADO = {
    "voz": 46,
    "video": 34,
    "bg": 8,
    "be": 0,
}

FLUXOS_DOWNLINK = {
    "voz": {"src": "20.0.0.1", "dst": "10.0.0.1", "proto": 17, "dscp": 46},
    "video": {"src": "20.0.0.2", "dst": "10.0.0.2", "proto": 17, "dscp": 34},
    "bg": {"src": "20.0.0.3", "dst": "10.0.0.3", "proto": 6, "dscp": 8},
}

METRICAS_UDP = {
    "delay": {
        "label": "Atraso fim-a-fim UDP",
        "ylabel": "Atraso (ms)",
        "converter": lambda x: x * 1000.0,  # D-ITG geralmente em segundos
    },
    "jitter": {
        "label": "Jitter UDP",
        "ylabel": "Jitter (ms)",
        "converter": lambda x: x * 1000.0,  # D-ITG geralmente em segundos
    },
    "loss": {
        "label": "Perda UDP",
        "ylabel": "Perda de pacotes (D-ITG)",
        "converter": lambda x: x,
    },
    "bitrate": {
        "label": "Bitrate UDP",
        "ylabel": "Bitrate (D-ITG)",
        "converter": lambda x: x,
    },
}


# =============================================================================
# Utilitarios
# =============================================================================

def ensure_dirs(out_dir: Path) -> Dict[str, Path]:
    dirs = {
        "root": out_dir,
        "plots": out_dir / "plots",
        "tables": out_dir / "tabelas",
        "reports": out_dir / "relatorios",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def parse_combo_from_folder(folder: Path) -> Dict[str, object]:
    """
    Extrai bw, loss, delay de nomes:
    SemQoSBanda10MbpsLoss0.0Delay0
    ComQoSBanda10MbpsLoss0.0Delay0
    """
    m = re.search(r"Banda(?P<bw>\d+)MbpsLoss(?P<loss>[\d.]+)Delay(?P<delay>\d+)", folder.name)
    if not m:
        return {"bw": None, "loss": None, "delay": None}
    return {
        "bw": int(m.group("bw")),
        "loss": float(m.group("loss")),
        "delay": int(m.group("delay")),
    }


def parse_rep_from_name(path: Path) -> Optional[int]:
    m = re.search(r"_rep(\d+)", path.name)
    return int(m.group(1)) if m else None


def scenario_label(scenario: str) -> str:
    return "Com QoS" if scenario == "qos" else "Baseline"


def class_label(cls: str) -> str:
    return {"voz": "Voz", "video": "Video", "bg": "Background", "be": "Best-Effort"}.get(cls, cls)


# =============================================================================
# Leitura D-ITG .dat
# =============================================================================

def read_itg_dat(path: Path, metric: str) -> pd.DataFrame:
    """
    Le arquivos .dat do ITGDec no formato:
      Time flow Aggregate-Flow
      0.000000 0.006113 0.006113
    Usa a primeira coluna como tempo e a ultima coluna como valor agregado.
    """
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=["tempo_s", "valor"])

    try:
        df = pd.read_csv(path, sep=r"\s+", engine="python", comment="#")
    except Exception:
        return pd.DataFrame(columns=["tempo_s", "valor"])

    if df.empty or df.shape[1] < 2:
        return pd.DataFrame(columns=["tempo_s", "valor"])

    tempo = pd.to_numeric(df.iloc[:, 0], errors="coerce")
    valor = pd.to_numeric(df.iloc[:, -1], errors="coerce")

    out = pd.DataFrame({"tempo_s": tempo, "valor": valor}).dropna()
    if metric in METRICAS_UDP:
        out["valor"] = METRICAS_UDP[metric]["converter"](out["valor"])
    return out


def collect_udp_metrics(base_dir: Path, scenario: str, bw: Optional[int], delay: Optional[int], loss: Optional[float]) -> pd.DataFrame:
    root = base_dir / ("rodadas_qos" if scenario == "qos" else "rodadas_baseline")
    if not root.exists():
        return pd.DataFrame()

    rows = []
    for folder in sorted(root.glob("*")):
        if not folder.is_dir():
            continue
        combo = parse_combo_from_folder(folder)
        if bw is not None and combo["bw"] != bw:
            continue
        if delay is not None and combo["delay"] != delay:
            continue
        if loss is not None and combo["loss"] != loss:
            continue

        for metric in METRICAS_UDP:
            for cls in CLASSES_UDP:
                pattern = f"{metric}_{cls}_bw*_del*_loss*_rep*.dat"
                for f in sorted(folder.glob(pattern)):
                    rep = parse_rep_from_name(f)
                    dat = read_itg_dat(f, metric)
                    if dat.empty:
                        continue
                    dat["scenario"] = scenario
                    dat["scenario_label"] = scenario_label(scenario)
                    dat["classe"] = cls
                    dat["classe_label"] = class_label(cls)
                    dat["metrica"] = metric
                    dat["arquivo"] = str(f)
                    dat["rep"] = rep
                    dat["bw"] = combo["bw"]
                    dat["delay"] = combo["delay"]
                    dat["loss"] = combo["loss"]
                    rows.append(dat)

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# =============================================================================
# Leitura iperf3 TCP
# =============================================================================

IPERF_INTERVAL_RE = re.compile(
    r"\[\s*\d+\]\s+"
    r"(?P<t0>\d+(?:\.\d+)?)-(?P<t1>\d+(?:\.\d+)?)\s+sec\s+"
    r"(?P<transfer>[\d.]+)\s+(?P<transfer_unit>[KMG]?Bytes)\s+"
    r"(?P<bitrate>[\d.]+)\s+(?P<br_unit>[KMG]?bits/sec)"
)


def bitrate_to_mbps(value: float, unit: str) -> float:
    unit = unit.strip()
    if unit == "bits/sec":
        return value / 1e6
    if unit == "Kbits/sec":
        return value / 1e3
    if unit == "Mbits/sec":
        return value
    if unit == "Gbits/sec":
        return value * 1e3
    return value


def read_iperf_log(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=["tempo_s", "goodput_mbps"])

    rows = []
    text = path.read_text(errors="ignore").splitlines()
    for line in text:
        # Ignora linhas finais sender/receiver para manter apenas serie temporal.
        if "sender" in line or "receiver" in line:
            continue
        m = IPERF_INTERVAL_RE.search(line)
        if not m:
            continue
        t0 = float(m.group("t0"))
        t1 = float(m.group("t1"))
        bitrate = float(m.group("bitrate"))
        unit = m.group("br_unit")
        rows.append({
            "tempo_s": t0,
            "tempo_fim_s": t1,
            "goodput_mbps": bitrate_to_mbps(bitrate, unit),
        })
    return pd.DataFrame(rows)


def collect_tcp_goodput(base_dir: Path, scenario: str, bw: Optional[int], delay: Optional[int], loss: Optional[float]) -> pd.DataFrame:
    root = base_dir / ("rodadas_qos" if scenario == "qos" else "rodadas_baseline")
    if not root.exists():
        return pd.DataFrame()

    rows = []
    for folder in sorted(root.glob("*")):
        if not folder.is_dir():
            continue
        combo = parse_combo_from_folder(folder)
        if bw is not None and combo["bw"] != bw:
            continue
        if delay is not None and combo["delay"] != delay:
            continue
        if loss is not None and combo["loss"] != loss:
            continue

        for f in sorted(folder.glob("iperf_bg_bw*_del*_loss*_rep*.log")):
            rep = parse_rep_from_name(f)
            df = read_iperf_log(f)
            if df.empty:
                continue
            df["scenario"] = scenario
            df["scenario_label"] = scenario_label(scenario)
            df["classe"] = "bg"
            df["classe_label"] = "Background"
            df["arquivo"] = str(f)
            df["rep"] = rep
            df["bw"] = combo["bw"]
            df["delay"] = combo["delay"]
            df["loss"] = combo["loss"]
            rows.append(df)

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# =============================================================================
# Leitura TXT exportado do tshark
# =============================================================================

def read_tshark_txt(path: Path) -> pd.DataFrame:
    """
    Le TXT gerado com:
    frame.number, frame.time_relative, ip.src, ip.dst, ip.proto,
    ip.dsfield.dscp, tcp.dstport, udp.dstport, frame.len

    O codigo de export usa '-E separator=/t', mas em algumas versoes fica TAB real.
    Esta funcao tenta TAB, literal /t e whitespace.
    """
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()

    seps = ["\t", "/t", r"\s+"]
    df = None
    for sep in seps:
        try:
            tmp = pd.read_csv(path, sep=sep, engine="python", quotechar='"')
            if tmp.shape[1] >= 6:
                df = tmp
                break
        except Exception:
            continue

    if df is None or df.empty:
        return pd.DataFrame()

    expected = [
        "frame.number", "frame.time_relative", "ip.src", "ip.dst", "ip.proto",
        "ip.dsfield.dscp", "tcp.dstport", "udp.dstport", "frame.len"
    ]
    if len(df.columns) >= len(expected):
        df = df.iloc[:, :len(expected)]
        df.columns = expected

    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.replace('"', "", regex=False).str.strip()

    numeric_cols = ["frame.number", "frame.time_relative", "ip.proto", "ip.dsfield.dscp", "tcp.dstport", "udp.dstport", "frame.len"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def find_txt_files(base_dir: Path, scenario: str, bw: Optional[int], delay: Optional[int], loss: Optional[float]) -> List[Path]:
    root = base_dir / ("rodadas_qos" if scenario == "qos" else "rodadas_baseline")
    prefix = "qos" if scenario == "qos" else "baseline"
    files = []
    for folder in sorted(root.glob("*")):
        if not folder.is_dir():
            continue
        combo = parse_combo_from_folder(folder)
        if bw is not None and combo["bw"] != bw:
            continue
        if delay is not None and combo["delay"] != delay:
            continue
        if loss is not None and combo["loss"] != loss:
            continue
        files.extend(sorted(folder.glob(f"{prefix}_bw*_del*_loss*_rep*.txt")))
    return files


def collect_dscp_counts(base_dir: Path, scenario: str, bw: Optional[int], delay: Optional[int], loss: Optional[float]) -> pd.DataFrame:
    rows = []
    for f in find_txt_files(base_dir, scenario, bw, delay, loss):
        combo = parse_combo_from_folder(f.parent)
        rep = parse_rep_from_name(f)
        df = read_tshark_txt(f)
        if df.empty or "ip.dsfield.dscp" not in df.columns:
            continue
        counts = df["ip.dsfield.dscp"].dropna().astype(int).value_counts().sort_index()
        for dscp, count in counts.items():
            rows.append({
                "scenario": scenario,
                "scenario_label": scenario_label(scenario),
                "rep": rep,
                "bw": combo["bw"],
                "delay": combo["delay"],
                "loss": combo["loss"],
                "dscp": int(dscp),
                "pacotes": int(count),
                "arquivo": str(f),
            })
    return pd.DataFrame(rows)


def collect_pcap_throughput(base_dir: Path, scenario: str, bw: Optional[int], delay: Optional[int], loss: Optional[float]) -> pd.DataFrame:
    """
    Throughput observado no gargalo por segundo, com base em frame.len no TXT.
    Usa apenas o sentido downlink principal por classe.
    """
    rows = []
    for f in find_txt_files(base_dir, scenario, bw, delay, loss):
        combo = parse_combo_from_folder(f.parent)
        rep = parse_rep_from_name(f)
        df = read_tshark_txt(f)
        if df.empty:
            continue

        required = {"frame.time_relative", "ip.src", "ip.dst", "ip.proto", "frame.len"}
        if not required.issubset(df.columns):
            continue

        for cls, fl in FLUXOS_DOWNLINK.items():
            mask = (
                (df["ip.src"].astype(str) == fl["src"]) &
                (df["ip.dst"].astype(str) == fl["dst"]) &
                (df["ip.proto"] == fl["proto"])
            )
            sub = df.loc[mask].copy()
            if sub.empty:
                continue

            sub["segundo_global"] = np.floor(sub["frame.time_relative"]).astype(int)
            agg = sub.groupby("segundo_global", as_index=False)["frame.len"].sum()
            agg["throughput_mbps_pcap"] = (agg["frame.len"] * 8.0) / 1e6
            t0 = agg["segundo_global"].min()
            agg["tempo_s"] = agg["segundo_global"] - t0

            agg["scenario"] = scenario
            agg["scenario_label"] = scenario_label(scenario)
            agg["classe"] = cls
            agg["classe_label"] = class_label(cls)
            agg["rep"] = rep
            agg["bw"] = combo["bw"]
            agg["delay"] = combo["delay"]
            agg["loss"] = combo["loss"]
            agg["arquivo"] = str(f)
            rows.append(agg)

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# =============================================================================
# Estatisticas
# =============================================================================

def summary_stats(df: pd.DataFrame, value_col: str, group_cols: List[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = df.groupby(group_cols)[value_col]
    out = g.agg(
        n="count",
        media="mean",
        mediana="median",
        desvio_padrao="std",
        minimo="min",
        maximo="max",
    ).reset_index()
    p95 = g.quantile(0.95).rename("p95").reset_index()
    p99 = g.quantile(0.99).rename("p99").reset_index()
    out = out.merge(p95, on=group_cols, how="left").merge(p99, on=group_cols, how="left")
    return out


# =============================================================================
# Plots
# =============================================================================
def safe_loss_str(loss_value) -> str:
    """Converte loss 0.1 em 0p1 para usar no nome do arquivo."""
    return str(loss_value).replace(".", "p")


def combo_slug(bw, delay, loss_value) -> str:
    """Nome padronizado para arquivos por combinacao."""
    return f"bw{int(bw)}_del{int(delay)}_loss{safe_loss_str(loss_value)}"

def plot_time_compare(df: pd.DataFrame, x: str, y: str, title: str, ylabel: str, out: Path, group_col: str = "scenario_label") -> None:
    if df.empty:
        return

    plt.figure(figsize=(11, 6))
    for name, sub in df.groupby(group_col):
        mean = sub.groupby(x, as_index=False)[y].mean().sort_values(x)
        plt.plot(mean[x], mean[y], marker="o", linewidth=1.8, markersize=3, label=str(name))

    plt.title(title)
    plt.xlabel("Tempo na fase de trafego (s)")
    plt.ylabel(ylabel)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close()

def plot_time_compare_services(
    df: pd.DataFrame,
    x: str,
    y: str,
    title: str,
    ylabel: str,
    out: Path,
    classes_order: Optional[List[str]] = None
) -> None:
    """
    Grafico temporal com:
      - cor = servico/classe
      - estilo = cenario Baseline ou QoS

    Exemplo:
      Voz Baseline
      Voz QoS
      Video Baseline
      Video QoS
      Background Baseline
      Background QoS
    """
    if df.empty:
        return

    if classes_order is None:
        classes_order = ["voz", "video", "bg"]

    color_map = {
        "voz": "tab:blue",
        "video": "tab:orange",
        "bg": "tab:green",
        "be": "tab:red",
    }

    linestyle_map = {
        "baseline": "--",
        "qos": "-",
    }

    scenario_order = ["baseline", "qos"]

    plt.figure(figsize=(12, 6))

    for cls in classes_order:
        for scenario in scenario_order:
            sub = df[(df["classe"] == cls) & (df["scenario"] == scenario)].copy()
            if sub.empty:
                continue

            # Media por tempo usando todas as repeticoes existentes
            mean = sub.groupby(x, as_index=False)[y].mean().sort_values(x)

            label = f"{class_label(cls)} - {scenario_label(scenario)}"

            plt.plot(
                mean[x],
                mean[y],
                marker="o",
                linewidth=1.8,
                markersize=3,
                color=color_map.get(cls, None),
                linestyle=linestyle_map.get(scenario, "-"),
                label=label,
            )

    plt.title(title)
    plt.xlabel("Tempo na fase de trafego (s)")
    plt.ylabel(ylabel)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close()


def plot_time_split_scenarios_services(
    df: pd.DataFrame,
    x: str,
    y: str,
    title: str,
    ylabel: str,
    out: Path,
    classes_order: Optional[List[str]] = None,
) -> None:
    """
    Uma unica imagem com dois paineis:
      - esquerda: Baseline
      - direita: Com QoS

    As cores representam os servicos. Essa versao evita sobrepor Baseline e QoS
    no mesmo eixo, deixando a leitura por combinacao mais direta.
    """
    if df.empty:
        return

    if classes_order is None:
        classes_order = ["voz", "video", "bg"]

    color_map = {
        "voz": "tab:blue",
        "video": "tab:orange",
        "bg": "tab:green",
        "be": "tab:red",
    }

    scenario_order = [("baseline", "Baseline"), ("qos", "Com QoS")]
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.8), sharex=True, sharey=True)

    plotted_any = False
    for ax, (scenario, scenario_title) in zip(axes, scenario_order):
        sub_scenario = df[df["scenario"] == scenario].copy()
        ax.set_title(scenario_title)
        ax.set_xlabel("Tempo na fase de trafego (s)")
        ax.grid(True, alpha=0.3)

        for cls in classes_order:
            sub = sub_scenario[sub_scenario["classe"] == cls].copy()
            if sub.empty:
                continue

            mean = sub.groupby(x, as_index=False)[y].mean().sort_values(x)
            ax.plot(
                mean[x],
                mean[y],
                marker="o",
                linewidth=1.8,
                markersize=3,
                color=color_map.get(cls, None),
                label=class_label(cls),
            )
            plotted_any = True

        ax.legend(loc="best")

    if not plotted_any:
        plt.close(fig)
        return

    axes[0].set_ylabel(ylabel)
    fig.suptitle(title)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(out, dpi=160)
    plt.close(fig)


def plot_dscp_counts(df: pd.DataFrame, out: Path) -> None:
    if df.empty:
        return
    pivot = df.groupby(["scenario_label", "dscp"], as_index=False)["pacotes"].sum()
    labels = sorted(pivot["dscp"].unique())
    scenarios = list(pivot["scenario_label"].unique())

    x = np.arange(len(labels))
    width = 0.8 / max(len(scenarios), 1)

    plt.figure(figsize=(10, 6))
    for i, sc in enumerate(scenarios):
        vals = []
        for d in labels:
            s = pivot[(pivot["scenario_label"] == sc) & (pivot["dscp"] == d)]["pacotes"]
            vals.append(int(s.iloc[0]) if not s.empty else 0)
        plt.bar(x + i * width, vals, width=width, label=sc)

    plt.xticks(x + width * (len(scenarios) - 1) / 2, [str(d) for d in labels])
    plt.title("Contagem de pacotes por DSCP")
    plt.xlabel("DSCP")
    plt.ylabel("Pacotes")
    plt.grid(True, axis="y", alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close()


def plot_box_compare(df: pd.DataFrame, y: str, title: str, ylabel: str, out: Path, by_cols: Tuple[str, str] = ("scenario_label", "classe_label")) -> None:
    if df.empty:
        return

    labels = []
    data = []
    for keys, sub in df.groupby(list(by_cols)):
        labels.append(" / ".join(map(str, keys)))
        data.append(sub[y].dropna().values)

    if not data:
        return

    plt.figure(figsize=(12, 6))
    plt.boxplot(data, tick_labels=labels, showfliers=False)
    plt.title(title)
    plt.ylabel(ylabel)
    plt.xticks(rotation=30, ha="right")
    plt.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close()


def generate_plots(dirs: Dict[str, Path], udp: pd.DataFrame, tcp: pd.DataFrame, pcap_thr: pd.DataFrame, dscp: pd.DataFrame, overhead: pd.DataFrame) -> None:
    plots = dirs["plots"]
    plots_combo = plots / "por_combinacao"
    plots_combo_split = plots / "por_combinacao_lado_a_lado"
    plots_combo.mkdir(parents=True, exist_ok=True)
    plots_combo_split.mkdir(parents=True, exist_ok=True)

    for metric, cfg in METRICAS_UDP.items():
        for cls in CLASSES_UDP:
            sub = udp[(udp["metrica"] == metric) & (udp["classe"] == cls)]
            plot_time_compare(
                sub,
                x="tempo_s",
                y="valor",
                title=f"{cfg['label']} - {class_label(cls)}",
                ylabel=cfg["ylabel"],
                out=plots / f"udp_{metric}_{cls}_baseline_vs_qos.png",
            )

        # Novo plot: Voz + Video juntos no mesmo grafico temporal
        sub_all = udp[(udp["metrica"] == metric) & (udp["classe"].isin(CLASSES_UDP))]

        plot_time_compare_services(
            sub_all,
            x="tempo_s",
            y="valor",
            title=f"{cfg['label']} por tempo - Voz e Video | Baseline vs QoS",
            ylabel=cfg["ylabel"],
            out=plots / f"udp_{metric}_tempo_servicos_baseline_vs_qos.png",
            classes_order=["voz", "video"],
        )
        plot_time_split_scenarios_services(
            sub_all,
            x="tempo_s",
            y="valor",
            title=f"{cfg['label']} por tempo - Voz e Video",
            ylabel=cfg["ylabel"],
            out=plots / f"udp_{metric}_tempo_servicos_lado_a_lado.png",
            classes_order=["voz", "video"],
        )

        # Novo plot por combinacao: uma figura para cada bw/delay/loss
        if not sub_all.empty:
            for (bw, delay, loss_value), sub_combo in sub_all.groupby(["bw", "delay", "loss"]):
                plot_time_compare_services(
                    sub_combo,
                    x="tempo_s",
                    y="valor",
                    title=f"{cfg['label']} por tempo - Voz e Video | BW={bw} Mbps, Delay={delay} ms, Loss={loss_value}%",
                    ylabel=cfg["ylabel"],
                    out=plots_combo / f"{combo_slug(bw, delay, loss_value)}_udp_{metric}_tempo_servicos_baseline_vs_qos.png",
                    classes_order=["voz", "video"],
                )
                plot_time_split_scenarios_services(
                    sub_combo,
                    x="tempo_s",
                    y="valor",
                    title=f"{cfg['label']} por tempo - Voz e Video | BW={bw} Mbps, Delay={delay} ms, Loss={loss_value}%",
                    ylabel=cfg["ylabel"],
                    out=plots_combo_split / f"{combo_slug(bw, delay, loss_value)}_udp_{metric}_tempo_servicos_lado_a_lado.png",
                    classes_order=["voz", "video"],
                )

        # Boxplot geral ja existente
        plot_box_compare(
            sub_all,
            y="valor",
            title=f"Distribuicao - {cfg['label']}",
            ylabel=cfg["ylabel"],
            out=plots / f"box_udp_{metric}.png",
        )

    plot_time_compare(
        tcp,
        x="tempo_s",
        y="goodput_mbps",
        title="TCP Background - Goodput / Vazao util",
        ylabel="Goodput (Mbit/s)",
        out=plots / "tcp_bg_goodput_baseline_vs_qos.png",
    )
    plot_box_compare(
        tcp,
        y="goodput_mbps",
        title="Distribuicao - TCP Background Goodput",
        ylabel="Goodput (Mbit/s)",
        out=plots / "box_tcp_bg_goodput.png",
        by_cols=("scenario_label", "classe_label"),
    )

    bg_thr = pcap_thr[pcap_thr["classe"] == "bg"] if not pcap_thr.empty else pcap_thr
    plot_time_compare(
        bg_thr,
        x="tempo_s",
        y="throughput_mbps_pcap",
        title="TCP Background - Throughput observado no gargalo",
        ylabel="Throughput no gargalo (Mbit/s)",
        out=plots / "tcp_bg_throughput_pcap_baseline_vs_qos.png",
    )
        # Novo plot: throughput no gargalo para Voz + Video + Background
    if not pcap_thr.empty:
        plot_time_compare_services(
            pcap_thr,
            x="tempo_s",
            y="throughput_mbps_pcap",
            title="Throughput no gargalo por tempo - Voz, Video e Background | Baseline vs QoS",
            ylabel="Throughput no gargalo (Mbit/s)",
            out=plots / "throughput_gargalo_tempo_servicos_baseline_vs_qos.png",
            classes_order=["voz", "video", "bg"],
        )
        plot_time_split_scenarios_services(
            pcap_thr,
            x="tempo_s",
            y="throughput_mbps_pcap",
            title="Throughput no gargalo por tempo - Voz, Video e Background",
            ylabel="Throughput no gargalo (Mbit/s)",
            out=plots / "throughput_gargalo_tempo_servicos_lado_a_lado.png",
            classes_order=["voz", "video", "bg"],
        )

        # Novo plot por combinacao
        for (bw, delay, loss_value), sub_combo in pcap_thr.groupby(["bw", "delay", "loss"]):
            plot_time_compare_services(
                sub_combo,
                x="tempo_s",
                y="throughput_mbps_pcap",
                title=f"Throughput no gargalo por tempo | BW={bw} Mbps, Delay={delay} ms, Loss={loss_value}%",
                ylabel="Throughput no gargalo (Mbit/s)",
                out=plots_combo / f"{combo_slug(bw, delay, loss_value)}_throughput_gargalo_tempo_servicos_baseline_vs_qos.png",
                classes_order=["voz", "video", "bg"],
            )
            plot_time_split_scenarios_services(
                sub_combo,
                x="tempo_s",
                y="throughput_mbps_pcap",
                title=f"Throughput no gargalo por tempo | BW={bw} Mbps, Delay={delay} ms, Loss={loss_value}%",
                ylabel="Throughput no gargalo (Mbit/s)",
                out=plots_combo_split / f"{combo_slug(bw, delay, loss_value)}_throughput_gargalo_tempo_servicos_lado_a_lado.png",
                classes_order=["voz", "video", "bg"],
            )

    if not overhead.empty:
        plot_time_compare(
            overhead,
            x="tempo_s",
            y="overhead_mbps",
            title="TCP Background - Overhead estimado",
            ylabel="Overhead (Mbit/s)",
            out=plots / "tcp_bg_overhead_mbps_baseline_vs_qos.png",
        )
        plot_time_compare(
            overhead,
            x="tempo_s",
            y="overhead_percent",
            title="TCP Background - Overhead percentual estimado",
            ylabel="Overhead (%)",
            out=plots / "tcp_bg_overhead_percent_baseline_vs_qos.png",
        )

    plot_dscp_counts(dscp, plots / "dscp_counts_baseline_vs_qos.png")


# =============================================================================
# Overhead TCP
# =============================================================================

def compute_tcp_overhead(tcp: pd.DataFrame, pcap_thr: pd.DataFrame) -> pd.DataFrame:
    if tcp.empty or pcap_thr.empty:
        return pd.DataFrame()

    bg_pcap = pcap_thr[pcap_thr["classe"] == "bg"].copy()
    if bg_pcap.empty:
        return pd.DataFrame()

    cols_key = ["scenario", "rep", "bw", "delay", "loss", "tempo_s"]
    left = tcp.copy()
    left["tempo_s"] = np.floor(left["tempo_s"]).astype(int)
    right = bg_pcap.copy()
    right["tempo_s"] = np.floor(right["tempo_s"]).astype(int)

    merged = pd.merge(
        left,
        right[cols_key + ["throughput_mbps_pcap"]],
        on=cols_key,
        how="inner",
    )

    if merged.empty:
        return merged

    merged["overhead_mbps"] = merged["throughput_mbps_pcap"] - merged["goodput_mbps"]
    merged["overhead_mbps"] = merged["overhead_mbps"].clip(lower=0)
    merged["overhead_percent"] = np.where(
        merged["throughput_mbps_pcap"] > 0,
        (merged["overhead_mbps"] / merged["throughput_mbps_pcap"]) * 100.0,
        np.nan,
    )
    return merged


# =============================================================================
# Relatorio
# =============================================================================

def write_report(
    report_path: Path,
    base_dir: Path,
    udp: pd.DataFrame,
    tcp: pd.DataFrame,
    pcap_thr: pd.DataFrame,
    dscp: pd.DataFrame,
    overhead: pd.DataFrame,
    stats_udp: pd.DataFrame,
    stats_tcp: pd.DataFrame,
    stats_overhead: pd.DataFrame,
) -> None:
    lines = []
    lines.append("=" * 72)
    lines.append("RELATORIO DE ANALISE - TCC QoS SDN x Baseline")
    lines.append("=" * 72)
    lines.append(f"Base analisada: {base_dir}")
    lines.append("")

    lines.append("1) Arquivos carregados")
    lines.append(f"   UDP D-ITG linhas:       {len(udp)}")
    lines.append(f"   TCP iperf linhas:       {len(tcp)}")
    lines.append(f"   PCAP/TXT throughput:    {len(pcap_thr)}")
    lines.append(f"   DSCP contagens linhas:  {len(dscp)}")
    lines.append(f"   Overhead TCP linhas:    {len(overhead)}")
    lines.append("")

    lines.append("2) Criterio esperado")
    lines.append("   Baseline: pacotes podem estar marcados, mas sem set_queue/fila QoS.")
    lines.append("   QoS: DSCP deve conduzir Voz->Queue0, Video->Queue1, Background->Queue3.")
    lines.append("   Esta analise compara metricas resultantes por tempo e por cenario.")
    lines.append("")

    if not dscp.empty:
        lines.append("3) Contagem total por DSCP")
        d = dscp.groupby(["scenario_label", "dscp"], as_index=False)["pacotes"].sum()
        lines.append(d.to_string(index=False))
        lines.append("")

    if not stats_udp.empty:
        lines.append("4) Estatisticas UDP principais")
        show = stats_udp.copy()
        cols = [c for c in ["scenario_label", "classe_label", "metrica", "n", "media", "mediana", "p95", "p99", "minimo", "maximo"] if c in show.columns]
        lines.append(show[cols].to_string(index=False))
        lines.append("")

    if not stats_tcp.empty:
        lines.append("5) Estatisticas TCP Goodput")
        lines.append(stats_tcp.to_string(index=False))
        lines.append("")

    if not stats_overhead.empty:
        lines.append("6) Estatisticas TCP Overhead")
        lines.append(stats_overhead.to_string(index=False))
        lines.append("")

    lines.append("7) Plots gerados")
    lines.append("   Ver pasta plots/.")
    lines.append("")
    lines.append("Observacao metodologica:")
    lines.append("   Delay, jitter e perda UDP usam os .dat do D-ITG.")
    lines.append("   Goodput TCP usa o iperf3.")
    lines.append("   Throughput do gargalo usa frame.len no TXT exportado do pcap.")
    lines.append("   Overhead TCP = throughput observado no gargalo - goodput iperf3.")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Analise estatistica e plots do TCC QoS SDN x Baseline.")
    parser.add_argument("--base", default="/home/wifi/Documents/Benedito/dumbell", help="Diretorio base dos resultados.")
    parser.add_argument("--out", default=None, help="Diretorio de saida. Padrao: <base>/analise_tcc")
    parser.add_argument("--bw", type=int, default=None, help="Filtrar banda. Ex: 10")
    parser.add_argument("--delay", type=int, default=None, help="Filtrar delay. Ex: 0")
    parser.add_argument("--loss", type=float, default=None, help="Filtrar loss. Ex: 0.0")
    args = parser.parse_args()

    base_dir = Path(args.base)
    out_dir = Path(args.out) if args.out else base_dir / "analise_tcc"
    dirs = ensure_dirs(out_dir)

    print("=" * 72)
    print("ANALISE TCC - QoS SDN x Baseline")
    print("=" * 72)
    print(f"Base: {base_dir}")
    print(f"Saida: {out_dir}")
    print(f"Filtro: bw={args.bw}, delay={args.delay}, loss={args.loss}")
    print("")

    udp_all = []
    tcp_all = []
    dscp_all = []
    pcap_all = []

    for scenario in ["baseline", "qos"]:
        udp_all.append(collect_udp_metrics(base_dir, scenario, args.bw, args.delay, args.loss))
        tcp_all.append(collect_tcp_goodput(base_dir, scenario, args.bw, args.delay, args.loss))
        dscp_all.append(collect_dscp_counts(base_dir, scenario, args.bw, args.delay, args.loss))
        pcap_all.append(collect_pcap_throughput(base_dir, scenario, args.bw, args.delay, args.loss))

    udp = pd.concat([x for x in udp_all if not x.empty], ignore_index=True) if any(not x.empty for x in udp_all) else pd.DataFrame()
    tcp = pd.concat([x for x in tcp_all if not x.empty], ignore_index=True) if any(not x.empty for x in tcp_all) else pd.DataFrame()
    dscp = pd.concat([x for x in dscp_all if not x.empty], ignore_index=True) if any(not x.empty for x in dscp_all) else pd.DataFrame()
    pcap_thr = pd.concat([x for x in pcap_all if not x.empty], ignore_index=True) if any(not x.empty for x in pcap_all) else pd.DataFrame()

    overhead = compute_tcp_overhead(tcp, pcap_thr)

    if not udp.empty:
        udp.to_csv(dirs["tables"] / "series_udp_ditg.csv", index=False)
        udp_resumo_tempo = (
            udp.groupby(["scenario", "scenario_label", "classe", "classe_label", "metrica", "bw", "delay", "loss", "tempo_s"], as_index=False)["valor"]
            .mean()
        )
        udp_resumo_tempo.to_csv(dirs["tables"] / "series_udp_resumo_tempo.csv", index=False)    
    if not tcp.empty:
        tcp.to_csv(dirs["tables"] / "series_tcp_goodput_iperf.csv", index=False)
    if not pcap_thr.empty:
        pcap_thr.to_csv(dirs["tables"] / "series_throughput_pcap.csv", index=False)
        pcap_thr_resumo_tempo = (
            pcap_thr.groupby(["scenario", "scenario_label", "classe", "classe_label", "bw", "delay", "loss", "tempo_s"], as_index=False)["throughput_mbps_pcap"]
            .mean()
        )
        pcap_thr_resumo_tempo.to_csv(dirs["tables"] / "series_throughput_pcap_resumo_tempo.csv", index=False)
    if not dscp.empty:
        dscp.to_csv(dirs["tables"] / "contagem_dscp.csv", index=False)
    if not overhead.empty:
        overhead.to_csv(dirs["tables"] / "series_tcp_overhead.csv", index=False)

    stats_udp = summary_stats(
        udp,
        value_col="valor",
        group_cols=["scenario_label", "classe_label", "metrica", "bw", "delay", "loss"],
    ) if not udp.empty else pd.DataFrame()

    stats_tcp = summary_stats(
        tcp,
        value_col="goodput_mbps",
        group_cols=["scenario_label", "classe_label", "bw", "delay", "loss"],
    ) if not tcp.empty else pd.DataFrame()

    stats_overhead = summary_stats(
        overhead,
        value_col="overhead_percent",
        group_cols=["scenario_label", "classe_label", "bw", "delay", "loss"],
    ) if not overhead.empty else pd.DataFrame()

    if not stats_udp.empty:
        stats_udp.to_csv(dirs["tables"] / "estatisticas_udp.csv", index=False)
    if not stats_tcp.empty:
        stats_tcp.to_csv(dirs["tables"] / "estatisticas_tcp_goodput.csv", index=False)
    if not stats_overhead.empty:
        stats_overhead.to_csv(dirs["tables"] / "estatisticas_tcp_overhead_percent.csv", index=False)

    generate_plots(dirs, udp, tcp, pcap_thr, dscp, overhead)

    report_path = dirs["reports"] / "relatorio_analise_tcc.txt"
    write_report(report_path, base_dir, udp, tcp, pcap_thr, dscp, overhead, stats_udp, stats_tcp, stats_overhead)

    print("Arquivos gerados:")
    print(f"  Tabelas:    {dirs['tables']}")
    print(f"  Plots:      {dirs['plots']}")
    print(f"  Relatorio:  {report_path}")
    print("")
    print("Principais plots esperados:")
    for p in sorted(dirs["plots"].glob("*.png")):
        print(f"  - {p.name}")
    print("")
    print("Concluido.")


if __name__ == "__main__":
    main()
