<img src="neuromapa-simbolo.svg" width="56" alt="NeuroMapa">

# NeuroMapa · Avaliar com evidência

Plataforma criada e administrada pelo **Neurotestes**.

Guia de testes psicológicos e neuropsicológicos por transtorno: o que observar, dados de aplicação, referências e montagem de bateria.

- **Por transtorno:** 38 condições, com o que observar em cada instrumento e a referência.
- **Monte sua bateria:** sugestão de instrumentos por queixa, idade, informante e possibilidade de manipular objetos.
- **Catálogo:** 115 testes e 24 inventários não padronizados (escalas de rastreio com validação brasileira), com ficha, comparação e exportação em CSV ou PDF.
- **Glossário e método:** termos de avaliação, critérios de escolha das fontes e níveis de evidência.

Site estático (HTML + JavaScript, sem build). Os dados ficam em `data.js`.

> Material de referência clínica. Confira a situação de cada teste em [satepsi.cfp.org.br](https://satepsi.cfp.org.br) e use sempre o manual da edição brasileira em uso. Nenhum resultado isolado fecha diagnóstico.

Revisado em 18 de novembro de 2026.

## Design

Visual baseado no design system Open Design: fontes Inter, Inter Tight, Playfair Display e JetBrains Mono (SIL OFL 1.1, ver `fonts/OFL-fontes.txt`). As figuras foram desenhadas em SVG para o projeto.

## Verificação automática do SATEPSI

Toda segunda-feira o fluxo `.github/workflows/satepsi.yml` abre o site do SATEPSI, lê as listas de testes favoráveis, desfavoráveis e não privativos e compara com o guia (`scripts/satepsi_check.py`). Se houver mudança, ele abre um pedido de alteração com o resumo; o site só muda depois da aprovação. A situação de cada instrumento aparece na ficha, com a data da conferência.

- Rodar agora: aba **Actions** → *Verificação semanal do SATEPSI* → **Run workflow**.
- Corrigir uma correspondência de nomes: edite `scripts/satepsi-map.json` (nome no NeuroMapa → nome exato no SATEPSI, ou `null`).
- Se a leitura falhar (por exemplo, mudança no layout do SATEPSI), o GitHub envia um e-mail e os arquivos de diagnóstico ficam no artefato `satepsi-debug` da execução.
