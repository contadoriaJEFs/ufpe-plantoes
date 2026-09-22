
import io
import re
import unicodedata
from datetime import datetime, date

import pandas as pd
import numpy as np
import fitz
import pytesseract
from PIL import Image
import streamlit as st
import plotly.express as px

st.set_page_config(page_title="UFPE • Apuração de Plantões", page_icon="🧾", layout="wide")

MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
MESES_NORM = {
    "".join(c for c in unicodedata.normalize("NFKD", k)
            if not unicodedata.combining(c)): v
    for k, v in MESES.items()
}
TIME = r"\d{1,2}:\d{2}"
DATE_FULL_RE = re.compile(r"(?<!\d)(\d{2}/\d{2}/\d{4})(?!\d)")
DATE_OLD_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})\b")
INTERVAL_RE = re.compile(rf"({TIME})\s*-\s*({TIME})")
PERIOD_RE = re.compile(
    r"Período:\s*(\d{1,2})/([A-Za-zÀ-ÿ]+)/(20\d{2})\s+a\s+"
    r"(\d{1,2})/([A-Za-zÀ-ÿ]+)/(20\d{2})", re.I
)

def norm(s):
    return "".join(c for c in unicodedata.normalize("NFKD", str(s or "").lower())
                   if not unicodedata.combining(c))

def hhmm_to_min(s):
    if s is None or pd.isna(s): return None
    m = re.search(r"(\d{1,3}):(\d{2})", str(s))
    return int(m.group(1))*60 + int(m.group(2)) if m else None

def min_to_hhmm(m):
    if m is None or pd.isna(m): return ""
    m = int(m)
    sign = "-" if m < 0 else ""
    m = abs(m)
    return f"{sign}{m//60:02d}:{m%60:02d}"

def interval_minutes(a,b):
    a,b = hhmm_to_min(a), hhmm_to_min(b)
    if a is None or b is None: return None
    if b < a: b += 1440
    return b-a

def classify_turno(entry):
    m = hhmm_to_min(entry)
    if m is None: return ""
    if m >= 18*60 or m < 6*60: return "🌙 Noturno"
    return "☀️ Diurno"

def status_icon(status):
    return {"completo":"🟢", "horario":"🟡", "falta":"🔴"}.get(status, "⚪")

def parse_period(text):
    m = PERIOD_RE.search(text or "")
    if not m: return None
    month = MESES_NORM.get(norm(m.group(2)))
    return (int(m.group(3)), month) if month else None

def extract_metadata(text):
    out = {"nome":"","matricula":"","cpf":""}
    m = re.search(r"Servidor:\s*(.+?)\s*\((\d+)\)", text or "", re.I)
    if m:
        out["nome"], out["matricula"] = m.group(1).strip(), m.group(2)
    m = re.search(r"CPF:\s*([\d.\-]+)", text or "", re.I)
    if m: out["cpf"] = m.group(1)
    return out

def detect_model(text):
    n = norm(text)
    if "espelho de ponto" in n or "sigrh" in n or "ponto diario associado" in n:
        return "SIGRH"
    return "CARTAO"

def extract_text(page, use_ocr=True):
    text = page.get_text("text") or ""
    if len(re.sub(r"\s+", "", text)) >= 100:
        return text, "PDF"
    if use_ocr:
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(2,2), alpha=False)
            img = Image.frombytes("RGB",[pix.width,pix.height],pix.samples)
            ocr = pytesseract.image_to_string(img,lang="por+eng")
            if len(re.sub(r"\s+","",ocr)) > len(re.sub(r"\s+","",text)):
                return ocr, "OCR"
        except Exception:
            pass
    return text, "PDF"

def pdf_pages(data, filename, use_ocr=True):
    doc = fitz.open(stream=data, filetype="pdf")
    out=[]
    for i in range(len(doc)):
        text,source=extract_text(doc[i],use_ocr)
        out.append({"pdf":filename,"pagina":i+1,"text":text,"fonte_texto":source})
    return out

