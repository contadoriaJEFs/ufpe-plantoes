# UFPE — Apuração de Plantões v2

Versão estrutural da aplicação Streamlit.

- Limpar análise.
- Uma competência por vez.
- Inconsistências integradas à Auditoria.
- 🟢 completo / 🟡 horário para conferir / 🔴 falta de registro.
- ☀️ Diurno / 🌙 Noturno.
- Reprocessamento da sugestão quando muda o limite de horas.
- Preservação de decisão manual.
- Leitor separado para Cartão-Ponto e SIGRH.
- Evidência textual + imagem da página original.
- ID único por evento.
- Resumo e exportação por turno.

No SIGRH, somente **Horário Registrado** forma eventos. HC, HH, HE e HA não são interpretados como batidas.


## Interface v3

A interface voltou ao modelo de auditoria anterior, mantendo as implementações novas:
- Auditoria como tabela principal;
- aba separada de Inconsistências;
- tratamento individual das inconsistências;
- competência por vez;
- status por bolinhas;
- turno ☀️/🌙;
- evidência integrada e também em aba própria;
- decisões manuais preservadas.

- Turno agora é uma classificação **analisável**: o sistema sugere ☀️/🌙, mas o usuário pode trocar por um seletor.
- As contagens de diurnos/noturnos, resumo e exportação usam a classificação analisada.
- Competências presentes no PDF são mantidas mesmo quando a folha não possui batidas; nesses casos a competência aparece como ficha localizada sem registros.
- O leitor Cartão-Ponto continua separado do leitor SIGRH e aceita o formato `Período ... / Data Escala Batidas Motivos Resultados`.

- v5: `Turno`/`turno_analise` é efetivamente editável na tabela de Auditoria por `SelectboxColumn`.
- Apenas `Validar` e `Turno` são editáveis; os demais campos permanecem protegidos.
- Os indicadores de diurnos/noturnos são recalculados a partir de `turno_analise` após a edição.
