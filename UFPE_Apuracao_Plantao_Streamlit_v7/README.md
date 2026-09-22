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

- v6: inconsistências permanecem na sequência da Auditoria apenas pelo marcador de status 🟢/🟡/🔴; o tratamento detalhado continua na aba Inconsistências.
- Registros 🟢 completos são validados automaticamente.
- `Plantão sugerido` deixou de ser uma caixa de seleção: agora é uma indicação textual SIM/NÃO, pois não é uma decisão do usuário.
- `Validar` é a decisão efetivamente editável.

- v7: navegação principal por trimestre; os três meses do trimestre aparecem na mesma tela, em sequência.
- O botão Próximo avança o trimestre, evitando o conflito anterior entre o botão e o seletor de competência.
- Inconsistências passam a aparecer na sequência da Auditoria, com marcador e indicação do campo problemático, além do detalhamento na aba Inconsistências.