def make_inc(day,status,problem,text,page):
    return {
        "data":day,"competencia":f"{day.month:02d}/{day.year}",
        "status":status,"status_icon":status_icon(status),
        "problema":problem,"texto_original":text,
        "pdf":page["pdf"],"pagina":page["pagina"],
        "tratamento":"Pendente","observacao":""
    }

def make_record(day,intervals,total,declared,model,page,original,status,occurrence=""):
    comp=f"{day.month:02d}/{day.year}"
    entry=intervals[0]["entrada"] if intervals else ""
    return {
        "data":day,"competencia":comp,
        "entrada":entry,
        "saida":intervals[-1]["saida"] if intervals else "",
        "intervalos":" | ".join(f'{x["entrada"]} - {x["saida"]}' for x in intervals),
        "horas_min":total,"horas":min_to_hhmm(total),
        "horas_documento":min_to_hhmm(declared) if declared is not None else "",
        "modelo":model,"turno":classify_turno(entry),
        "turno_analise":classify_turno(entry),
        "pdf":page["pdf"],"pagina":page["pagina"],
        "texto_original":original,"status":status,
        "status_icon":status_icon(status),"problema":"",
        "ocorrencia":occurrence,"plantao_sugerido":False,
        "validar":False,"decisao_manual":False,"id_evento":""
    }

def parse_cartao(page):
    text=page["text"]; period=parse_period(text)
    if not period: return [],[]
    year,month=period
    lines=[re.sub(r"[ \t]+"," ",x).strip() for x in text.splitlines() if x.strip()]
    records=[]; inc=[]; current=None; buf=[]

    def flush(day, lines_):
        if day is None: return
        block=" ".join(lines_)
        block2=re.sub(r"\[[^\]]*(?:às|as)\s*\d{1,2}:\d{2}[^\]]*\]","",block,flags=re.I)
        block2=re.sub(r"PlantãoNoturno.*?(?=\d{1,2}:\d{2}\s+\d{1,2}:\d{2})","",block2,flags=re.I)
        pairs=INTERVAL_RE.findall(block2)
        times=re.findall(TIME,block2)
        explicit=re.search(r"Horas\s+Trabalhadas\s*:?\s*(\d{1,3}:\d{2})",block,flags=re.I)

        if pairs:
            intervals=[{"entrada":a,"saida":b} for a,b in pairs]
        elif len(times)>=2:
            intervals=[{"entrada":times[0],"saida":times[1]}]
        elif len(times)==1:
            inc.append(make_inc(day,"falta","Batida incompleta: apenas um horário encontrado.",block,page))
            return
        else:
            return

        total=sum(interval_minutes(x["entrada"],x["saida"]) or 0 for x in intervals)
        declared=hhmm_to_min(explicit.group(1)) if explicit else None
        records.append(make_record(
            day,intervals,total,declared,"CARTAO",page,block,
            "horario" if len(intervals)>1 else "completo"
        ))

    for line in lines:
        if norm(line).startswith("emitido em"): continue
        m=DATE_OLD_RE.match(line)
        if m:
            try: day=date(year,int(m.group(2)),int(m.group(1)))
            except Exception: continue
            flush(current,buf); current=day; buf=[line]
        elif current:
            buf.append(line)
    flush(current,buf)
    return records,inc

def sigrh_data_page(text):
    n=norm(text)
    return ("ponto diario associado" in n and "horario registrado" in n
            and bool(DATE_FULL_RE.search(text)))

