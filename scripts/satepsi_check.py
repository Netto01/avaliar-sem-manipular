#!/usr/bin/env python3
"""Consulta o SATEPSI, compara com o NeuroMapa e prepara um resumo para revisão.

Uso (no GitHub Actions ou localmente):
    python scripts/satepsi_check.py

Variáveis de ambiente opcionais:
    SATEPSI_URL   endereço inicial (padrão: https://satepsi.cfp.org.br/)
    MIN_FAVORAVEIS  número mínimo de testes favoráveis para aceitar a leitura (padrão: 60)

Saídas:
    satepsi.json        situação lida no SATEPSI e correspondência com o NeuroMapa
    debug/              HTML, capturas de tela e respostas JSON de cada página (para calibrar o leitor)
    debug/pr_body.md    resumo das mudanças para o pedido de alteração
    GITHUB_OUTPUT       changed=true|false
"""
import json, os, re, subprocess, sys, unicodedata, difflib, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEBUG = ROOT / "debug"
OUT = ROOT / "satepsi.json"
MAP = ROOT / "scripts" / "satepsi-map.json"
START = os.environ.get("SATEPSI_URL", "https://satepsi.cfp.org.br/")
MIN_FAV = int(os.environ.get("MIN_FAVORAVEIS", "60"))

# páginas públicas de cada lista (confirmadas no menu "Lista do SATEPSI")
PAGES = {
    "favoravel": "testesFavoraveis.cfm",
    "desfavoravel": "testesDesfavoraveis.cfm",
    "nao_privativo": "testesNaoPrivativos.cfm",
}
# palavras que indicam cada lista no site (reserva, se os endereços mudarem)
TABS = {
    "favoravel": ["favorave", "favoráve", "testes favor"],
    "desfavoravel": ["desfavor"],
    "nao_privativo": ["nao privativ", "não privativ"],
}
NEXT_WORDS = ["próxim", "proxim", "next", "»", "›", ">"]
STOP = {"de", "da", "do", "das", "dos", "e", "para", "a", "o", "em", "com", "teste", "escala", "inventario",
        "bateria", "versao", "brasileira", "so", "os", "subtestes", "verbais", "the", "of", "edicao"}


def norm(s):
    s = unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(w for w in s.split() if w not in STOP)


def acronyms(name):
    base = re.sub(r"\(.*?\)", " ", name)
    toks = re.findall(r"[A-Za-zÀ-ú0-9][A-Za-zÀ-ú0-9\-/½]*", base)
    out = set()
    for t in toks:
        for part in re.split(r"[/]", t):
            letters = re.sub(r"[^A-Za-z]", "", part)
            if len(letters) >= 2 and sum(c.isupper() for c in letters) >= max(2, len(letters) - 1):
                out.add(norm(part).replace(" ", ""))
    return {a for a in out if len(a) >= 2}


def load_neuromapa():
    js = "global.window={};require('./data.js');const M=window.META;console.log(JSON.stringify(window.INSTRUMENTOS.map(r=>[r[0],(M[r[0]]||[])[0]||''])))"
    res = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


