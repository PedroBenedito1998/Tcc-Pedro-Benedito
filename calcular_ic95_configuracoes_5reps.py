from pathlib import Path

import numpy as np
import pandas as pd


BASE = Path(__file__).resolve().parent
INPUT = BASE / "tabelas_comparacao_por_config_5reps.csv"
SIGN_TEST = BASE / "tabelas_vitorias_por_metrica_config_5reps.csv"
OUTPUT = BASE / "tabelas_ic95_diferencas_config_5reps.csv"

N_BOOTSTRAP = 100_000
SEED = 20260620


def main() -> None:
    comparisons = pd.read_csv(INPUT)
    sign_test = pd.read_csv(SIGN_TEST)[
        ["classe", "metrica", "sign_test_p_bilateral"]
    ]
    rng = np.random.default_rng(SEED)
    rows = []

    for (classe, metrica), group in comparisons.groupby(
        ["classe", "metrica"], sort=False
    ):
        baseline = group["baseline_media_5_reps"].to_numpy(dtype=float)
        qos = group["qos_media_5_reps"].to_numpy(dtype=float)
        direction = group["direcao_melhor"].iloc[0]

        # Positive values always mean an effect favorable to QoS.
        effect = qos - baseline if direction == "maior_melhor" else baseline - qos
        n = len(effect)
        samples = rng.integers(0, n, size=(N_BOOTSTRAP, n))
        bootstrap_means = effect[samples].mean(axis=1)
        ci_low, ci_high = np.quantile(bootstrap_means, [0.025, 0.975])

        rows.append(
            {
                "classe": classe,
                "metrica": metrica,
                "unidade": group["unidade"].iloc[0],
                "n_configuracoes": n,
                "efeito_medio_favor_qos": effect.mean(),
                "mediana_efeito_favor_qos": np.median(effect),
                "ic95_inferior": ci_low,
                "ic95_superior": ci_high,
                "qos_melhor": int((group["vencedor"] == "qos").sum()),
                "baseline_melhor": int((group["vencedor"] == "baseline").sum()),
                "empates": int((group["vencedor"] == "empate").sum()),
            }
        )

    result = pd.DataFrame(rows).merge(sign_test, on=["classe", "metrica"])
    result.to_csv(OUTPUT, index=False)
    print(result.to_string(index=False))
    print(f"\nArquivo gerado: {OUTPUT}")


if __name__ == "__main__":
    main()