def parse_sigrh(page):
    text=page["text"]
    if not sigrh_data_page(text): return [],[]
    lines=[re.sub(r"[ \t]+"," ",x).strip() for x in text.splitlines() if x.strip()]
    starts=[]
    for i,line in enumerate(lines):
        m=DATE_FULL_RE.search(line)
        if not m: continue
        if norm(line).startswith("emitido em"): continue
        if "pje" in norm(line) or "numero do documento" in norm(line): continue
        try:
            d=datetime.strptime(m.group(1),"%d/%m/%Y").date()
            starts.append((i,d))
        except Exception: pass

    records=[]; inc=[]
    for k,(i,d) in enumerate(starts):
        j=starts[k+1][0] if k+1<len(starts) else len(lines)
        block_lines=lines[i:j]; block=" ".join(block_lines)
        date_pos=block.find(d.strftime("%d/%m/%Y"))
        after=block[date_pos+10:] if date_pos>=0 else block

        intervals=INTERVAL_RE.findall(after)
        occurrence_tokens=[]
        for tok in ["ATIVIDADE REMOTA","FALHA NO SISTEMA","FÉRIAS","FERIAS",
                    "FERIADO","PONTO FACULTATIVO","ABONO","AFASTAMENTO",
                    "SUSPENSÃO DAS ATIVIDADES","TRABALHO REMOTO"]:
            if norm(tok) in norm(block): occurrence_tokens.append(tok)

        if intervals:
            total=sum(interval_minutes(a,b) or 0 for a,b in intervals)
            records.append(make_record(
                d,[{"entrada":a,"saida":b} for a,b in intervals],
                total,None,"SIGRH",page,block,
                "horario" if len(intervals)>1 else "completo",
                " | ".join(occurrence_tokens)
            ))
        else:
            # Apenas uma ponta explícita no campo de horário registrado.
            partial=re.search(rf"({TIME})\s*-\s*(?:---)?(?:\s|$)",after)
            if partial:
                inc.append(make_inc(
                    d,"falta",
                    "Registro de entrada/saída incompleto no campo Horário Registrado.",
                    block,page
                ))
    return records,inc

def event_id(comp, idx):
    return f"UFPE-{comp.replace('/','-')}-{idx:03d}"

def parse_files(uploaded,use_ocr=True):
    records=[]; inc=[]; pages=[]; metadata={}; periodos=[]
    for up in uploaded:
        data=up.getvalue()
        ps=pdf_pages(data,up.name,use_ocr); pages.extend(ps)
        for p in ps:
            md=extract_metadata(p["text"])
            if md["nome"] or md["matricula"]:
                metadata[up.name]=md
                break
        for p in ps:
            period=parse_period(p["text"])
            if period:
                year,month=period
                comp=f"{month:02d}/{year}"
                periodos.append({
                    "competencia":comp,
                    "pdf":p["pdf"],
                    "pagina":p["pagina"],
                    "modelo":detect_model(p["text"]),
                    "texto":p["text"]
                })
            if detect_model(p["text"])=="SIGRH":
                r,x=parse_sigrh(p)
            else:
                r,x=parse_cartao(p)
            records.extend(r); inc.extend(x)

    df=pd.DataFrame(records)
    if not df.empty:
        df=df.drop_duplicates(
            subset=["pdf","pagina","data","intervalos"],keep="first"
        ).reset_index(drop=True)
        df["id_evento"]=[event_id(c,i+1) for i,c in enumerate(df["competencia"])]
        if "turno_analise" not in df:
            df["turno_analise"]=df["turno"]

    idf=pd.DataFrame(inc)
    if not idf.empty:
        idf=idf.drop_duplicates(
            subset=["pdf","pagina","data","problema","texto_original"]
        ).reset_index(drop=True)

    # Um documento pode existir sem qualquer batida (como os cartões de
    # 2020/2021 sem marcações). Não perder a competência por isso.
    periodos_df=pd.DataFrame(periodos).drop_duplicates(
        subset=["competencia","pdf","pagina"]
    ) if periodos else pd.DataFrame(
        columns=["competencia","pdf","pagina","modelo","texto"]
    )
    return df,idf,pages,metadata,periodos_df

def page_image(pdf,pageno):
    try:
        pdf_bytes=st.session_state.pdf_bytes[pdf]
        doc=fitz.open(stream=pdf_bytes,filetype="pdf")
        pg=doc[pageno-1]
        pix=pg.get_pixmap(matrix=fitz.Matrix(1.6,1.6),alpha=False)
        return Image.frombytes("RGB",[pix.width,pix.height],pix.samples)
    except Exception:
        return None

