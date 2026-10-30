# HDL-Repair

Ferramenta de **reparo automático de HDL gerado por LLM** para torná-lo
**sintetizável** e **funcionalmente equivalente** a um modelo-base (golden),
usando um loop iterativo no estilo **PAIR** (ingerir → testar → analisar → reparar).

Projeto de TCC (USP). Reaproveita o motor PAIR do AHA! Challenge 2026
(`../AHA_Challenge_2026/Notebook/pair.ipynb`), re-enquadrando os papéis de
geração de trojan para **reparo para síntese**: sem CVSS, stealth ou payload.

## Motivação

O trabalho de referência (arXiv:2603.11287, *Synthesis-in-the-Loop Evaluation of
LLMs for RTL*) mostra que HDL gerado por LLM frequentemente não é sintetizável, e
avalia modelos em geração **single-turn, zero-shot** — deixando **refinamento
iterativo com feedback de síntese como trabalho futuro**. Esta ferramenta preenche
essa lacuna e vai além: atua em fluxos com **implementação-base** (legado,
manutenção, otimização), reparando a versão nova (feita com auxílio de LLM) até
que ela seja sintetizável e equivalente à base.

## Como funciona

Para cada execução, dado um arquivo RTL de entrada e um design golden:

1. **Ingestão** (iteração 1): o HDL de entrada é avaliado como está — sem chamar LLM.
2. **Gates** (a cada iteração):
   - *Compilação* — `iverilog` compila o design com o candidato no lugar do
     arquivo-alvo (a árvore golden nunca é modificada).
   - *Sintetizabilidade* — `yosys` elabora e mapeia para a biblioteca sky130
     (opcional; se o Yosys não estiver instalado, o gate é marcado como não
     verificado e não bloqueia o loop).
   - *Equivalência* — o testbench do golden (`tb_aes`) precisa passar.
3. **Analista** (quando algum gate falha): classifica o erro pela taxonomia do
   paper (sintaxe tardia, módulo indefinido, construto não-sintetizável, task
   só-de-simulação, aninhamento inválido) e emite ações de reparo — via LLM
   (DeepSeek-R1) ou determinístico se o LLM não estiver disponível.
4. **Reparador** (DeepSeek-V3): reescreve o módulo aplicando as ações, preservando
   interface e semântica de reset.
5. **Parada**: quando compila + sintetiza + equivale (ACCEPT), ou ao atingir
   `MAX_ITERATIONS`/orçamento de tokens.

Artefatos por execução em `runs/run_<timestamp>/`: `iter_NN/`,
`repair_iterations.jsonl`, `repair_iterations.md`, `repair_summary.json` e
`repaired/<arquivo>.v` (melhor candidato).

## Instalação

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows (Git Bash)
pip install -r requirements.txt
cp .env.example .env            # preencha HF_TOKEN
```

```bash
python -m venv venv
source venv/bin/activate   # Linux
pip install -r requirements.txt
source .env
```

Ferramentas externas esperadas no PATH:
- `iverilog` + `vvp` — compilação e simulação (obrigatório p/ os gates de
  compilação e equivalência).
- `yosys` — gate de sintetizabilidade e área (opcional; instale via OSS CAD Suite
  ou `../AHA_Challenge_2026/setupEnv.sh`). Sem ele, a síntese não é verificada.
- `sta` (OpenSTA) — timing/slack (opcional).

O golden `secworks/aes` é clonado em `golden/aes/`. A Liberty sky130 (13 MB) é
reutilizada da pasta do AHA por padrão; veja `SKY130_LIB` em `.env.example`.

## Uso

```bash
python -m hdl_repair --input cases/aes/minha_versao_llm.v
# alvo inferido pelo nome do módulo; ou explícito:
python -m hdl_repair --input X.v --target-rtl aes_core.v --max-iter 8
# sem o gate de síntese (apenas compila + equivalência):
python -m hdl_repair --input X.v --no-synth
```

## Estrutura

```
src/hdl_repair/
  config.py      # DesignSpec, RepairConfig, aes_design()
  llm_client.py  # wrapper HF Inference Router (hf_chat reusado)
  rtl_io.py      # extração/validação de RTL
  gates.py       # compilação + equivalência + taxonomia de erros
  synthesis.py   # gate de sintetizabilidade (Yosys) + PPA
  analyst.py     # diagnóstico -> ações de reparo
  repairer.py    # reparo via LLM
  scoring.py     # score de reparo/qualidade
  pipeline.py    # loop orquestrador + logging
  cli.py         # CLI
golden/aes/      # clone do secworks/aes (referência limpa)
cases/aes/       # HDLs de entrada a reparar
runs/            # artefatos por execução
```

## Escopo

v1: estudo de caso **AES** (clássico). A arquitetura é agnóstica ao design
(`DesignSpec`); PQC (ex. Kyber) e integração com Vivado/FPGA são próximos passos.