# ---------------------------------------------------------------- leitura do site
def scrape():
    from playwright.sync_api import sync_playwright
    DEBUG.mkdir(exist_ok=True)
    records, pages_seen, json_hits = [], [], 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="pt-BR", user_agent="Mozilla/5.0 (NeuroMapa; verificacao semanal SATEPSI)")
        page = ctx.new_page()

        def on_response(resp):
            nonlocal json_hits
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    json_hits += 1
                    (DEBUG / f"resp_{json_hits:03d}.json").write_text(resp.text()[:2_000_000], encoding="utf-8")
            except Exception:
                pass
        page.on("response", on_response)

        def snapshot(tag):
            safe = re.sub(r"[^a-z0-9_]+", "_", tag.lower())[:60]
            (DEBUG / f"{safe}.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(DEBUG / f"{safe}.png"), full_page=True)

        def show_all():
            # listas com "mostrar N registros": escolhe a maior opção
            for sel in page.query_selector_all("select"):
                opts = [o.get_attribute("value") or o.inner_text() for o in sel.query_selector_all("option")]
                nums = [o for o in opts if re.fullmatch(r"-?\d+", str(o).strip())]
                if nums:
                    best = "-1" if "-1" in nums else max(nums, key=lambda v: int(v))
                    try:
                        sel.select_option(best); page.wait_for_timeout(1500)
                    except Exception:
                        pass

        def read_tables(status):
            got = 0
            for tbl in page.query_selector_all("table"):
                heads = [h.inner_text().strip() for h in tbl.query_selector_all("thead th")]
                for tr in tbl.query_selector_all("tbody tr"):
                    cells = [c.inner_text().strip() for c in tr.query_selector_all("td")]
                    if not any(cells):
                        continue
                    rec = {"status": status, "celulas": cells}
                    if heads and len(heads) == len(cells):
                        rec["campos"] = dict(zip(heads, cells))
                    rec["nome"] = pick_name(rec)
                    if rec["nome"]:
                        records.append(rec); got += 1
            return got

        def read_accordion(status):
            got = 0
            items = page.query_selector_all("#accordionTestes .accordion-item, #accordionTestes > div, .accordion-item")
            seen_items = set()
            for it in items:
                h = it.query_selector("a, button, h3, h4, h5, .card-header")
                name = re.sub(r"\s+", " ", (h.text_content() if h else "") or "").strip()
                if not name or name in seen_items:
                    continue
                seen_items.add(name)
                raw = re.sub(r"\s+", " ", it.text_content() or "").strip()
                det = raw[len(name):].strip() if raw.startswith(name) else raw
                campos = {}
                for m in re.finditer(r"([A-ZÀ-Úa-zà-ú][A-Za-zÀ-ú ()/º°.]{2,60}?):\s*(.*?)(?=\s[A-ZÀ-Ú][A-Za-zÀ-ú ()/º°.]{2,60}?:\s|$)", det):
                    k, v = m.group(1).strip(), m.group(2).strip()
                    if v and len(campos) < 30:
                        campos[k] = v[:400]
                rec = {"status": status, "nome": name, "celulas": [det[:1500]]}
                if campos:
                    rec["campos"] = campos
                records.append(rec); got += 1
            return got

        def submit_search(url):
            """Envia a busca vazia (o site informa que, sem filtros, traz todos os testes da lista)."""
            form = {"teste": "", "autor": "", "editora": "", "btsalvar": ""}
            if "Favoraveis" in url:
                form.update({"buscaTextual": "", "texto": ""})
            try:
                resp = page.request.post(url, form=form, timeout=90_000)
                html = resp.text()
            except Exception as e:
                print(f"[aviso] falha ao enviar a busca: {e}")
                return False
            if "accordion-item" not in html and "<table" not in html.lower():
                # tenta pelo próprio formulário da página, incluindo o botão no envio
                try:
                    page.evaluate("""() => { const f=[...document.forms].find(f => /testes/i.test(f.getAttribute('action')||''));
                        if (!f) return; const h=document.createElement('input'); h.type='hidden'; h.name='btsalvar'; h.value=''; f.appendChild(h); f.submit(); }""")
                    page.wait_for_load_state("networkidle", timeout=90_000)
                    page.wait_for_timeout(1500)
                    return True
                except Exception as e:
                    print(f"[aviso] busca pelo formulário falhou: {e}")
                    return False
            page.set_content(html, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            return True

        def read_all_pages(status):
            total = read_tables(status) + read_accordion(status)
            for _ in range(80):
                nxt = None
                cands = page.query_selector_all(".paginate_button.next, li.next a, .page-item.next a, a[rel=next], [class*=pag] a, [class*=pag] button")
                for a in cands:
                    t = (a.inner_text() or "").strip().lower()
                    cls = ((a.get_attribute("class") or "") + " " + (a.evaluate("e => e.parentElement ? e.parentElement.className : ''") or "")).lower()
                    if "disabled" in cls:
                        continue
                    if "next" in cls or (t and len(t) <= 10 and any(w in t for w in NEXT_WORDS)):
                        nxt = a; break
                if not nxt:
                    break
                before = page.content()
                try:
                    nxt.click(); page.wait_for_timeout(1200)
                except Exception:
                    break
                if page.content() == before:
                    break
                total += read_tables(status) + read_accordion(status)
            return total

        def close_modals():
            try:
                page.keyboard.press("Escape"); page.wait_for_timeout(300)
                for b in page.query_selector_all(".modal.show [data-dismiss=modal], .modal.show .close"):
                    if b.is_visible():
                        b.click(); page.wait_for_timeout(300)
            except Exception:
                pass

        page.goto(START, wait_until="networkidle", timeout=90_000)
        snapshot("inicio")
        base = START if START.endswith("/") else START + "/"
        done = set()
        for status, path in PAGES.items():
            try:
                resp = page.goto(base + path, wait_until="networkidle", timeout=90_000)
                if resp and resp.status >= 400:
                    raise RuntimeError(f"HTTP {resp.status}")
            except Exception as e:
                print(f"[aviso] {path} indisponível ({e}); vou procurar pelo menu")
                continue
            close_modals()
            if submit_search(base + path):
                close_modals()
            show_all()
            n = read_all_pages(status)
            snapshot(status)
            pages_seen.append({"status": status, "url": page.url, "linhas": n})
            print(f"{status}: {n} linhas em {page.url}")
            if n:
                done.add(status)
        page.goto(START, wait_until="networkidle", timeout=90_000)
        # reserva: descobre abas ou links de cada lista
        for status, words in TABS.items():
            if status in done:
                continue
            target = None
            for el in page.query_selector_all("a, button, [role=tab], li"):
                t = (el.inner_text() or "").strip().lower()
                if t and len(t) < 80 and any(w in t for w in words) and not (status == "favoravel" and "desfavor" in t):
                    target = el; break
            if not target:
                print(f"[aviso] não encontrei a aba de {status}")
                continue
            href = target.get_attribute("href")
            close_modals()
            try:
                if href and not href.startswith(("#", "javascript")):
                    page.goto(page.url if href.startswith("?") else href if href.startswith("http") else START.rstrip("/") + "/" + href.lstrip("/"), wait_until="networkidle", timeout=90_000)
                else:
                    target.click(); page.wait_for_load_state("networkidle"); page.wait_for_timeout(1500)
            except Exception as e:
                print(f"[aviso] falha ao abrir {status}: {e}")
                continue
            show_all()
            n = read_all_pages(status)
            snapshot(status)
            pages_seen.append({"status": status, "url": page.url, "linhas": n})
            print(f"{status}: {n} linhas em {page.url}")
            page.goto(START, wait_until="networkidle", timeout=90_000)
        browser.close()

    # remove repetidos
    seen, uniq = set(), []
    for r in records:
        k = (r["status"], norm(r["nome"]))
        if k not in seen:
            seen.add(k); uniq.append(r)
    return uniq, pages_seen


def pick_name(rec):
    campos = rec.get("campos") or {}
    for k, v in campos.items():
        nk = norm(k)
        if any(w in nk for w in ("nome", "instrumento", "titulo")) or k.strip().lower() == "teste":
            return v
    cells = [c for c in rec["celulas"] if c and not re.fullmatch(r"[\d/.\- ]+", c)]
    return max(cells[:3], key=len) if cells else ""


# ---------------------------------------------------------------- correspondência
def match(ours, theirs, overrides):
    def raw_tokens(name):
        toks = set()
        for t in re.findall(r"[A-Za-zÀ-ú0-9][A-Za-zÀ-ú0-9\-/]*", name):
            for part in [t] + t.split("/"):
                k = norm(part).replace(" ", "")
                if k:
                    toks.add(k)
        return toks
    idx = [(r, norm(r["nome"]), raw_tokens(r["nome"])) for r in theirs]
    result = {}
    for name, tipo in ours:
        key = "MINI" if name == "MINI-Kid" else name
        if key in overrides:
            want = overrides[key]
            if want is None:
                result[name] = {"tipo_neuromapa": tipo, "status": "nao_listado", "metodo": "manual"}
                continue
            hit = next((r for r, n, _ in idx if norm(r["nome"]) == norm(want)), None)
            result[name] = ({"tipo_neuromapa": tipo, "status": hit["status"], "nome_satepsi": hit["nome"], "metodo": "manual"}
                            if hit else {"tipo_neuromapa": tipo, "status": "nao_encontrado", "nome_satepsi": want, "metodo": "manual"})
            continue
        acr, nn = acronyms(name), norm(name)
        best, score, how = None, 0.0, ""
        for r, n, toks in idx:
            s = difflib.SequenceMatcher(None, nn, n).ratio()
            if acr and acr & toks:
                s = max(s, .9)
            words = nn.split()
            if words and any(len(w) >= 5 for w in words) and all(w in toks for w in words):
                s = max(s, .86)
            if s > score:
                best, score, how = r, s, ("sigla" if acr & toks else "texto")
        if best and score >= .82:
            result[name] = {"tipo_neuromapa": tipo, "status": best["status"], "nome_satepsi": best["nome"],
                            "metodo": how, "confianca": round(score, 2)}
        else:
            result[name] = {"tipo_neuromapa": tipo, "status": "nao_encontrado",
                            "sugestao": best["nome"] if best and score >= .6 else None, "confianca": round(score, 2)}
    return result


LABEL = {"favoravel": "Favorável", "desfavoravel": "Desfavorável", "nao_privativo": "Não privativo",
         "nao_encontrado": "Não encontrado", "nao_listado": "Fora do SATEPSI"}


def summarize(new, old):
    lines, changes = [], []
    oldm = (old or {}).get("instrumentos", {})
    for name, info in new["instrumentos"].items():
        prev = oldm.get(name, {}).get("status")
        if old and prev != info["status"]:
            changes.append(f"- **{name}**: {LABEL.get(prev, prev or '—')} → **{LABEL.get(info['status'])}**"
                           + (f" (SATEPSI: {info.get('nome_satepsi')})" if info.get("nome_satepsi") else ""))
    alerts = [f"- **{n}** está como privativo no guia, mas aparece como **desfavorável** no SATEPSI"
              for n, i in new["instrumentos"].items() if i["tipo_neuromapa"] == "P" and i["status"] == "desfavoravel"]
    review = [f"- {n} ↔ {i.get('nome_satepsi')} (confiança {i.get('confianca')})"
              for n, i in new["instrumentos"].items() if i.get("metodo") == "texto" and i.get("confianca", 1) < .9]
    missing = [f"- {n}" + (f" (talvez: {i['sugestao']})" if i.get("sugestao") else "")
               for n, i in new["instrumentos"].items() if i["tipo_neuromapa"] in ("P", "N") and i["status"] == "nao_encontrado"]
    old_names = {norm(t["nome"]) for t in (old or {}).get("satepsi", [])}
    novos = [f"- {t['nome']} ({LABEL[t['status']]})" for t in new["satepsi"] if old and norm(t["nome"]) not in old_names]

    c = new["contagens"]
    lines.append(f"## Verificação do SATEPSI · {new['verificado_em']}\n")
    lines.append(f"Lidos no site: **{c.get('favoravel', 0)}** favoráveis, **{c.get('desfavoravel', 0)}** desfavoráveis, "
                 f"**{c.get('nao_privativo', 0)}** não privativos.\n")
    for title, items in (("Mudanças de situação", changes), ("Alertas", alerts), ("Novos no SATEPSI", novos),
                         ("Correspondências para conferir", review), ("Instrumentos do guia não encontrados", missing)):
        if items:
            lines.append(f"### {title}\n" + "\n".join(items[:80]) + ("\n- …" if len(items) > 80 else "") + "\n")
    if not old:
        lines.append("Primeira leitura: confira as correspondências antes de aprovar. Para corrigir uma, "
                     "edite `scripts/satepsi-map.json` (nome no NeuroMapa → nome exato no SATEPSI, ou `null` se não estiver no SATEPSI).\n")
    lines.append("---\nGerado automaticamente pelo fluxo semanal `.github/workflows/satepsi.yml`. "
                 "Nada muda no site até este pedido ser aprovado.")
    return "\n".join(lines), bool(changes or novos or not old)


def main():
    theirs, pages = scrape()
    counts = {}
    for r in theirs:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("contagens:", counts)
    if counts.get("favoravel", 0) < MIN_FAV:
        print(f"[erro] só {counts.get('favoravel', 0)} favoráveis lidos (mínimo {MIN_FAV}). "
              "O site pode ter mudado; veja os arquivos em debug/.")
        sys.exit(1)
    overrides = json.loads(MAP.read_text(encoding="utf-8")) if MAP.exists() else {}
    overrides = {k: v for k, v in overrides.items() if not k.startswith("_")}
    ours = load_neuromapa()
    new = {
        "verificado_em": datetime.date.today().isoformat(),
        "fonte": START,
        "paginas": pages,
        "contagens": counts,
        "instrumentos": match(ours, theirs, overrides),
        "satepsi": [{"nome": r["nome"], "status": r["status"], "campos": r.get("campos") or r["celulas"]} for r in theirs],
    }
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else None
    body, changed = summarize(new, old)
    DEBUG.mkdir(exist_ok=True)
    (DEBUG / "pr_body.md").write_text(body, encoding="utf-8")
    if changed:
        OUT.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    print(body)
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")


if __name__ == "__main__":
    main()