def evidence_panel(row,title="Evidência do registro"):
    st.markdown(f"### {title}")
    c1,c2,c3=st.columns(3)
    c1.metric("Data",row["data"].strftime("%d/%m/%Y"))
    c2.metric("Horas",row.get("horas",""))
    c3.metric("Turno analisado",row.get("turno_analise",row.get("turno","")))
    st.write(f"**PDF:** {row['pdf']}  •  **Página:** {row['pagina']}  •  **ID:** `{row.get('id_evento','')}`")
    if row.get("intervalos"): st.markdown(f"**Horário registrado:** `{row['intervalos']}`")
    if row.get("horas_documento"): st.markdown(f"**Horas informadas no documento:** `{row['horas_documento']}`")
    if row.get("ocorrencia"): st.markdown(f"**Ocorrência:** {row['ocorrencia']}")
    st.markdown("#### Texto extraído da evidência")
    st.code(row["texto_original"],language="text")
    img=page_image(row["pdf"],int(row["pagina"]))
    if img is not None:
        st.markdown("#### Página original do PDF")
        st.image(img,use_container_width=True)

def excel_bytes(df,inc):
    out=io.BytesIO()
    with pd.ExcelWriter(out,engine="openpyxl") as writer:
        valid=df[df["validar"]==True].copy()
        if not valid.empty:
            cols=["id_evento","competencia","data","turno_analise","entrada","saida","intervalos",
                  "horas","horas_documento","pdf","pagina","texto_original"]
            valid[[c for c in cols if c in valid]].to_excel(writer,sheet_name="Plantões Validados",index=False)
        else:
            pd.DataFrame({"Resultado":["Nenhum plantão validado"]}).to_excel(writer,sheet_name="Plantões Validados",index=False)

        rows=[]
        comps=sorted(df["competencia"].unique(),key=lambda x:(int(x[3:]),int(x[:2]))) if not df.empty else []
        for comp in comps:
            m=df[df["competencia"]==comp]; v=m[m["validar"]==True]
            rows.append({
                "Mês":comp,"Dias trabalhados":m["data"].nunique(),
                "Horas trabalhadas":min_to_hhmm(int(m["horas_min"].sum())),
                "☀️ Plantões diurnos":int((v["turno_analise"]=="☀️ Diurno").sum()),
                "🌙 Plantões noturnos":int((v["turno_analise"]=="🌙 Noturno").sum()),
                "Total de plantões":len(v),
                "Inconsistências":len(inc[inc["competencia"]==comp]) if not inc.empty else 0
            })
        pd.DataFrame(rows).to_excel(writer,sheet_name="Resumo Mensal",index=False)

        if not inc.empty: inc.to_excel(writer,sheet_name="Inconsistências",index=False)
        else: pd.DataFrame({"Resultado":["Nenhuma inconsistência"]}).to_excel(writer,sheet_name="Inconsistências",index=False)

        audit=["id_evento","competencia","data","turno_analise","entrada","saida","intervalos","horas",
               "horas_documento","modelo","pdf","pagina","status_icon","status","problema",
               "validar","decisao_manual","texto_original","ocorrencia"]
        df[[c for c in audit if c in df]].to_excel(writer,sheet_name="Auditoria",index=False)
    return out.getvalue()

def clear_analysis():
    for key in ["records","incons","pages","metadata","periodos","pdf_bytes","selected_comp","threshold_last"]:
        st.session_state.pop(key,None)
    st.session_state.clear_nonce=st.session_state.get("clear_nonce",0)+1

# ---------------- INTERFACE ----------------

st.title("UFPE • Apuração de Plantões")
st.caption("Apuração auditável de folhas de ponto — sugestão automática e validação humana.")

with st.sidebar:
    st.header("Configuração")
    threshold=st.number_input("Horas mínimas para sugerir plantão",
                              min_value=0.5,max_value=48.0,
                              value=float(st.session_state.get("threshold",12.0)),
                              step=0.5,key="threshold")
    st.caption(f"Critério: ≥ {threshold:.2f} h")
    use_ocr=st.checkbox("OCR como fallback",value=True)
    st.divider()
    if st.button("🧹 Limpar análise",use_container_width=True):
        clear_analysis(); st.rerun()

uploads=st.file_uploader("Envie um ou mais PDFs de folha de ponto",
                         type=["pdf"],accept_multiple_files=True,
                         key=f"uploads_{st.session_state.get('clear_nonce',0)}")

