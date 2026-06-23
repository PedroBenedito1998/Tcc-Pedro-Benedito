from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


BASE = Path(__file__).resolve().parent
CSV = BASE / "tabelas_vitorias_por_metrica_config_5reps.csv"
PLOTS = BASE / "plots"

OUT_MAIN = PLOTS / "qos_vitorias_por_config_5reps.png"
OUT_MAIN_ALT = PLOTS / "qos_vitorias_por_config_5reps_organizado.png"
OUT_FULL = PLOTS / "qos_vitorias_por_config_5reps_completo.png"

COL_QOS = "#2f6f4f"
COL_BASE = "#a3513d"
COL_TIE = "#b5b5b5"
COL_GRID = "#d8dde3"
COL_TEXT = "#222222"


def p_label(value: float) -> str:
    if value < 0.001:
        return "p < 0.001"
    return f"p = {value:.3f}"


def clean_metric(metric: str) -> str:
    mapping = {
        "Atraso UDP fim-a-fim": "Atraso",
        "Jitter UDP": "Jitter",
        "Bitrate UDP recebido": "Bitrate",
        "Perda UDP percentual D-ITG": "Perda (%)",
        "Perda UDP absoluta por intervalo": "Perda abs.",
        "Goodput TCP": "Goodput TCP",
        "Overhead TCP estimado": "Overhead TCP",
    }
    return mapping.get(metric, metric)


def load_data() -> pd.DataFrame:
    df = pd.read_csv(CSV)
    numeric_cols = [
        "configs_total",
        "qos_melhor",
        "baseline_melhor",
        "empates",
        "qos_melhor_percent_configs",
        "sign_test_p_bilateral",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col])
    df["metrica_curta"] = df["metrica"].map(clean_metric)
    df["classe_curta"] = df["classe"].replace({"Video": "Video", "Voz": "Voz"})
    return df


def select_main(df: pd.DataFrame) -> pd.DataFrame:
    wanted = [
        ("Voz", "Atraso UDP fim-a-fim"),
        ("Voz", "Jitter UDP"),
        ("Voz", "Bitrate UDP recebido"),
        ("Voz", "Perda UDP percentual D-ITG"),
        ("Video", "Atraso UDP fim-a-fim"),
        ("Video", "Jitter UDP"),
        ("Video", "Bitrate UDP recebido"),
        ("Video", "Perda UDP percentual D-ITG"),
        ("Background", "Goodput TCP"),
    ]
    order = {item: idx for idx, item in enumerate(wanted)}
    selected = df[df.apply(lambda r: (r["classe"], r["metrica"]) in order, axis=1)].copy()
    selected["ordem"] = selected.apply(lambda r: order[(r["classe"], r["metrica"])], axis=1)
    return selected.sort_values("ordem")


def select_full(df: pd.DataFrame) -> pd.DataFrame:
    order_class = {"Voz": 0, "Video": 1, "Background": 2}
    order_metric = {
        "Atraso": 0,
        "Jitter": 1,
        "Bitrate": 2,
        "Perda (%)": 3,
        "Perda abs.": 4,
        "Goodput TCP": 5,
        "Overhead TCP": 6,
    }
    full = df.copy()
    full["ordem"] = full.apply(
        lambda r: order_class.get(r["classe"], 9) * 10 + order_metric.get(r["metrica_curta"], 9),
        axis=1,
    )
    return full.sort_values("ordem")


