
import io
import re
import unicodedata
from datetime import datetime, date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pdfplumber
import fitz
import pytesseract
from PIL import Image
import plotly.express as px
import streamlit as st


# ============================================================
# UFPE — Sistema de Apuração de Plantões em Folhas de Ponto
# ============================================================

st.set_page_config(
    page_title="UFPE • Apuração de Plantões",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)


# -----------------------------
# Utilidades
# -----------------------------
MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
MESES_NORM = {
    "".join(c for c in unicodedata.normalize("NFKD", k.lower())
            if not unicodedata.combining(c)): v
    for k, v in MESES.items()
}

TIME_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2})(?!\d)")
INTERVAL_RE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
DATE_A_RE = re.compile(r"^(\d{1,2}/\d{1,2})(?:\s+\w{2,4})?\b")
DATE_B_RE = re.compile(r"(?<!\d)(\d{2}/\d{2}/\d{4})(?!\d)")
PERIODO_RE = re.compile(
    r"Período:\s*(\d{1,2})/([A-Za-zÀ-ÿ]+)/(20\d{2})\s+a\s+"
    r"(\d{1,2})/([A-Za-zÀ-ÿ]+)/(20\d{2})",
    re.I,
)
HORAS_TRAB_RE = re.compile(
    r"Horas\s+Trabalhadas\s*:?\s*(\d{1,3}:\d{2})", re.I
)

def norm(s):
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(s).lower())
        if not unicodedata.combining(c)
    )

def parse_hhmm(value):
    if value is None or pd.isna(value):
        return None
    m = re.search(r"(\d{1,3}):(\d{2})", str(value))
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))

def fmt_minutes(minutes):
    if minutes is None or pd.isna(minutes):
        return ""
    sign = "-" if int(minutes) < 0 else ""
    minutes = abs(int(minutes))
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"

def duration(entry, exit_):
    a = parse_hhmm(entry)
    b = parse_hhmm(exit_)
    if a is None or b is None:
        return None
    if b < a:
        b += 24 * 60
    return b - a

def make_date(day, month, year):
    try:
        return date(int(year), int(month), int(day))
    except Exception:
        return None

def safe_text(text):
    return re.sub(r"[ \t]+", " ", str(text or "")).strip()

def parse_period(text):
    m = PERIODO_RE.search(text or "")
    if not m:
        return None
    month = MESES_NORM.get(norm(m.group(2)))
    if not month:
        return None
    return int(m.group(3)), month

