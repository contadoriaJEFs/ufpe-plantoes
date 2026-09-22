# UFPE — Sistema de Apuração de Plantões

Aplicação Streamlit para análise auditável de folhas de ponto da UFPE.

## Modelos suportados

### Modelo A — Cartão-Ponto
Reconhece:
- `Período: ...`
- linhas com `entrada` e `saída`;
- `Horas Trabalhadas: HH:MM`;
- marcações incompletas.

### Modelo B — SIGRH
Reconhece:
- `Espelho de Ponto`;
- datas no formato `DD/MM/AAAA`;
- múltiplos intervalos na mesma data, inclusive atravessando meia-noite;
- ocorrências como `FALHA NO SISTEMA`, férias e afastamentos;
- páginas de resumo sem criar registros artificiais.

## Instalação

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Regra de plantão

O sistema não afirma que um registro é plantão.

Ele calcula a duração e sugere:

`Plantão sugerido = SIM quando horas trabalhadas >= parâmetro`

O usuário decide na coluna **Validar**.

## Auditoria

Cada registro preserva:
- data;
- entrada;
- saída;
- intervalos;
- horas calculadas;
- horas declaradas no documento quando disponíveis;
- PDF de origem;
- página;
- texto original;
- sugestão automática;
- decisão do usuário.

## Observação técnica

O OCR é fallback. PDFs com camada textual são processados primeiro com PyMuPDF, reduzindo ruído de reconhecimento.