def draw_plot(df: pd.DataFrame, out_path: Path, title: str, subtitle: str, height: float) -> None:
    rows = df.reset_index(drop=True).copy()
    labels = [f"{r.classe_curta} | {r.metrica_curta}" for r in rows.itertuples()]
    y = list(range(len(rows)))

    fig, ax = plt.subplots(figsize=(14.6, height))
    ax.barh(y, rows["qos_melhor"], color=COL_QOS, label="QoS melhor")
    ax.barh(
        y,
        rows["baseline_melhor"],
        left=rows["qos_melhor"],
        color=COL_BASE,
        label="Baseline melhor",
    )
    ax.barh(
        y,
        rows["empates"],
        left=rows["qos_melhor"] + rows["baseline_melhor"],
        color=COL_TIE,
        label="Empate",
    )

    ax.axvline(24, color="#3b4652", linewidth=1.1, linestyle="--", alpha=0.75)
    ax.text(24.25, len(rows) - 0.10, "metade (24/48)", ha="left", va="bottom",
            fontsize=8.5, color="#3b4652")

    for idx, row in rows.iterrows():
        q = int(row["qos_melhor"])
        b = int(row["baseline_melhor"])
        e = int(row["empates"])
        total = int(row["configs_total"])
        pct = row["qos_melhor_percent_configs"]

        left = 0
        for value, color_name in [(q, "white"), (b, "white"), (e, COL_TEXT)]:
            if value > 0:
                ax.text(left + value / 2, idx, str(value), va="center", ha="center",
                        fontsize=9, color=color_name, fontweight="bold")
            left += value

        sig = "sig." if row["resultado_estatistico_5pct"] == "significativo" else "n.s."
        ax.text(49.1, idx, f"{q}/{total} ({pct:.1f}%)", va="center", ha="left",
                fontsize=9, color=COL_QOS, fontweight="bold")
        ax.text(57.7, idx, f"{p_label(row['sign_test_p_bilateral'])} | {sig}",
                va="center", ha="left", fontsize=8.5, color=COL_TEXT)

    ax.text(49.1, -0.78, "QoS melhor", va="bottom", ha="left", fontsize=9,
            color=COL_QOS, fontweight="bold")
    ax.text(57.7, -0.78, "teste binomial", va="bottom", ha="left", fontsize=9,
            color=COL_TEXT, fontweight="bold")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlim(0, 66)
    ax.set_xticks([0, 12, 24, 36, 48])
    ax.set_xlabel("Numero de configuracoes comparadas (48 no total)", fontsize=10)
    fig.suptitle(title, fontsize=15.5, fontweight="bold", y=0.982)
    fig.text(0.065, 0.932, subtitle, ha="left", va="bottom",
             fontsize=10, color="#52606d")
    ax.grid(axis="x", color=COL_GRID, linewidth=0.8, alpha=0.7)
    ax.set_axisbelow(True)
    handles, legend_labels = ax.get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="upper center", bbox_to_anchor=(0.52, 0.925),
               ncol=3, frameon=False, fontsize=10)

    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#8c98a4")
    ax.tick_params(axis="y", length=0)

    note = (
        "Fonte: tabelas_vitorias_por_metrica_config_5reps.csv. "
        "Valores sao medias de 5 repeticoes por combinacao; p-valor bilateral ignora empates."
    )
    fig.text(0.01, 0.012, note, fontsize=8.2, color="#52606d")

    fig.tight_layout(rect=(0, 0.04, 1, 0.875))
    fig.savefig(out_path, dpi=240, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    PLOTS.mkdir(parents=True, exist_ok=True)

    if OUT_MAIN.exists():
        backup = PLOTS / "qos_vitorias_por_config_5reps_original.png"
        if not backup.exists():
            backup.write_bytes(OUT_MAIN.read_bytes())

    df = load_data()
    main_df = select_main(df)
    full_df = select_full(df)

    title = "Vitorias por configuracao: QoS x Baseline"
    subtitle = "Comparacao direta em 48 combinacoes de banda, atraso e perda; cada combinacao usa a media de 5 repeticoes."
    draw_plot(main_df, OUT_MAIN, title, subtitle, height=6.7)
    draw_plot(main_df, OUT_MAIN_ALT, title, subtitle, height=6.7)

    full_title = "Vitorias por configuracao: todas as metricas do CSV"
    full_subtitle = "Grafico completo para auditoria, incluindo perda absoluta e overhead TCP."
    draw_plot(full_df, OUT_FULL, full_title, full_subtitle, height=7.9)

    print(f"Gerado: {OUT_MAIN}")
    print(f"Gerado: {OUT_MAIN_ALT}")
    print(f"Gerado: {OUT_FULL}")


if __name__ == "__main__":
    main()
