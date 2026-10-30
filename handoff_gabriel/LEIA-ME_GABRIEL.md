# HDL-Repair — integração do gate de síntese (Yosys/sky130)

Ferramenta de reparo automático de HDL gerado por LLM, estilo PAIR
(ingestão → compila → *sintetiza* → equivalência → analista → reparador → repete).
Hoje rodam 2 dos 3 gates: **compilação** (iverilog) e **equivalência** (testbench do
golden). O **gate de síntese é a sua parte** e já está com o esqueleto pronto no código —
falta instalar o Yosys, apontar a biblioteca sky130 e validar.

Golden model: `secworks/aes` (7 módulos RTL). Top de síntese = `aes`.

---

## 1. Onde está cada coisa no zip

| O que | Caminho no zip |
|---|---|
| **Ponto de integração do Yosys** (seu código) | `src/hdl_repair/synthesis.py` → `run_synthesis_gate()` e `_gen_yosys_script()` |
| Como o loop chama a síntese | `src/hdl_repair/pipeline.py` (linhas ~96–103 e ~165–185) |
| Golden RTL (7 módulos, intactos) | `golden/aes/src/rtl/*.v` |
| Testbench do golden (equivalência) | `golden/aes/src/tb/tb_aes.v` |
| Config / variáveis de ambiente | `.env.example` (copie para `.env`) |
| Runner de dataset (roda o pipeline em lote) | `tools/run_dataset.py` |
| Dataset de ENTRADA (12 AES defeituosos) | `datasets/AES_defective_examples.csv` |
| **Códigos CORRIGIDOS** (saída da ferramenta) | `datasets/AES_repaired_examples.csv` |
| Tabela de resultados (sem Yosys ainda) | `datasets/RESULTS.md` e `datasets/results.csv` |
| **Códigos corrigidos já em arquivos `.v`** | `handoff_gabriel/rtl_reparados/` |
| **Casos que SÓ o Yosys pega (material de teste p/ você)** | `handoff_gabriel/rtl_nao_sintetizaveis_para_yosys/` |
| Reparo incompleto (compila mas não-equivalente) | `handoff_gabriel/rtl_reparo_incompleto/` |

> **Importante — não está no zip:** a biblioteca sky130
> `sky130_fd_sc_hd__tt_025C_1v80.lib` (~13 MB). Use a sua cópia (ou peça pro Ygor)
> e aponte via `SKY130_LIB` no `.env` (passo 3). O `.env` real foi removido do zip
> porque continha um token pessoal — você cria o seu a partir do `.env.example`.

---

## 2. Pré-requisitos

- **Python 3.10+** e `pip install -r requirements.txt`
- **Icarus Verilog** (`iverilog`/`vvp`) no PATH — gates de compilação e equivalência
- **Yosys** no PATH — o gate de síntese (sua parte)
- **sky130 liberty**: `sky130_fd_sc_hd__tt_025C_1v80.lib`

Checagem rápida: `iverilog -V` e `yosys -V` devem responder.

---

## 3. Configuração

```bash
cp .env.example .env
```

Edite o `.env` e aponte a biblioteca (única coisa obrigatória pra sua parte):

```
SKY130_LIB=/caminho/para/sky130_fd_sc_hd__tt_025C_1v80.lib
RUN_SYNTHESIS=1
SYNTH_TIMEOUT_SEC=1800
```

Se você for rodar o **loop completo** (síntese detecta → LLM repara → síntese confirma),
precisa também de um `HF_TOKEN` seu no `.env` (Hugging Face Router). Para só **validar o
gate de síntese**, não precisa de token — veja o passo 5.

---

## 4. Como o gate de síntese funciona (o que validar)

`run_synthesis_gate()` em `src/hdl_repair/synthesis.py`:

1. Checa se o `yosys` está no PATH e se a liberty existe; se não, retorna
   `available=False` e **não bloqueia** o loop (é o estado atual, sem Yosys).