if uploads and "records" not in st.session_state:
    if st.button("🔎 Processar PDFs",type="primary"):
        with st.spinner("Lendo Cartão-Ponto e SIGRH..."):
            df,inc,pages,metadata,periodos=parse_files(uploads,use_ocr)
            st.session_state.records=df; st.session_state.incons=inc
            st.session_state.pages=pages; st.session_state.metadata=metadata; st.session_state.periodos=periodos
            st.session_state.pdf_bytes={u.name:u.getvalue() for u in uploads}
            st.session_state.threshold_last=threshold
        st.success(f"{len(df)} eventos formados e {len(inc)} ocorrências para análise.")
        st.rerun()

if "records" not in st.session_state:
    st.info("Envie os PDFs e clique em **Processar PDFs**.")
    st.stop()

df=st.session_state.records.copy()
inc=st.session_state.incons.copy()
if not df.empty and "turno_analise" not in df.columns:
    df["turno_analise"]=df["turno"]

if st.session_state.get("threshold_last") != threshold:
    df["plantao_sugerido"]=df["horas_min"] >= threshold*60
    if "decisao_manual" not in df: df["decisao_manual"]=False
    # Registro verde/completo entra validado automaticamente.
    # Registros com inconsistência permanecem para decisão manual.
    auto_ok = df["status"].eq("completo") & ~df["decisao_manual"]
    df.loc[auto_ok,"validar"]=True
    st.session_state.records=df; st.session_state.threshold_last=threshold

if "plantao_sugerido" not in df:
    df["plantao_sugerido"]=df["horas_min"] >= threshold*60
if "decisao_manual" not in df:
    df["decisao_manual"]=False
if "validar" not in df:
    df["validar"]=df["status"].eq("completo")
st.session_state.records=df

record_comps=df["competencia"].dropna().unique().tolist() if not df.empty else []
period_comps=st.session_state.get("periodos",pd.DataFrame())
period_comps=period_comps["competencia"].dropna().unique().tolist() if not period_comps.empty else []
comps=sorted(
    set(record_comps) | set(period_comps),
    key=lambda x:(int(x[3:]),int(x[:2]))
)
if not comps:
    st.warning("Nenhuma competência identificada.")
    st.stop()

current=st.session_state.get("selected_comp",comps[0])
if current not in comps: current=comps[0]
st.session_state.selected_comp=current

st.subheader("Competência em análise")
cprev,csel,cnext=st.columns([1,4,1]); idx=comps.index(current)
with cprev:
    if st.button("◀ Anterior",disabled=idx==0):
        st.session_state.selected_comp=comps[idx-1]; st.rerun()
with csel:
    selected=st.selectbox("Mês",comps,index=idx,label_visibility="collapsed",key="competencia_selector")
    if selected!=current:
        st.session_state.selected_comp=selected; st.rerun()
with cnext:
    if st.button("Próximo ▶",disabled=idx==len(comps)-1):
        st.session_state.selected_comp=comps[idx+1]; st.rerun()

current=st.session_state.selected_comp
month_df=df[df["competencia"]==current].copy() if not df.empty else pd.DataFrame()
month_inc=inc[inc["competencia"]==current].copy() if not inc.empty else pd.DataFrame()
period_df=st.session_state.get("periodos",pd.DataFrame())
month_periods=period_df[period_df["competencia"]==current].copy() if not period_df.empty else pd.DataFrame()
valid=month_df[month_df["validar"]==True] if not month_df.empty else pd.DataFrame()

k1,k2,k3,k4,k5=st.columns(5)
k1.metric("Horas trabalhadas",min_to_hhmm(int(month_df["horas_min"].sum())) if not month_df.empty else "00:00")
k2.metric("Dias trabalhados",int(month_df["data"].nunique()) if not month_df.empty else 0)
k3.metric("Plantões validados",len(valid))
k4.metric("☀️ Diurnos",int((valid["turno_analise"]=="☀️ Diurno").sum()) if not valid.empty else 0)
k5.metric("🌙 Noturnos",int((valid["turno_analise"]=="🌙 Noturno").sum()) if not valid.empty else 0)


