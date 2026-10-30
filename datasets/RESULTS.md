# Resultados — reparo de AES defeituoso (sem Yosys ainda)

Gerado por `tools/run_dataset.py` sobre `datasets/AES_defective_examples.csv`.
Golden: `golden/aes` (secworks/aes). Gates ativos: compilacao (iverilog) + equivalencia (testbench do golden).
Gate de sintese (Yosys/sky130) ainda nao instalado — ver `synthesis.py::run_synthesis_gate`.

| # | modulo | defeito injetado | detectavel sem Yosys | resultado | iter |
|---|---|---|---|---|---|
| 0 | aes_sbox.v | erro de sintaxe | sim | REPARADO (compila + equivalente) | 2 |
| 1 | aes_inv_sbox.v | erro de sintaxe | sim | REPARADO (compila + equivalente) | 2 |
| 2 | aes_key_mem.v | modulo/sinal indefinido | sim | REPARADO (compila + equivalente) | 2 |
| 3 | aes_core.v | modulo/sinal indefinido | sim | REPARADO (compila + equivalente) | 2 |
| 4 | aes.v | construto nao-sintetizavel | sim | REPARADO (compila + equivalente) | 2 |
| 5 | aes_encipher_block.v | construto nao-sintetizavel | nao | passou gates atuais (so Yosys pega) | 1 |
| 6 | aes_decipher_block.v | erro de sintaxe | sim | compilou, mas nao-equivalente | 3 |
| 7 | aes_encipher_block.v | modulo/sinal indefinido | sim | REPARADO (compila + equivalente) | 3 |
| 8 | aes_inv_sbox.v | aninhamento invalido | sim | REPARADO (compila + equivalente) | 2 |
| 9 | aes_sbox.v | bug funcional | sim | REPARADO (compila + equivalente) | 2 |
| 10 | aes_key_mem.v | construto nao-sintetizavel | nao | passou gates atuais (so Yosys pega) | 1 |
| 11 | aes_core.v | task so-de-simulacao | nao | passou gates atuais (so Yosys pega) | 1 |

**Resumo:** 12 exemplos de AES defeituoso. Dos 9 detectaveis sem Yosys, **8 foram reparados** (voltaram a compilar e a bater com o golden) e 1 compilou mas nao recuperou equivalencia. Os 3 restantes compilam/passam no testbench e **so o gate de sintese (Yosys) detecta** — proxima etapa (Gabriel). Tokens LLM totais: ~89k (DeepSeek-V3).

## Codigos corrigidos (`AES_repaired_examples.csv`)

Gerado pelo mesmo script com `--codes-out` (replicavel). Contem, por exemplo, o
codigo defeituoso de entrada (`Defective_Code`) e o codigo reparado produzido
pela ferramenta (`Repaired_Code`), alem de status/iteracoes/tokens.

Esta versao cobre os **8 modulos menores** (`aes`, `aes_core`, `aes_key_mem`,
`aes_encipher_block`, `aes_decipher_block`). Os dois ROMs de S-box
(`aes_sbox.v` / `aes_inv_sbox.v`, tabelas de 256 entradas) ficaram de fora deste
CSV porque a LLM precisa regenerar a tabela inteira a cada iteracao, o que torna
a execucao muito lenta nesta sessao — eles estao documentados na tabela de 12
acima e entram no CSV completo quando a API estiver mais rapida.

Reproduzir:

```
PYTHONPATH=src python tools/run_dataset.py --csv datasets/_fast_subset.csv \
    --max-iter 3 --out datasets/results_fast.csv \
    --codes-out datasets/AES_repaired_examples.csv
```

Resultado deste subconjunto (8): **7 aceitos** (4 reparos reais de codigo + 3 casos
`needs-yosys` que passam nos gates atuais) e **1 nao-equivalente** (`aes_decipher_block.v`,
`missing_endmodule`: a LLM recolocou o `endmodule` e o codigo compilou, mas alterou
logica ao regenerar o bloco e perdeu a equivalencia).