2. Gera um script `.ys` (`_gen_yosys_script`) que lê os **7 RTLs juntos**
   (com o módulo-alvo substituído pelo candidato reparado), faz
   `hierarchy -check -top aes`, `proc/opt/flatten/memory/techmap`,
   `dfflibmap`, `abc -liberty ...` e `stat` → área + nº de células.
3. `synthesizable = True` se elaborou e mapeou sem erro e gerou o netlist.
4. Se falhar, classifica o log (`classify_errors`) para orientar o reparo.

**Ponto-chave:** com o Yosys disponível, o critério de ACEITE passa a exigir
`compilar + equivalente + sintetizável` (`pipeline.py`, ~linha 184). Ou seja, os casos
que hoje "passam" por falta do gate de síntese vão **falhar** e disparar o reparo —
é exatamente o que a sua integração habilita.

---

## 5. Teste recomendado (sem precisar de token LLM)

Os arquivos em `handoff_gabriel/rtl_nao_sintetizaveis_para_yosys/` são AES defeituosos
que **compilam e passam no testbench**, mas **não são sintetizáveis** (bloco `initial`
com declaração local, atraso `#10` não-sintetizável, `$display` dentro do DUT).
Hoje a ferramenta os aceita por engano — **o seu gate deve reprová-los.**

Rode o dataset só com ingestão (sem LLM): a iteração 1 já executa o gate de síntese.

```bash
PYTHONPATH=src python tools/run_dataset.py \
    --csv datasets/AES_defective_examples.csv \
    --no-llm --max-iter 1 --out datasets/results_yosys_check.csv
```

**Esperado depois do Yosys plugado:** nas linhas `non-synthesizable-construct` e
`sim-only-system-task`, a coluna `synth_verified` deve ficar `True` (gate rodou) e o
caso deve ser marcado como **não-sintetizável** (deixa de ser aceito). Compare com
`datasets/results.csv` (estado atual, sem Yosys), onde esses casos aparecem aceitos.

Sanity check isolado (sintetizar o golden intacto direto no Yosys):

```bash
yosys -p "read_verilog golden/aes/src/rtl/*.v; \
          read_liberty -lib $SKY130_LIB; \
          hierarchy -check -top aes; proc; opt; flatten; memory; opt; \
          techmap; opt; dfflibmap -liberty $SKY130_LIB; \
          abc -liberty $SKY130_LIB -D 1000; stat -liberty $SKY130_LIB"
```

Isso deve sintetizar sem erro e imprimir área/células — é o baseline do golden.

---

## 6. Loop completo (opcional, precisa de HF_TOKEN)

Com `SKY130_LIB` + `HF_TOKEN` no `.env`:

```bash
PYTHONPATH=src python tools/run_dataset.py \
    --csv datasets/AES_defective_examples.csv --max-iter 3
```

Agora os casos não-sintetizáveis seguem: Yosys reprova → analista classifica →
reparador reescreve → Yosys reconfere. Os artefatos de cada execução ficam em
`runs/run_<timestamp>/iter_NN/` (inclui o `synth/synth.ys`, `area_report.txt`,
`netlist.v` e os logs), e o resumo em `repair_summary.json`.

---

## 7. Métricas (PPA) — próximo passo

`synthesis.py` já extrai **área** e **nº de células** (`parse_area_report`) e calcula
`area_overhead_pct` vs. o golden quando há baseline. Timing (slack) está como
placeholder (`parse_sta_report`) à espera do OpenSTA por execução — esse é um gancho
natural pra você estender se quiser comparar timing também.

---

## Resumo do que é seu

1. Instalar Yosys + apontar `SKY130_LIB` no `.env`.
2. Validar com o teste do passo 5 (os `rtl_nao_sintetizaveis_para_yosys/` devem ser reprovados).
3. Confirmar o loop completo (passo 6) convergindo nesses casos.
4. (Extensão) Timing/PPA com OpenSTA.

Dúvidas sobre o resto do pipeline: `README.md` na raiz e `datasets/RESULTS.md`.