tabs=st.tabs([
    "📋 Auditoria",
    "⚠️ Inconsistências",
    "📊 Resumo mensal",
    "📈 Estatísticas",
    "👁️ Evidência",
    "📤 Exportação"
])

with tabs[0]:
    st.subheader("Auditoria dos registros")
    st.caption(
        "🟢 registros completos entram validados automaticamente. "
        "🟡/🔴 registros com inconsistência exigem análise. "
        "A coluna 'Validar' pode ser alterada manualmente."
    )

    # A competência continua selecionável, mas a apresentação volta ao modelo
    # anterior: uma única tabela principal de auditoria.
    st.markdown(f"### Competência: {current}")
    month_df=df[df["competencia"]==current].copy()

    if month_df.empty:
        st.info(
            "Esta competência foi localizada no(s) PDF(s), mas não foram "
            "encontradas batidas registradas para formar eventos. "
            "Isso não deve fazer a competência desaparecer da análise."
        )
        if not month_periods.empty:
            for _,pr in month_periods.iterrows():
                st.write(
                    f"**Ficha localizada:** {pr['pdf']} • página {int(pr['pagina'])} "
                    f"• formato {pr['modelo']}"
                )
    else:
        view=month_df.copy()
        view["status_visual"]=view["status"].map(status_icon)
        view["intervalos"]=view["intervalos"].replace("", "—")
        view["turno_analise"]=view["turno_analise"].replace("", "—")
        view["plantao_sugerido_texto"]=np.where(
            view["plantao_sugerido"], "SIM", "NÃO"
        )

        # Ordem inspirada na auditoria original, mantendo as novas colunas.
        view_cols=[
            "status_visual","validar","competencia","data","entrada","saida",
            "intervalos","horas","horas_documento","turno_analise",
            "plantao_sugerido_texto"
        ]

        edited=st.data_editor(
            view[view_cols],
            key=f"editor_auditoria_{current.replace('/','_')}",
            hide_index=True,
            use_container_width=True,
            disabled=[c for c in view_cols if c not in ("validar","turno_analise")],
            column_config={
                "validar":st.column_config.CheckboxColumn("✓ Validar"),
                "competencia":st.column_config.TextColumn("Competência",width="small"),
                "data":st.column_config.DateColumn("Data",format="DD/MM/YYYY"),
                "entrada":st.column_config.TextColumn("Entrada"),
                "saida":st.column_config.TextColumn("Saída"),
                "intervalos":st.column_config.TextColumn("Intervalos"),
                "horas":st.column_config.TextColumn("Horas"),
                "horas_documento":st.column_config.TextColumn("Horas no documento"),
                "turno_analise":st.column_config.SelectboxColumn("Turno", options=["☀️ Diurno","🌙 Noturno"], width="small"),
                "plantao_sugerido_texto":st.column_config.TextColumn("Plantão sugerido",width="small"),
                "status_visual":st.column_config.TextColumn("Status",width="small"),
            }
        )

        # Persistência da decisão manual.
        changed=False
        for ridx,val in edited["validar"].items():
            new_val=bool(val)
            old_val=bool(df.loc[ridx,"validar"])
            if new_val != old_val:
                changed=True
                df.loc[ridx,"validar"]=new_val
                # Qualquer alteração pelo usuário passa a ser decisão manual.
                df.loc[ridx,"decisao_manual"]=True

        if "turno_analise" in edited:
            for ridx,val in edited["turno_analise"].items():
                if val in ["☀️ Diurno","🌙 Noturno"] and val != df.loc[ridx,"turno_analise"]:
                    changed=True
                    df.loc[ridx,"turno_analise"]=val

        if changed:
            st.session_state.records=df
            # Recarrega a competência para que os indicadores abaixo reflitam
            # imediatamente a classificação analisada.
            month_df=df[df["competencia"]==current].copy()

    # Resumo rápido da competência, sem tirar a atenção da tabela.
    valid=month_df[month_df["validar"]==True] if not month_df.empty else pd.DataFrame()
    q1,q2,q3,q4,q5=st.columns(5)
    q1.metric("Registros",len(month_df))
    q2.metric("Dias",int(month_df["data"].nunique()))
    q3.metric("Plantões validados",len(valid))
    q4.metric("☀️ Diurnos",int((valid["turno_analise"]=="☀️ Diurno").sum()))
    q5.metric("🌙 Noturnos",int((valid["turno_analise"]=="🌙 Noturno").sum()))

    st.markdown("### Ver evidência do registro")
    if not month_df.empty:
        opts=[
            f"{i} • {r['data'].strftime('%d/%m/%Y')} • {r['horas']} • {r['id_evento']}"
            for i,r in month_df.iterrows()
        ]
        sel=st.selectbox(
            "Selecione um registro",
            opts,
            key=f"aud_evidence_{current}"
        )
        ridx=int(sel.split(" • ",1)[0])
        evidence_panel(df.loc[ridx])

