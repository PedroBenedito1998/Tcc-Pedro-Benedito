#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditoria_pos_teste_tcc.py

Auditoria POS-TESTE para experimento TCC Baseline x QoS SDN.
Nao precisa rodar durante o Mininet. Analisa arquivos finais ja salvos.

Verifica:
- Cobertura da matriz: bw, delay, loss, rep, baseline/qos.
- Arquivos obrigatorios por rodada.
- TXT/pcap exportado: DSCP por fluxo, fluxos esperados, pacotes sem marcacao.
- D-ITG UDP: delay, jitter, loss, bitrate para voz/video por tempo.
- iperf3 TCP: goodput/vazao util para background.
- Logs do Ryu, D-ITG e iperf.
- Comparacao estatistica Baseline vs QoS por combinacao.
- Possiveis falhas, anomalias, outliers e execucoes incompletas.

Saida:
  <base>/auditoria_pos_teste/{relatorios,tabelas,plots}

Exemplos:
  python3 auditoria_pos_teste_tcc.py --base /home/wifi/Documents/Benedito/dumbell --bws 10 --delays 0 --losses 0.0 --reps 1
  python3 auditoria_pos_teste_tcc.py --base /home/wifi/Documents/Benedito/dumbell --reps 30
"""

import argparse
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


SCENARIOS = {
    "baseline": {
        "label": "Baseline",
        "root": "rodadas_baseline",
        "folder_prefix": "SemQoS",
        "txt_prefix": "baseline",
        "expect_set_queue": False,
        "expect_qos_ovs": False,
    },
    "qos": {
        "label": "Com QoS",
        "root": "rodadas_qos",
        "folder_prefix": "ComQoS",
        "txt_prefix": "qos",
        "expect_set_queue": None,   # nao validavel pos-teste sem auditoria ao vivo
        "expect_qos_ovs": None,
    },
}

UDP_CLASSES = ["voz", "video"]
TCP_CLASSES = ["bg"]
METRICS_UDP = ["delay", "jitter", "bitrate", "loss"]

FLOW_EXPECTED = {
    "voz":   {"src": "20.0.0.1", "dst": "10.0.0.1", "proto": 17, "dscp": 46, "name": "Voz UDP"},
    "video": {"src": "20.0.0.2", "dst": "10.0.0.2", "proto": 17, "dscp": 34, "name": "Video UDP"},
    "bg":    {"src": "20.0.0.3", "dst": "10.0.0.3", "proto": 6,  "dscp": 8,  "name": "Background TCP"},
}

UDP_VALUE_UNITS = {
    "delay": "ms",
    "jitter": "ms",
    "bitrate": "D-ITG",
    "loss": "pacotes",
}


@dataclass
class Combo:
    bw: int
    delay: int
    loss: str
    rep: int

    @property
    def suffix(self) -> str:
        return f"bw{self.bw}_del{self.delay}_loss{self.loss}_rep{self.rep}"


def parse_list_str(s: str, cast=str) -> List:
    out = []
    for part in str(s).split(','):
        part = part.strip()
        if part == '':
            continue
        out.append(cast(part))
    return out


def loss_cast(x: str) -> str:
    # Mantem formato 0.0, 0.1, 1.0, 3.0 exatamente como pastas/scripts.
    return str(x).strip()


def ensure_out(out_dir: Path) -> Dict[str, Path]:
    dirs = {
        "root": out_dir,
        "tables": out_dir / "tabelas",
        "reports": out_dir / "relatorios",
        "plots": out_dir / "plots",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def scenario_folder(base: Path, scenario: str, bw: int, delay: int, loss: str) -> Path:
    cfg = SCENARIOS[scenario]
    return base / cfg["root"] / f"{cfg['folder_prefix']}Banda{bw}MbpsLoss{loss}Delay{delay}"


def issue(issues: List[Dict], severity: str, scenario: str, combo: Optional[Combo], category: str, message: str, file: Optional[Path] = None):
    issues.append({
        "severity": severity,
        "scenario": scenario,
        "bw": combo.bw if combo else None,
        "delay": combo.delay if combo else None,
        "loss": combo.loss if combo else None,
        "rep": combo.rep if combo else None,
        "category": category,
        "message": message,
        "file": str(file) if file else "",
    })


def file_info(path: Path, required: bool, scenario: str, combo: Combo, kind: str) -> Dict:
    exists = path.exists()
    size = path.stat().st_size if exists else 0
    return {
        "scenario": scenario,
        "bw": combo.bw,
        "delay": combo.delay,
        "loss": combo.loss,
        "rep": combo.rep,
        "kind": kind,
        "path": str(path),
        "required": required,
        "exists": exists,
        "size_bytes": size,
    }


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(errors="ignore")
    except Exception:
        return ""


# =============================================================================
# Parsers
# =============================================================================

def read_tshark_txt(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()

    # Testa separadores reais e literal /t.
    for sep in ['\t', '/t', r'\s{2,}|\s+']:
        try:
            df = pd.read_csv(path, sep=sep, engine='python', quotechar='"')
            if df.shape[1] >= 6:
                break
        except Exception:
            df = pd.DataFrame()
    else:
        return pd.DataFrame()

    if df.empty:
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
            df[col] = df[col].astype(str).str.replace('"', '', regex=False).str.strip()

    for col in ["frame.number", "frame.time_relative", "ip.proto", "ip.dsfield.dscp", "tcp.dstport", "udp.dstport", "frame.len"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    return df


def read_itg_dat(path: Path, metric: str) -> pd.DataFrame:
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
    out = pd.DataFrame({"tempo_s": tempo, "valor_raw": valor}).dropna()
    if metric in ["delay", "jitter"]:
        out["valor"] = out["valor_raw"] * 1000.0
        out["unidade"] = "ms"
    else:
        out["valor"] = out["valor_raw"]
        out["unidade"] = UDP_VALUE_UNITS.get(metric, "")
    return out


IPERF_INTERVAL_RE = re.compile(
    r"\[\s*\d+\]\s+"
    r"(?P<t0>\d+(?:\.\d+)?)-(?P<t1>\d+(?:\.\d+)?)\s+sec\s+"
    r"(?P<transfer>[\d.]+)\s+(?P<transfer_unit>[KMG]?Bytes)\s+"
    r"(?P<bitrate>[\d.]+)\s+(?P<br_unit>[KMG]?bits/sec)"
)


def bitrate_to_mbps(v: float, unit: str) -> float:
    if unit == "bits/sec":
        return v / 1e6
    if unit == "Kbits/sec":
        return v / 1e3
    if unit == "Mbits/sec":
        return v
    if unit == "Gbits/sec":
        return v * 1000.0
    return v


def read_iperf_log(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=["tempo_s", "goodput_mbps"])
    rows = []
    for line in read_text_file(path).splitlines():
        if "sender" in line or "receiver" in line:
            continue
        m = IPERF_INTERVAL_RE.search(line)
        if not m:
            continue
        rows.append({
            "tempo_s": float(m.group("t0")),
            "tempo_fim_s": float(m.group("t1")),
            "goodput_mbps": bitrate_to_mbps(float(m.group("bitrate")), m.group("br_unit")),
        })
    return pd.DataFrame(rows)


def summary_stats(values: pd.Series) -> Dict[str, float]:
    v = pd.to_numeric(values, errors="coerce").dropna()
    if len(v) == 0:
        return {"n": 0, "media": np.nan, "mediana": np.nan, "std": np.nan, "min": np.nan, "max": np.nan, "p95": np.nan, "p99": np.nan}
    return {
        "n": int(len(v)),
        "media": float(v.mean()),
        "mediana": float(v.median()),
        "std": float(v.std(ddof=1)) if len(v) > 1 else 0.0,
        "min": float(v.min()),
        "max": float(v.max()),
        "p95": float(v.quantile(0.95)),
        "p99": float(v.quantile(0.99)),
    }


# =============================================================================
# Auditoria por rodada
# =============================================================================

def required_files(folder: Path, scenario: str, combo: Combo) -> List[Tuple[str, Path, bool]]:
    cfg = SCENARIOS[scenario]
    pfx = cfg["txt_prefix"]
    s = combo.suffix
    files = []
    files.append(("txt_pcap_export", folder / f"{pfx}_{s}.txt", True))
    files.append(("udp_summary_voz", folder / f"iperf_voz_{s}.log", True))
    files.append(("udp_summary_video", folder / f"iperf_video_{s}.log", True))
    files.append(("tcp_iperf_bg", folder / f"iperf_bg_{s}.log", True))
    for cls in UDP_CLASSES:
        for metric in METRICS_UDP:
            files.append((f"udp_dat_{metric}_{cls}", folder / f"{metric}_{cls}_{s}.dat", True))
    files.append(("ping_voz", folder / f"ping_voz_{s}.log", True))
    files.append(("ping_video", folder / f"ping_video_{s}.log", True))
    files.append(("ping_bg", folder / f"ping_bg_{s}.log", True))
    files.append(("ryu_log", folder / f"ryu_{s}.log", False))
    return files


def audit_round(base: Path, scenario: str, combo: Combo, issues: List[Dict]) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict]]:
    inventory = []
    dscp_rows = []
    udp_metric_rows = []
    tcp_metric_rows = []

    folder = scenario_folder(base, scenario, combo.bw, combo.delay, combo.loss)
    if not folder.exists():
        issue(issues, "ERROR", scenario, combo, "folder_missing", "Pasta da combinacao nao existe", folder)
        return inventory, dscp_rows, udp_metric_rows, tcp_metric_rows

    # Arquivos esperados.
    for kind, path, required in required_files(folder, scenario, combo):
        info = file_info(path, required, scenario, combo, kind)
        inventory.append(info)
        if required and not info["exists"]:
            issue(issues, "ERROR", scenario, combo, "file_missing", f"Arquivo obrigatorio ausente: {kind}", path)
        elif required and info["size_bytes"] == 0:
            issue(issues, "ERROR", scenario, combo, "file_empty", f"Arquivo obrigatorio vazio: {kind}", path)

    s = combo.suffix
    cfg = SCENARIOS[scenario]
    txt_path = folder / f"{cfg['txt_prefix']}_{s}.txt"

    # TXT/DSCP/fluxos.
    txt_df = read_tshark_txt(txt_path)
    if txt_path.exists():
        if txt_df.empty:
            issue(issues, "ERROR", scenario, combo, "txt_parse", "TXT existe, mas nao foi possivel parsear", txt_path)
        else:
            if len(txt_df) < 100:
                issue(issues, "WARN", scenario, combo, "txt_small", f"TXT tem poucas linhas: {len(txt_df)}", txt_path)

            if "ip.dsfield.dscp" in txt_df.columns:
                counts = txt_df["ip.dsfield.dscp"].dropna().astype(int).value_counts().sort_index()
                for dscp, cnt in counts.items():
                    dscp_rows.append({
                        "scenario": scenario, "bw": combo.bw, "delay": combo.delay, "loss": combo.loss, "rep": combo.rep,
                        "dscp": int(dscp), "pacotes": int(cnt), "txt": str(txt_path)
                    })

            required_cols = {"ip.src", "ip.dst", "ip.proto", "ip.dsfield.dscp", "frame.len"}
            if required_cols.issubset(txt_df.columns):
                for cls, fl in FLOW_EXPECTED.items():
                    mask = (
                        (txt_df["ip.src"].astype(str) == fl["src"]) &
                        (txt_df["ip.dst"].astype(str) == fl["dst"]) &
                        (txt_df["ip.proto"] == fl["proto"])
                    )
                    sub = txt_df.loc[mask]
                    if sub.empty:
                        issue(issues, "ERROR", scenario, combo, "flow_missing", f"Fluxo esperado ausente no TXT: {fl['name']} {fl['src']}->{fl['dst']}", txt_path)
                        continue
                    ok = sub[sub["ip.dsfield.dscp"] == fl["dscp"]]
                    bad0 = sub[sub["ip.dsfield.dscp"] == 0]
                    if ok.empty:
                        issue(issues, "ERROR", scenario, combo, "dscp_missing", f"Fluxo {fl['name']} existe, mas nao apareceu com DSCP {fl['dscp']}", txt_path)
                    if fl["dscp"] != 0 and not bad0.empty:
                        # Pequenos pacotes de controle podem existir, mas para UDP pesado nao deve ser alto.
                        ratio = len(bad0) / max(len(sub), 1)
                        sev = "ERROR" if ratio > 0.05 else "WARN"
                        issue(issues, sev, scenario, combo, "dscp_zero", f"Fluxo {fl['name']} teve {len(bad0)}/{len(sub)} pacotes DSCP 0 ({ratio:.1%})", txt_path)
            else:
                issue(issues, "ERROR", scenario, combo, "txt_columns", "TXT sem colunas necessarias para validar fluxos DSCP", txt_path)

    # D-ITG dat UDP.
    for cls in UDP_CLASSES:
        for metric in METRICS_UDP:
            path = folder / f"{metric}_{cls}_{s}.dat"
            dat = read_itg_dat(path, metric)
            if dat.empty:
                continue
            if len(dat) < 20 or len(dat) > 40:
                issue(issues, "WARN", scenario, combo, "dat_rows", f"{metric}_{cls} tem {len(dat)} linhas; esperado aproximadamente 30", path)
            if (dat["valor"] < 0).any():
                issue(issues, "ERROR", scenario, combo, "dat_negative", f"{metric}_{cls} contem valor negativo", path)
            st = summary_stats(dat["valor"])
            udp_metric_rows.append({
                "scenario": scenario, "bw": combo.bw, "delay": combo.delay, "loss": combo.loss, "rep": combo.rep,
                "classe": cls, "metric": metric, "arquivo": str(path), **st
            })
            # Sinais fortes de erro.
            if metric in ["delay", "jitter"] and st["max"] > 60000:
                issue(issues, "WARN", scenario, combo, "metric_extreme", f"{metric}_{cls} max muito alto: {st['max']:.3f} ms", path)
            if metric == "bitrate" and st["media"] <= 0:
                issue(issues, "ERROR", scenario, combo, "metric_zero", f"bitrate_{cls} media <= 0", path)

    # Logs UDP sumario.
    for cls, porta in [("voz", 5101), ("video", 5102)]:
        log_path = folder / f"iperf_{cls}_{s}.log"
        text = read_text_file(log_path)
        if log_path.exists() and log_path.stat().st_size > 0:
            if "Average delay" not in text or "Average jitter" not in text:
                issue(issues, "WARN", scenario, combo, "ditg_summary", f"Resumo D-ITG {cls} sem Average delay/jitter", log_path)
            if re.search(r"Error lines\s+=\s+([1-9]\d*)", text):
                issue(issues, "ERROR", scenario, combo, "ditg_error_lines", f"Resumo D-ITG {cls} indica Error lines > 0", log_path)

    # TCP iperf.
    iperf_path = folder / f"iperf_bg_{s}.log"
    ipdf = read_iperf_log(iperf_path)
    if iperf_path.exists() and iperf_path.stat().st_size > 0:
        iptext = read_text_file(iperf_path)
        if "iperf Done" not in iptext:
            issue(issues, "WARN", scenario, combo, "iperf_incomplete", "iperf_bg nao contem 'iperf Done'", iperf_path)
        if ipdf.empty:
            issue(issues, "ERROR", scenario, combo, "iperf_parse", "Nao foi possivel extrair serie temporal do iperf_bg", iperf_path)
        else:
            if len(ipdf) < 20:
                issue(issues, "WARN", scenario, combo, "iperf_rows", f"iperf_bg tem poucas linhas temporais: {len(ipdf)}", iperf_path)

            zeros = int((ipdf["goodput_mbps"] <= 0).sum())
            positivos = int((ipdf["goodput_mbps"] > 0).sum())
            total = int(len(ipdf))
            iperf_done = "iperf Done" in iptext

            if zeros == total and iperf_done:
                issue(
                    issues,
                    "WARN",
                    scenario,
                    combo,
                    "tcp_starvation",
                    f"iperf_bg terminou com iperf Done, mas todos os {total} intervalos tiveram goodput zero. Interpretar como starvation/colapso TCP, nao como falha de execucao.",
                    iperf_path
                )
            elif zeros == total and not iperf_done:
                issue(
                    issues,
                    "ERROR",
                    scenario,
                    combo,
                    "iperf_zero",
                    f"iperf_bg teve todos os {total} intervalos com goodput zero e nao confirmou iperf Done",
                    iperf_path
                )
            elif zeros > 0:
                issue(
                    issues,
                    "WARN",
                    scenario,
                    combo,
                    "iperf_zero_partial",
                    f"iperf_bg teve {zeros}/{total} intervalos com goodput zero e {positivos}/{total} positivos",
                    iperf_path
                )

            if ipdf["goodput_mbps"].max() > combo.bw * 1.30:
                issue(issues, "WARN", scenario, combo, "iperf_above_bw", f"iperf_bg max {ipdf['goodput_mbps'].max():.2f} Mbps acima do BW {combo.bw}", iperf_path)
            st = summary_stats(ipdf["goodput_mbps"])
            tcp_metric_rows.append({
                "scenario": scenario, "bw": combo.bw, "delay": combo.delay, "loss": combo.loss, "rep": combo.rep,
                "classe": "bg", "metric": "goodput_mbps", "arquivo": str(iperf_path), **st
            })

    # Ryu log copiado.
    ryu_path = folder / f"ryu_{s}.log"
    if ryu_path.exists() and ryu_path.stat().st_size > 0:
        txt = read_text_file(ryu_path)
        bad = re.findall(r"(?i)(error|exception|traceback|failed|bad request|warn)", txt)
        if bad:
            issue(issues, "WARN", scenario, combo, "ryu_log", f"ryu log contem termos suspeitos: {sorted(set([b.lower() for b in bad]))}", ryu_path)

    return inventory, dscp_rows, udp_metric_rows, tcp_metric_rows


# =============================================================================
# Cross-checks e relatorio
# =============================================================================

def add_cross_scenario_checks(udp_stats: pd.DataFrame, tcp_stats: pd.DataFrame, issues: List[Dict]):
    if udp_stats.empty:
        return
    keys = ["bw", "delay", "loss", "rep", "classe", "metric"]
    piv = udp_stats.pivot_table(index=keys, columns="scenario", values="media", aggfunc="mean").reset_index()
    for _, r in piv.iterrows():
        if "baseline" not in r or "qos" not in r or pd.isna(r.get("baseline")) or pd.isna(r.get("qos")):
            continue
        combo = Combo(int(r["bw"]), int(r["delay"]), str(r["loss"]), int(r["rep"]))
        cls = r["classe"]
        metric = r["metric"]
        base_v = float(r["baseline"])
        qos_v = float(r["qos"])
        # Para delay/jitter/loss, espera QoS menor ou igual. Como rede pode variar, usa WARN.
        if metric in ["delay", "jitter", "loss"] and qos_v > base_v * 1.10 and qos_v - base_v > 0.1:
            issue(issues, "WARN", "compare", combo, "qos_not_better", f"QoS {metric}_{cls} maior que baseline: qos={qos_v:.4f}, baseline={base_v:.4f}")
        if metric == "bitrate" and qos_v < base_v * 0.70:
            issue(issues, "WARN", "compare", combo, "qos_bitrate_low", f"QoS bitrate_{cls} muito menor que baseline: qos={qos_v:.4f}, baseline={base_v:.4f}")

    if not tcp_stats.empty:
        piv2 = tcp_stats.pivot_table(index=["bw", "delay", "loss", "rep", "classe", "metric"], columns="scenario", values="media", aggfunc="mean").reset_index()
        for _, r in piv2.iterrows():
            if "baseline" not in r or "qos" not in r or pd.isna(r.get("baseline")) or pd.isna(r.get("qos")):
                continue
            combo = Combo(int(r["bw"]), int(r["delay"]), str(r["loss"]), int(r["rep"]))
            # Background menor no QoS e esperado, mas se QoS zerar é erro.
            if float(r["qos"]) <= 0:
                issue(issues, "ERROR", "compare", combo, "qos_tcp_zero", "QoS TCP background com media <= 0")


def build_coverage(expected: List[Tuple[str, Combo]], inventory: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario, combo in expected:
        inv = inventory[(inventory["scenario"] == scenario) & (inventory["bw"] == combo.bw) & (inventory["delay"] == combo.delay) & (inventory["loss"] == combo.loss) & (inventory["rep"] == combo.rep)]
        req = inv[inv["required"] == True]
        rows.append({
            "scenario": scenario,
            "bw": combo.bw,
            "delay": combo.delay,
            "loss": combo.loss,
            "rep": combo.rep,
            "required_files": int(len(req)),
            "existing_required_files": int(req["exists"].sum()) if not req.empty else 0,
            "empty_required_files": int(((req["exists"] == True) & (req["size_bytes"] == 0)).sum()) if not req.empty else 0,
            "complete": bool((not req.empty) and req["exists"].all() and (req["size_bytes"] > 0).all()),
        })
    return pd.DataFrame(rows)


def plot_issue_counts(issues: pd.DataFrame, out: Path):
    if issues.empty:
        return
    c = issues.groupby(["severity", "category"], as_index=False).size()
    if c.empty:
        return
    labels = c["severity"] + " / " + c["category"]
    plt.figure(figsize=(12, 7))
    plt.barh(labels, c["size"])
    plt.xlabel("Ocorrencias")
    plt.title("Auditoria pos-teste - problemas encontrados")
    plt.grid(True, axis="x", alpha=0.3)
    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close()


def plot_metric_compare(udp_stats: pd.DataFrame, tcp_stats: pd.DataFrame, out_dir: Path):
    if not udp_stats.empty:
        for metric in METRICS_UDP:
            sub = udp_stats[udp_stats["metric"] == metric]
            if sub.empty:
                continue
            for cls in UDP_CLASSES:
                s2 = sub[sub["classe"] == cls]
                if s2.empty:
                    continue
                labels = []
                data = []
                for sc in ["baseline", "qos"]:
                    vals = s2[s2["scenario"] == sc]["media"].dropna().values
                    if len(vals) > 0:
                        labels.append(SCENARIOS[sc]["label"])
                        data.append(vals)
                if not data:
                    continue
                plt.figure(figsize=(8, 5))
                plt.boxplot(data, labels=labels, showfliers=False)
                plt.ylabel(f"{metric} ({UDP_VALUE_UNITS.get(metric, '')})")
                plt.title(f"{metric} {cls} - distribuicao por rodada/combinacao")
                plt.grid(True, axis="y", alpha=0.3)
                plt.tight_layout()
                plt.savefig(out_dir / f"audit_box_{metric}_{cls}.png", dpi=160)
                plt.close()

    if not tcp_stats.empty:
        labels = []
        data = []
        for sc in ["baseline", "qos"]:
            vals = tcp_stats[tcp_stats["scenario"] == sc]["media"].dropna().values
            if len(vals) > 0:
                labels.append(SCENARIOS[sc]["label"])
                data.append(vals)
        if data:
            plt.figure(figsize=(8, 5))
            plt.boxplot(data, labels=labels, showfliers=False)
            plt.ylabel("Goodput TCP medio (Mbit/s)")
            plt.title("TCP Background - goodput por rodada/combinacao")
            plt.grid(True, axis="y", alpha=0.3)
            plt.tight_layout()
            plt.savefig(out_dir / "audit_box_tcp_bg_goodput.png", dpi=160)
            plt.close()


def write_report(path: Path, args, coverage: pd.DataFrame, issues: pd.DataFrame, inventory: pd.DataFrame, dscp: pd.DataFrame, udp: pd.DataFrame, tcp: pd.DataFrame):
    lines = []
    lines.append("=" * 80)
    lines.append("AUDITORIA POS-TESTE - TCC Baseline x QoS")
    lines.append("=" * 80)
    lines.append(f"Base: {args.base}")
    lines.append(f"BWs: {args.bws}")
    lines.append(f"Delays: {args.delays}")
    lines.append(f"Losses: {args.losses}")
    lines.append(f"Repeticoes esperadas: {args.reps}")
    lines.append("")

    total_expected = len(coverage)
    complete = int(coverage["complete"].sum()) if not coverage.empty else 0
    lines.append("1) COBERTURA DA MATRIZ")
    lines.append(f"   Rodadas esperadas: {total_expected}")
    lines.append(f"   Rodadas completas: {complete}")
    lines.append(f"   Rodadas incompletas: {total_expected - complete}")
    lines.append("")
    if not coverage.empty:
        cov_sum = coverage.groupby("scenario", as_index=False).agg(esperadas=("complete", "count"), completas=("complete", "sum"), vazias=("empty_required_files", "sum"))
        lines.append(cov_sum.to_string(index=False))
        lines.append("")

    lines.append("2) PROBLEMAS ENCONTRADOS")
    if issues.empty:
        lines.append("   Nenhum problema encontrado.")
    else:
        lines.append(issues.groupby(["severity", "category"], as_index=False).size().to_string(index=False))
        lines.append("")
        lines.append("   Primeiros problemas:")
        cols = ["severity", "scenario", "bw", "delay", "loss", "rep", "category", "message", "file"]
        lines.append(issues[cols].head(80).to_string(index=False))
    lines.append("")

    lines.append("3) INVENTARIO DE ARQUIVOS")
    if inventory.empty:
        lines.append("   Inventario vazio.")
    else:
        inv_sum = inventory.groupby(["scenario", "kind"], as_index=False).agg(total=("path", "count"), existentes=("exists", "sum"), bytes_media=("size_bytes", "mean"))
        lines.append(inv_sum.to_string(index=False))
    lines.append("")

    lines.append("4) CONTAGEM DSCP")
    if dscp.empty:
        lines.append("   Nenhuma contagem DSCP carregada.")
    else:
        dsum = dscp.groupby(["scenario", "dscp"], as_index=False)["pacotes"].sum()
        lines.append(dsum.to_string(index=False))
    lines.append("")

    lines.append("5) ESTATISTICAS UDP POR CENARIO")
    if udp.empty:
        lines.append("   Sem metricas UDP.")
    else:
        usum = udp.groupby(["scenario", "classe", "metric"], as_index=False).agg(n=("media", "count"), media_das_medias=("media", "mean"), mediana_das_medias=("media", "median"), max_das_medias=("media", "max"))
        lines.append(usum.to_string(index=False))
    lines.append("")

    lines.append("6) ESTATISTICAS TCP BACKGROUND")
    if tcp.empty:
        lines.append("   Sem metricas TCP.")
    else:
        tsum = tcp.groupby(["scenario", "classe", "metric"], as_index=False).agg(n=("media", "count"), media_das_medias=("media", "mean"), mediana_das_medias=("media", "median"), max_das_medias=("media", "max"))
        lines.append(tsum.to_string(index=False))
    lines.append("")

    lines.append("7) VEREDITO")
    n_err = int((issues["severity"] == "ERROR").sum()) if not issues.empty else 0
    n_warn = int((issues["severity"] == "WARN").sum()) if not issues.empty else 0
    if total_expected > 0 and complete == total_expected and n_err == 0:
        lines.append("   RESULTADO: APTO PARA PROSSEGUIR.")
        if n_warn > 0:
            lines.append(f"   Ha {n_warn} avisos. Revise antes da rodada final longa.")
        else:
            lines.append("   Nao ha erros nem avisos relevantes detectados.")
    else:
        lines.append("   RESULTADO: NAO RODE A MATRIZ DE DIAS AINDA.")
        lines.append(f"   Erros: {n_err} | Avisos: {n_warn} | Rodadas incompletas: {total_expected - complete}")
    lines.append("")
    lines.append("Arquivos detalhados em tabelas/*.csv e plots/*.png")

    path.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# Main
# =============================================================================

def main():
    ap = argparse.ArgumentParser(description="Auditoria pos-teste completa para TCC Baseline x QoS.")
    ap.add_argument("--base", default="/home/wifi/Documents/Benedito/dumbell")
    ap.add_argument("--out", default=None)
    ap.add_argument("--bws", default="10,100,1000")
    ap.add_argument("--delays", default="0,50,100,300")
    ap.add_argument("--losses", default="0.0,0.1,1.0,3.0")
    ap.add_argument("--reps", type=int, default=1, help="Repeticoes esperadas por combinacao. Use 1 no teste curto, 30 no final.")
    args = ap.parse_args()

    base = Path(args.base)
    out = Path(args.out) if args.out else base / "auditoria_pos_teste"
    dirs = ensure_out(out)

    bws = parse_list_str(args.bws, int)
    delays = parse_list_str(args.delays, int)
    losses = parse_list_str(args.losses, loss_cast)

    expected = []
    for scenario in ["baseline", "qos"]:
        for bw in bws:
            for delay in delays:
                for loss in losses:
                    for rep in range(1, args.reps + 1):
                        expected.append((scenario, Combo(bw, delay, loss, rep)))

    issues: List[Dict] = []
    all_inventory = []
    all_dscp = []
    all_udp = []
    all_tcp = []

    print("=" * 80)
    print("AUDITORIA POS-TESTE TCC")
    print("=" * 80)
    print(f"Base: {base}")
    print(f"Saida: {out}")
    print(f"Rodadas esperadas: {len(expected)}")
    print("")

    for idx, (scenario, combo) in enumerate(expected, start=1):
        if idx % 20 == 0 or idx == 1:
            print(f"Auditando {idx}/{len(expected)}...")
        inv, dscp, udp, tcp = audit_round(base, scenario, combo, issues)
        all_inventory.extend(inv)
        all_dscp.extend(dscp)
        all_udp.extend(udp)
        all_tcp.extend(tcp)

    inventory_df = pd.DataFrame(all_inventory)
    dscp_df = pd.DataFrame(all_dscp)
    udp_df = pd.DataFrame(all_udp)
    tcp_df = pd.DataFrame(all_tcp)

    add_cross_scenario_checks(udp_df, tcp_df, issues)
    issues_df = pd.DataFrame(issues)
    coverage_df = build_coverage(expected, inventory_df) if not inventory_df.empty else pd.DataFrame()

    # Salva CSVs.
    coverage_df.to_csv(dirs["tables"] / "cobertura_matriz.csv", index=False)
    inventory_df.to_csv(dirs["tables"] / "inventario_arquivos.csv", index=False)
    dscp_df.to_csv(dirs["tables"] / "contagem_dscp_por_rodada.csv", index=False)
    udp_df.to_csv(dirs["tables"] / "estatisticas_udp_por_rodada.csv", index=False)
    tcp_df.to_csv(dirs["tables"] / "estatisticas_tcp_por_rodada.csv", index=False)
    issues_df.to_csv(dirs["tables"] / "problemas_detectados.csv", index=False)

    # Plots.
    plot_issue_counts(issues_df, dirs["plots"] / "problemas_detectados.png")
    plot_metric_compare(udp_df, tcp_df, dirs["plots"])

    report_path = dirs["reports"] / "relatorio_auditoria_pos_teste.txt"
    write_report(report_path, args, coverage_df, issues_df, inventory_df, dscp_df, udp_df, tcp_df)

    print("")
    print("=" * 80)
    print("AUDITORIA FINALIZADA")
    print("=" * 80)
    print(f"Relatorio: {report_path}")
    print(f"Tabelas:   {dirs['tables']}")
    print(f"Plots:     {dirs['plots']}")
    print("")
    print("Comando para ver o veredito:")
    print(f"grep -A20 '7) VEREDITO' '{report_path}'")


if __name__ == "__main__":
    main()