def extract_first_date_b(text):
    m = DATE_B_RE.search(text or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d/%m/%Y").date()
    except Exception:
        return None

def detect_model(text):
    n = norm(text)
    if "sigrh" in n or "espelho de ponto" in n or "frequencia" in n:
        return "B — SIGRH"
    return "A — Cartão-Ponto"

def extract_metadata(text):
    data = {"nome": "", "matricula": "", "cpf": "", "modelo": detect_model(text)}

    m = re.search(r"Empregado:\s*(.+?)(?:\s*\|\s*CPF:|\n|$)", text, re.I)
    if m:
        data["nome"] = safe_text(m.group(1))

    m = re.search(r"Servidor:\s*(.+?)\s*\((\d+)\)", text, re.I)
    if m:
        data["nome"] = safe_text(m.group(1))
        data["matricula"] = m.group(2)

    m = re.search(r"CPF:\s*([\d.\-]+)", text, re.I)
    if m:
        data["cpf"] = m.group(1)

    m = re.search(r"Matrícula:\s*([\d.\-]+)", text, re.I)
    if m:
        data["matricula"] = m.group(1)

    if not data["nome"]:
        m = re.search(r"Servidor:\s*(.+)", text, re.I)
        if m:
            data["nome"] = safe_text(m.group(1))

    return data

def extract_page_text(page, use_ocr=True):
    # PyMuPDF é a primeira fonte porque preserva melhor a posição/ordem do SIGRH.
    text = page.get_text("text") or ""
    if len(re.sub(r"\s+", "", text)) >= 80:
        return text, "texto"

    # pdfplumber como segunda tentativa.
    try:
        with pdfplumber.open(page.parent.name if hasattr(page.parent, "name") else ""):
            pass
    except Exception:
        pass

    if use_ocr:
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            ocr = pytesseract.image_to_string(img, lang="por+eng")
            if len(re.sub(r"\s+", "", ocr)) > len(re.sub(r"\s+", "", text)):
                return ocr, "OCR"
        except Exception:
            pass

    return text, "texto"

def extract_pages(file_bytes, filename, use_ocr=True):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages = []
    for page_no in range(len(doc)):
        page = doc[page_no]
        text, source = extract_page_text(page, use_ocr=use_ocr)
        pages.append({
            "pdf": filename,
            "page_index": page_no,
            "page": page_no + 1,
            "text": text,
            "text_source": source,
        })
    return pages

def infer_competence_from_page(text):
    period = parse_period(text)
    if period:
        return period

    d = extract_first_date_b(text)
    if d:
        return d.year, d.month

    # SIGRH pode ter cabeçalho com caracteres corrompidos.
    # Datas do quadro diário continuam preservadas; a primeira data do quadro
    # é usada como competência da página.
    return None

def page_is_sigrh_data(text):
    n = norm(text)
    return (
        (
            "espelho de ponto" in n
            or "ponto diario associado" in n
            or "frequencia" in n
        )
        and bool(DATE_B_RE.search(text or ""))
    )

def page_is_sigrh_summary(text):
    n = norm(text)
    return "sigrh" in n and (
        "saldo de horas de" in n or "total de horas homologadas" in n
    )


# -----------------------------
# Modelo A
# -----------------------------
def parse_model_a_page(page_info):
    text = page_info["text"]
    period = parse_period(text)
    if not period:
        return [], []

    year, month = period
    records, inconsistencies = [], []

    lines = [safe_text(x) for x in text.splitlines() if safe_text(x)]
    current_day = None
    current_original = []

    def flush(day, original):
        if day is None:
            return

        block = " ".join(original)
        # No Cartão-Ponto antigo, a coluna Escala pode conter horários
        # do turno (ex.: [19:00 às 07:00]). Esses horários NÃO são batidas.
        # Remove-se a descrição da escala antes de extrair Entrada/Saída.
        batidas_block = re.sub(
            r"Plantão.*?(?:FOLGA\s+NOTURNA|12h\s*-\s*19:00\s+à\s+07:00)\s*",
            "", block, flags=re.I
        )
        # Remove totais/lançamentos que também contêm HH:MM, mas não são
        # Entrada/Saída do dia.
        batidas_block = re.sub(
            r"Horas\s+Previstas\s*:\s*\d{1,3}:\d{2}", "",
            batidas_block, flags=re.I
        )
        batidas_block = re.sub(
            r"BANCO\s*DE\s*HORAS\s*:\s*-?\d{1,3}:\d{2}", "",
            batidas_block, flags=re.I
        )
        batidas_block = re.sub(
            r"(?:Férias|Ferias|Horas\s+Falta)\s*:?\s*-?\d{1,3}:\d{2}", "",
            batidas_block, flags=re.I
        )
        times = TIME_RE.findall(batidas_block)

        # Remover horários que pertencem ao total "Horas Trabalhadas".
        explicit = HORAS_TRAB_RE.search(block)
        explicit_minutes = parse_hhmm(explicit.group(1)) if explicit else None

        # Os dois primeiros horários são, no modelo A, entrada e saída.
        # Se houver apenas um, é inconsistência.
        entry = times[0] if len(times) >= 1 else None
        exit_ = times[1] if len(times) >= 2 else None

        if entry and exit_:
            calc = duration(entry, exit_)
            if explicit_minutes is not None:
                # O valor calculado é a memória matemática da apuração.
                # Mantemos também o valor declarado pelo documento.
                hours = calc
                duration_source = "calculado"
                declared = explicit_minutes
            else:
                hours = calc
                duration_source = "calculado"
                declared = None

            records.append({
                "data": day,
                "entrada": entry,
                "saida": exit_,
                "horas_min": hours,
                "horas": fmt_minutes(hours),
                "horas_calculadas_min": calc,
                "horas_calculadas": fmt_minutes(calc),
                "horas_documento": fmt_minutes(declared) if declared is not None else "",
                "duracao_fonte": duration_source,
                "modelo": "A — Cartão-Ponto",
                "pdf": page_info["pdf"],
                "pagina": page_info["page"],
                "texto_original": block,
                "status": "OK",
                "problema": "",
            })
        elif entry or exit_:
            inconsistencies.append({
                "data": day,
                "problema": "Batida incompleta: apenas um horário encontrado.",
                "texto_original": block,
                "pdf": page_info["pdf"],
                "pagina": page_info["page"],
            })

    for line in lines:
        # O rodapé marca o fim do quadro diário.
        if line.startswith("* Batida") or line.startswith("Num. "):
            break

        # Data no formato 01/02, 02/09 etc.
        m = DATE_A_RE.search(line)
        if m:
            # Ignora datas que estejam dentro do rodapé/documento.
            dm = re.match(r"(\d{1,2})/(\d{1,2})", m.group(1))
            if dm:
                day = make_date(dm.group(1), dm.group(2), year)
                flush(current_day, current_original)
                current_day = day
                current_original = [line]
                continue

        if current_day is not None:
            # Uma linha com outro conteúdo pertence à linha corrente.
            current_original.append(line)

    flush(current_day, current_original)
    return records, inconsistencies


# -----------------------------
# Modelo B — SIGRH
# -----------------------------
def parse_model_b_page(page_info):
    text = page_info["text"]
    records, inconsistencies = [], []

    if not page_is_sigrh_data(text):
        return records, inconsistencies

    lines = [safe_text(x) for x in text.splitlines() if safe_text(x)]

    # Localiza todos os inícios de linha diária.
    starts = []
    for idx, line in enumerate(lines):
        m = DATE_B_RE.search(line)
        if m:
            try:
                d = datetime.strptime(m.group(1), "%d/%m/%Y").date()
                starts.append((idx, d))
            except Exception:
                pass

    for pos, (idx, d) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        block_lines = lines[idx:end]
        block = " ".join(block_lines)

        # Tudo até a próxima data é a evidência daquele dia.
        intervals = INTERVAL_RE.findall(block)

        # Detecta explicitamente uma marcação aberta, como "19:01 -".
        block_multiline = "\n".join(block_lines)
        open_interval = bool(
            re.search(r"\d{1,2}:\d{2}\s*-\s*(?:---\s*)?$", block_multiline, re.M)
        )
        occurrences = []

        # Extrai ocorrências comuns do SIGRH.
        for token in [
            "FALHA NO SISTEMA", "FALTA DE INTERNET", "Férias",
            "FERIAS", "AFASTAMENTOS DIVERSOS", "PONTO FACULTATIVO",
            "Feriado", "Feriados", "TRABALHO REMOTO"
        ]:
            if norm(token) in norm(block):
                occurrences.append(token)

        if intervals:
            # Cada intervalo é uma parcela do trabalho do mesmo dia.
            total = 0
            for entry, exit_ in intervals:
                total += duration(entry, exit_) or 0

            # No SIGRH, depois das linhas de intervalo vem HR (Horas
            # Registradas), que representa a duração documental do dia.
            # Preserva-se esse valor e também o cálculo aritmético.
            last_interval_line = 0
            for j, line in enumerate(block_lines):
                if INTERVAL_RE.search(line):
                    last_interval_line = j
            document_minutes = None
            for line in block_lines[last_interval_line + 1:]:
                if re.fullmatch(r"\d{1,3}:\d{2}", line):
                    document_minutes = parse_hhmm(line)
                    break

            hours = document_minutes if document_minutes is not None else total

            records.append({
                "data": d,
                "entrada": intervals[0][0],
                "saida": intervals[-1][1],
                "intervalos": " | ".join(f"{a} - {b}" for a, b in intervals),
                "horas_min": hours,
                "horas": fmt_minutes(hours),
                "horas_calculadas_min": total,
                "horas_calculadas": fmt_minutes(total),
                "horas_documento": (
                    fmt_minutes(document_minutes)
                    if document_minutes is not None else ""
                ),
                "duracao_fonte": "calculado a partir das marcações",
                "modelo": "B — SIGRH",
                "pdf": page_info["pdf"],
                "pagina": page_info["page"],
                "texto_original": block,
                "status": "INCONSISTÊNCIA" if open_interval else "OK",
                "problema": (
                    "Marcação aberta/incompleta." if open_interval else ""
                ),
                "ocorrencias": " | ".join(occurrences),
            })

            if open_interval:
                inconsistencies.append({
                    "data": d,
                    "problema": "Marcação aberta/incompleta.",
                    "texto_original": block,
                    "pdf": page_info["pdf"],
                    "pagina": page_info["page"],
                })
        elif TIME_RE.search(block):
            # Há horários, mas não foi possível formar um intervalo completo.
            # Não transformar automaticamente ocorrência justificada em plantão.
            if not occurrences:
                problem = "Horário encontrado sem par entrada/saída."
            else:
                problem = "Horário/ocorrência sem intervalo completo."

            inconsistencies.append({
                "data": d,
                "problema": problem,
                "texto_original": block,
                "pdf": page_info["pdf"],
                "pagina": page_info["page"],
            })

    return records, inconsistencies


def parse_all(uploaded_files, use_ocr=True):
    all_records = []
    all_incons = []
    all_pages = []
    metadata = {}
    pdf_bytes = {}

    for uploaded in uploaded_files:
        file_bytes = uploaded.getvalue()
        pdf_bytes[uploaded.name] = file_bytes
        pages = extract_pages(file_bytes, uploaded.name, use_ocr=use_ocr)
        all_pages.extend(pages)

        # Metadados do primeiro texto útil.
        for p in pages:
            md = extract_metadata(p["text"])
            if md.get("nome") or md.get("matricula"):
                metadata.setdefault(uploaded.name, md)
                break

        active_comp = None
        for p in pages:
            model = detect_model(p["text"])
            p["modelo"] = model

            if model.startswith("B"):
                comp = infer_competence_from_page(p["text"])
                if comp:
                    active_comp = comp
                if page_is_sigrh_data(p["text"]):
                    recs, incs = parse_model_b_page(p)
                    for r in recs:
                        r["ano"] = r["data"].year
                        r["mes"] = r["data"].month
                        r["competencia"] = f"{r['mes']:02d}/{r['ano']}"
                    for x in incs:
                        x["competencia"] = (
                            f"{x['data'].month:02d}/{x['data'].year}"
                            if x.get("data") else ""
                        )
                    all_records.extend(recs)
                    all_incons.extend(incs)
            else:
                recs, incs = parse_model_a_page(p)
                for r in recs:
                    r["ano"] = r["data"].year
                    r["mes"] = r["data"].month
                    r["competencia"] = f"{r['mes']:02d}/{r['ano']}"
                for x in incs:
                    x["competencia"] = (
                        f"{x['data'].month:02d}/{x['data'].year}"
                        if x.get("data") else ""
                    )
                all_records.extend(recs)
                all_incons.extend(incs)

    # Deduplicação conservadora: mesmo PDF/página/data/entrada/saída.
    if all_records:
        df = pd.DataFrame(all_records)
        df = df.drop_duplicates(
            subset=["pdf", "pagina", "data", "entrada", "saida"],
            keep="first"
        ).reset_index(drop=True)
    else:
        df = pd.DataFrame()

    inc_df = pd.DataFrame(all_incons)
    if not inc_df.empty:
        inc_df = inc_df.drop_duplicates(
            subset=["pdf", "pagina", "data", "problema", "texto_original"]
        ).reset_index(drop=True)

    return df, inc_df, all_pages, metadata, pdf_bytes


# -----------------------------
# Estado e exportação
# -----------------------------
def ensure_state(df):
    if "records" not in st.session_state:
        st.session_state.records = df.copy()
    if not st.session_state.records.empty and "validar" not in st.session_state.records:
        threshold = st.session_state.get("threshold", 12.0)
        st.session_state.records["validar"] = (
            st.session_state.records["horas_min"] >= threshold * 60
        )
    if "incons" not in st.session_state:
        st.session_state.incons = pd.DataFrame()
    if "pages" not in st.session_state:
        st.session_state.pages = []


def build_excel(records, inconsistencies):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        r = records.copy()

        valid = r[r["validar"] == True].copy() if not r.empty else pd.DataFrame()
        if not valid.empty:
            cols = [
                "competencia", "data", "entrada", "saida",
                "intervalos", "horas", "modelo", "pdf", "pagina",
                "texto_original"
            ]
            cols = [c for c in cols if c in valid.columns]
            valid[cols].to_excel(writer, sheet_name="Plantões Validados", index=False)
        else:
            pd.DataFrame({"Resultado": ["Nenhum plantão validado"]}).to_excel(
                writer, sheet_name="Plantões Validados", index=False
            )

        if not records.empty:
            resumo = (
                records.groupby("competencia", dropna=False)
                .agg(
                    dias_trabalhados=("data", "nunique"),
                    horas_totais_min=("horas_min", "sum"),
                    plantões_validados=("validar", "sum"),
                )
                .reset_index()
            )
            resumo["horas_totais"] = resumo["horas_totais_min"].apply(fmt_minutes)
            resumo = resumo.drop(columns=["horas_totais_min"])
            resumo.to_excel(writer, sheet_name="Resumo Mensal", index=False)
        else:
            pd.DataFrame().to_excel(writer, sheet_name="Resumo Mensal", index=False)

        if inconsistencies is not None and not inconsistencies.empty:
            inconsistencies.to_excel(
                writer, sheet_name="Inconsistências", index=False
            )
        else:
            pd.DataFrame({"Resultado": ["Nenhuma inconsistência registrada"]}).to_excel(
                writer, sheet_name="Inconsistências", index=False
            )

        audit_cols = [
            "competencia", "data", "entrada", "saida", "intervalos",
            "horas", "horas_calculadas", "horas_documento", "modelo", "pdf", "pagina",
            "status", "problema", "validar", "texto_original"
        ]
        audit_cols = [c for c in audit_cols if c in r.columns]
        r[audit_cols].to_excel(writer, sheet_name="Auditoria", index=False)

    return output.getvalue()


# -----------------------------
# Interface
# -----------------------------
st.title("UFPE • Apuração de Plantões")
st.caption(
    "Análise auditável de folhas de ponto — sugestão automática, validação humana e rastreabilidade por PDF/página."
)

with st.sidebar:
    st.header("Configuração")
    threshold = st.number_input(
        "Horas mínimas para sugerir 1 plantão",
        min_value=0.5,
        max_value=48.0,
        value=float(st.session_state.get("threshold", 12.0)),
        step=0.5,
    )
    st.session_state.threshold = threshold

    use_ocr = st.checkbox(
        "Usar OCR como fallback",
        value=True,
        help="Aciona OCR somente quando a extração textual do PDF for insuficiente."
    )

    st.divider()
    st.subheader("Fluxo")
    st.markdown(
        "1. Upload\n"
        "2. Extração\n"
        "3. Auditoria\n"
        "4. Validação\n"
        "5. Resumo\n"
        "6. Exportação"
    )

uploads = st.file_uploader(
    "Envie um ou mais PDFs de folha de ponto",
    type=["pdf"],
    accept_multiple_files=True,
)

if uploads:
    if st.button("🔎 Processar PDFs", type="primary"):
        with st.spinner("Extraindo páginas, competências e marcações..."):
            records, inconsistencies, pages, metadata, pdf_bytes = parse_all(
                uploads, use_ocr=use_ocr
            )
            st.session_state.records = records
            st.session_state.incons = inconsistencies
            st.session_state.pages = pages
            st.session_state.metadata = metadata
            st.session_state.pdf_bytes = pdf_bytes
        st.success(
            f"Processamento concluído: {len(records)} registros de trabalho e "
            f"{len(inconsistencies)} inconsistências."
        )

if "records" not in st.session_state:
    st.info("Envie os PDFs e clique em **Processar PDFs**.")
    st.stop()

df = st.session_state.records.copy()
inc_df = st.session_state.get("incons", pd.DataFrame())

if df.empty:
    st.warning("Nenhum registro de trabalho foi identificado nos PDFs.")
    if not inc_df.empty:
        st.subheader("Inconsistências encontradas")
        st.dataframe(inc_df, use_container_width=True)
    st.stop()

# Recalcula sugestão somente quando o usuário altera o parâmetro.
df["plantao_sugerido"] = df["horas_min"] >= threshold * 60
if "validar" not in df:
    df["validar"] = df["plantao_sugerido"]
else:
    # Preserva decisões já feitas, mas novos registros seguem a sugestão.
    df["validar"] = df["validar"].fillna(df["plantao_sugerido"])

st.session_state.records = df

# Metadados
with st.expander("Dados do servidor / documentos", expanded=False):
    metadata = st.session_state.get("metadata", {})
    if metadata:
        rows = []
        for fn, md in metadata.items():
            rows.append({
                "PDF": fn,
                "Nome": md.get("nome", ""),
                "Matrícula": md.get("matricula", ""),
                "CPF": md.get("cpf", ""),
                "Modelo detectado": md.get("modelo", ""),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

# KPIs
total_horas = int(df["horas_min"].sum())
dias = int(df["data"].nunique())
plantao_count = int(df["validar"].sum())
media = total_horas / plantao_count if plantao_count else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Horas trabalhadas", fmt_minutes(total_horas))
c2.metric("Dias trabalhados", dias)
c3.metric("Plantões validados", plantao_count)
c4.metric("Média h/plantão", fmt_minutes(round(media)))

tabs = st.tabs([
    "Auditoria",
    "Inconsistências",
    "Resumo mensal",
    "Estatísticas",
    "Evidência",
    "Exportação",
])

with tabs[0]:
    st.subheader("Auditoria dos registros")
    st.caption(
        "A sugestão é automática. A coluna 'Validar' é a decisão do usuário "
        "e não deve ser confundida com a identificação automática."
    )

    display_cols = [
        "validar", "competencia", "data", "entrada", "saida",
        "intervalos", "horas", "horas_documento",
        "plantao_sugerido", "status", "modelo", "pdf", "pagina"
    ]
    display_cols = [c for c in display_cols if c in df.columns]

    edited = st.data_editor(
        df[display_cols],
        key="editor_registros",
        hide_index=True,
        use_container_width=True,
        disabled=[
            c for c in display_cols
            if c not in {"validar"}
        ],
        column_config={
            "validar": st.column_config.CheckboxColumn(
                "Validar",
                help="Decisão manual: considerar este registro como plantão.",
                default=False,
            ),
            "plantao_sugerido": st.column_config.CheckboxColumn(
                "Plantão sugerido",
                disabled=True,
            ),
            "data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
            "horas": st.column_config.TextColumn("Horas"),
            "horas_documento": st.column_config.TextColumn(
                "Horas no documento"
            ),
        },
    )

    # Reintegra a decisão editada ao dataset completo.
    if "validar" in edited:
        df.loc[edited.index, "validar"] = edited["validar"].astype(bool).values
        st.session_state.records = df

    st.markdown("### Critério da sugestão")
    st.code(
        f"Plantão sugerido = SIM quando horas trabalhadas ≥ {threshold:.1f} h",
        language="text",
    )

with tabs[1]:
    st.subheader("Inconsistências")
    if inc_df.empty:
        st.success("Nenhuma inconsistência foi registrada.")
    else:
        st.warning(f"{len(inc_df)} ocorrência(s) requerem decisão manual.")
        st.dataframe(
            inc_df[
                [c for c in [
                    "data", "problema", "texto_original", "pdf", "pagina",
                    "competencia"
                ] if c in inc_df.columns]
            ],
            use_container_width=True,
            hide_index=True,
        )

with tabs[2]:
    st.subheader("Resumo mensal")
    resumo = (
        df.groupby("competencia", dropna=False)
        .agg(
            dias_trabalhados=("data", "nunique"),
            horas_min=("horas_min", "sum"),
            plantões_validados=("validar", "sum"),
        )
        .reset_index()
        .sort_values("competencia")
    )
    resumo["horas_totais"] = resumo["horas_min"].apply(fmt_minutes)
    resumo = resumo[
        ["competencia", "dias_trabalhados", "horas_totais", "plantões_validados"]
    ]
    st.dataframe(resumo, use_container_width=True, hide_index=True)

with tabs[3]:
    st.subheader("Estatísticas")
    chart_base = (
        df.groupby("competencia", dropna=False)
        .agg(
            horas_min=("horas_min", "sum"),
            dias=("data", "nunique"),
            plantões=("validar", "sum"),
        )
        .reset_index()
        .sort_values("competencia")
    )

    col1, col2 = st.columns(2)
    with col1:
        fig = px.bar(
            chart_base, x="competencia", y="plantões",
            title="Plantões validados por mês",
            labels={"competencia": "Competência", "plantões": "Plantões"},
        )
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        chart_base["horas"] = chart_base["horas_min"] / 60
        fig = px.bar(
            chart_base, x="competencia", y="horas",
            title="Horas trabalhadas por mês",
            labels={"competencia": "Competência", "horas": "Horas"},
        )
        st.plotly_chart(fig, use_container_width=True)

    fig = px.bar(
        chart_base, x="competencia", y="dias",
        title="Dias trabalhados por mês",
        labels={"competencia": "Competência", "dias": "Dias"},
    )
    st.plotly_chart(fig, use_container_width=True)

with tabs[4]:
    st.subheader("Evidência do registro")
    options = []
    for idx, row in df.iterrows():
        options.append(
            f"{idx} • {row['data'].strftime('%d/%m/%Y')} • "
            f"{row['horas']} • pág. {row['pagina']} • {row['pdf']}"
        )

    selected = st.selectbox("Selecione um registro", options)
    selected_idx = int(selected.split(" • ", 1)[0])

    if st.button("🔎 Ver Evidência", type="primary", key="btn_evidencia"):
        st.session_state["evidencia_idx"] = selected_idx

    evidence_idx = st.session_state.get("evidencia_idx", selected_idx)
    row = df.loc[evidence_idx]

    st.markdown(
        f"**{row['data'].strftime('%d/%m/%Y')}** — "
        f"**{row['entrada']} → {row['saida']}** — "
        f"**{row['horas']}**"
    )
    st.write(
        f"**PDF:** {row['pdf']}  \n"
        f"**Página:** {row['pagina']}  \n"
        f"**Modelo:** {row['modelo']}  \n"
        f"**Sugestão:** {'SIM' if row['plantao_sugerido'] else 'NÃO'}  \n"
        f"**Decisão:** {'VALIDADO' if row['validar'] else 'NÃO VALIDADO'}"
    )

    st.markdown("#### Texto extraído")
    st.code(row["texto_original"], language="text")

    # Renderização da página original do PDF.
    try:
        pdf_bytes = st.session_state.get("pdf_bytes", {}).get(row["pdf"])
        if pdf_bytes:
            evidence_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            evidence_page = evidence_doc[int(row["pagina"]) - 1]
            pix = evidence_page.get_pixmap(
                matrix=fitz.Matrix(1.5, 1.5), alpha=False
            )
            st.image(
                pix.tobytes("png"),
                caption=f"Evidência visual — {row['pdf']} — página {row['pagina']}",
                use_container_width=True,
            )
            evidence_doc.close()
    except Exception as exc:
        st.warning(f"Não foi possível renderizar a página: {exc}")

with tabs[5]:
    st.subheader("Exportação")
    xlsx = build_excel(st.session_state.records, inc_df)
    st.download_button(
        "⬇️ Baixar Excel — Plantões + Resumo + Inconsistências + Auditoria",
        data=xlsx,
        file_name="apuracao_plantao_ufpe.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        type="primary",
    )

    st.markdown("### Estrutura do arquivo")
    st.markdown(
        "- **Plantões Validados:** somente os registros decididos pelo usuário.\n"
        "- **Resumo Mensal:** dias, horas e plantões por competência.\n"
        "- **Inconsistências:** marcações incompletas ou problemas detectados.\n"
        "- **Auditoria:** evidência textual, página, PDF, cálculo e decisão."
    )

st.divider()
st.caption(
    "Sistema de apoio à apuração. A identificação automática é sugestiva; "
    "a validação final permanece com o usuário."
)