with tabs[1]:
    st.subheader("Inconsistências")
    st.caption(
        "As inconsistências são tratadas separadamente da auditoria principal, "
        "mas permanecem vinculadas à competência, página e evidência documental."
    )

    # Mesma competência selecionada na Auditoria.
    st.markdown(f"### Competência: {current}")

    month_inc=inc[inc["competencia"]==current].copy() if not inc.empty else pd.DataFrame()

    if month_inc.empty:
        st.success("Nenhuma inconsistência registrada nesta competência.")
    else:
        n_falta=int((month_inc["status"]=="falta").sum())
        n_horario=int((month_inc["status"]=="horario").sum())

        a,b,c=st.columns(3)
        a.metric("Ocorrências",len(month_inc))
        b.metric("🔴 Falta de entrada/saída",n_falta)
        c.metric("🟡 Horário para conferir",n_horario)

        inc_view=month_inc.copy()
        inc_view["status_visual"]=inc_view["status"].map(status_icon)
        inc_view["data"]=pd.to_datetime(inc_view["data"])
        inc_view=inc_view[
            ["status_visual","data","problema","texto_original","pdf","pagina","competencia"]
        ]

        st.dataframe(
            inc_view,
            hide_index=True,
            use_container_width=True,
            column_config={
                "status_visual":st.column_config.TextColumn("Status",width="small"),
                "data":st.column_config.DateColumn("Data",format="DD/MM/YYYY"),
                "problema":st.column_config.TextColumn("Problema"),
                "texto_original":st.column_config.TextColumn("Texto original"),
                "pdf":st.column_config.TextColumn("PDF"),
                "pagina":st.column_config.NumberColumn("Página"),
                "competencia":st.column_config.TextColumn("Competência"),
            }
        )

        st.markdown("### Tratar inconsistência")
        options=[
            f"{i} • {r['data'].strftime('%d/%m/%Y')} • {r['problema']} • pág. {r['pagina']}"
            for i,r in month_inc.iterrows()
        ]
        selected_inc=st.selectbox(
            "Selecione a ocorrência",
            options,
            key=f"inc_select_old_model_{current}"
        )
        inc_idx=int(selected_inc.split(" • ",1)[0])
        ir=month_inc.loc[inc_idx]

        st.markdown(
            f"**{status_icon(ir['status'])} {ir['data'].strftime('%d/%m/%Y')}** — "
            f"{ir['problema']}"
        )

        evidence_panel({
            "data":ir["data"],
            "horas":"",
            "turno":"",
            "pdf":ir["pdf"],
            "pagina":ir["pagina"],
            "id_evento":f"INC-{current.replace('/','-')}-{inc_idx+1:03d}",
            "intervalos":"",
            "horas_documento":"",
            "ocorrencia":"",
            "texto_original":ir["texto_original"]
        },"Evidência da inconsistência")

        st.markdown("#### Decisão")
        action=st.selectbox(
            "Tratamento",
            [
                "Pendente",
                "Desconsiderar",
                "Considerar registro de entrada",
                "Considerar registro de saída",
                "Vincular a outro evento",
                "Manter como inconsistência"
            ],
            index=[
                "Pendente",
                "Desconsiderar",
                "Considerar registro de entrada",
                "Considerar registro de saída",
                "Vincular a outro evento",
                "Manter como inconsistência"
            ].index(str(ir.get("tratamento","Pendente")))
            if str(ir.get("tratamento","Pendente")) in [
                "Pendente",
                "Desconsiderar",
                "Considerar registro de entrada",
                "Considerar registro de saída",
                "Vincular a outro evento",
                "Manter como inconsistência"
            ] else 0,
            key=f"inc_action_{current}_{inc_idx}"
        )
        obs=st.text_area(
            "Observação / justificativa",
            value=str(ir.get("observacao","")),
            key=f"inc_obs_{current}_{inc_idx}"
        )

        if st.button("💾 Registrar tratamento",key=f"inc_save_{current}_{inc_idx}"):
            inc.loc[inc.index==ir.name,"tratamento"]=action
            inc.loc[inc.index==ir.name,"observacao"]=obs
            st.session_state.incons=inc
            st.success("Tratamento registrado.")
            st.rerun()

with tabs[2]:
    st.subheader("Resumo mensal")
    rows=[]
    for comp in comps:
        m=df[df["competencia"]==comp]
        v=m[m["validar"]==True]
        rows.append({
            "Mês":comp,
            "Dias trabalhados":m["data"].nunique(),
            "Horas trabalhadas":min_to_hhmm(int(m["horas_min"].sum())),
            "☀️ Plantões diurnos":int((v["turno_analise"]=="☀️ Diurno").sum()),
            "🌙 Plantões noturnos":int((v["turno_analise"]=="🌙 Noturno").sum()),
            "Total de plantões":len(v),
            "Inconsistências":len(inc[inc["competencia"]==comp]) if not inc.empty else 0
        })
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)

with tabs[3]:
    st.subheader("Estatísticas")
    rows=[]
    for comp in comps:
        m=df[df["competencia"]==comp]
        v=m[m["validar"]==True]
        rows.append({
            "competencia":comp,
            "horas":m["horas_min"].sum()/60,
            "dias":m["data"].nunique(),
            "plantões":len(v)
        })
    chart=pd.DataFrame(rows)
    c1,c2=st.columns(2)
    with c1:
        st.plotly_chart(
            px.bar(chart,x="competencia",y="plantões",
                   title="Plantões validados por mês"),
            use_container_width=True
        )
    with c2:
        st.plotly_chart(
            px.bar(chart,x="competencia",y="horas",
                   title="Horas trabalhadas por mês"),
            use_container_width=True
        )
    st.plotly_chart(
        px.bar(chart,x="competencia",y="dias",
               title="Dias trabalhados por mês"),
        use_container_width=True
    )

with tabs[4]:
    st.subheader("Evidência documental")
    st.caption(
        "A evidência pode ser consultada independentemente da auditoria ou "
        "do tratamento de inconsistências."
    )

    ev_comp=st.selectbox(
        "Competência",
        comps,
        index=comps.index(current),
        key="evidence_comp_global"
    )
    ev_df=df[df["competencia"]==ev_comp].copy()

    if ev_df.empty:
        st.info("Nenhum registro formado nesta competência.")
    else:
        ev_opts=[
            f"{i} • {r['data'].strftime('%d/%m/%Y')} • {r['horas']} • {r['id_evento']}"
            for i,r in ev_df.iterrows()
        ]
        ev_sel=st.selectbox(
            "Registro",
            ev_opts,
            key=f"global_evidence_{ev_comp}"
        )
        ev_idx=int(ev_sel.split(" • ",1)[0])
        evidence_panel(df.loc[ev_idx])

with tabs[5]:
    st.subheader("Exportação")
    st.download_button(
        "⬇️ Baixar Excel da apuração",
        data=excel_bytes(df,inc),
        file_name="apuracao_plantao_ufpe.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )
    st.markdown("""
O Excel contém:

1. **Plantões Validados**
2. **Resumo Mensal**
3. **Inconsistências**
4. **Auditoria**

O `id_evento` permite relacionar cada registro à planilha externa de apoio.
""")

st.divider()
st.caption("A identificação automática é sugestiva. A decisão final sobre o plantão permanece com o usuário.")
